"""The voice medium: what happens to an event that arrives during a call."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.agent.events import ObjectiveMoved
from app.agent.objectives import ONBOARDING
from app.events.payload import Channel, Origin
from app.google.events import GmailEvent, GmailPhase, InboxItem
from app.jev import Jev
from app.jobs.events import JobAsked
from app.main import App
from app.pipeline import Pipeline
from app.text.events import Typing, UserMessage
from app.users.user import Medium
from app.voice import responder as responder_module
from app.voice.responder import UNSAID, LiveCall, VoiceResponder, call_note
from tests.conftest import PHONE, CapturingMessenger, ev


class FakeSession:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bool | None]] = []

    async def send(self, content: str, /, *, respond: bool | None = None) -> None:
        self.sent.append((content, respond))


async def on_a_call(app: App, voice: VoiceResponder) -> tuple[Pipeline, LiveCall, FakeSession]:
    pipeline = app.pipeline
    pipeline.responders[Medium.VOICE] = voice
    from app.voice.call_state import CallEvent, CallTransition

    for t in (CallTransition.CONNECTING, CallTransition.CONNECTED):
        await pipeline.submit(PHONE, Origin.CALL, Channel.SYSTEM, CallEvent(transition=t))
    session = FakeSession()
    call = LiveCall(session)
    voice.calls[PHONE] = call
    return pipeline, call, session


def jev_answering(choice: str) -> Jev:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["questions"]["q"]["type"] == "choice"
        return httpx.Response(
            200, json={"answers": {"q": {"choice": choice, "probabilities": {choice: 0.7}}}}
        )

    return Jev(api_key="k", client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))


async def last_decision(pipeline: Pipeline) -> dict[str, Any]:
    events = [e for e in await pipeline.history(PHONE) if e.kind == "decision"]
    return events[-1].payload.model_dump()


async def test_when_the_agent_is_quiet_the_note_goes_straight_in(app: App) -> None:
    pipeline, _, session = await on_a_call(app, VoiceResponder())
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, UserMessage(text="it's sam@x.com"))
    assert len(session.sent) == 1 and "texted" in session.sent[0][0]
    assert (await last_decision(pipeline))["verb"] == "send"


async def test_a_text_while_speaking_interrupts(app: App) -> None:
    pipeline, call, session = await on_a_call(app, VoiceResponder())
    call.speaking = True
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, UserMessage(text="wait"))
    assert session.sent[-1][1] is True
    assert (await last_decision(pipeline))["verb"] == "interrupt"


async def test_without_jev_typing_is_absorbed_and_gmail_deferred(app: App) -> None:
    pipeline, call, session = await on_a_call(app, VoiceResponder())
    call.speaking = True
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, Typing(active=True, seconds=3))
    assert session.sent[-1][1] is False
    connected = GmailEvent(phase=GmailPhase.CONNECTED, email="s@x.com")
    await pipeline.submit(PHONE, Origin.GOOGLE, Channel.SYSTEM, connected)
    assert len(call.deferred) == 1 and (await last_decision(pipeline))["by"] == "fallback"
    await call.turn_complete(asked_question=False)
    assert call.deferred == [] and session.sent[-1][1] is True


async def test_jev_picks_the_verb_while_speaking(app: App) -> None:
    voice = VoiceResponder(jev=jev_answering("interrupt"))
    pipeline, call, session = await on_a_call(app, voice)
    call.speaking = True
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, Typing(active=True, seconds=3))
    decision = await last_decision(pipeline)
    assert decision["verb"] == "interrupt" and decision["by"] == "jev"
    assert session.sent[-1][1] is True


async def test_interrupt_waits_while_a_tool_runs(app: App) -> None:
    voice = VoiceResponder(jev=jev_answering("interrupt"))
    pipeline, call, _ = await on_a_call(app, voice)
    call.speaking, call.tool_running = True, True
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, Typing(active=True, seconds=3))
    assert (await last_decision(pipeline))["verb"] == "defer" and len(call.deferred) == 1


async def test_no_call_in_progress_drops(app: App) -> None:
    voice = VoiceResponder()
    pipeline, _, _ = await on_a_call(app, voice)
    voice.calls.clear()
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, UserMessage(text="hello?"))
    assert (await last_decision(pipeline))["verb"] == "drop"


async def test_talking_over_the_voice_hands_held_notes_in_silently(app: App) -> None:
    pipeline, call, session = await on_a_call(app, VoiceResponder())
    call.speaking = True
    connected = GmailEvent(phase=GmailPhase.CONNECTED, email="s@x.com")
    await pipeline.submit(PHONE, Origin.GOOGLE, Channel.SYSTEM, connected)
    assert len(call.deferred) == 1
    await call.user_started()
    assert call.deferred == [] and not call.speaking and session.sent[-1][1] is False
    assert session.sent[-1][0].startswith(UNSAID)  # still to be said, in its reply to them


async def test_a_task_running_through_a_quiet_stretch_gets_one_still_looking(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.voice import call as call_module

    _, call, session = await on_a_call(app, VoiceResponder())
    call.working["j1"] = "look up NU-SHIP enrollment"
    monkeypatch.setattr(call_module, "STILL_LOOKING", 0.0)
    monkeypatch.setattr(call_module, "SILENCE", 0.0)
    watcher = asyncio.create_task(call_module._silence(call))  # pyright: ignore[reportPrivateUsage]
    await asyncio.sleep(2.5)
    watcher.cancel()
    spoken = [text for text, speak in session.sent if speak]
    assert len(spoken) == 1 and "still looking" in spoken[0] and "NU-SHIP" in spoken[0]
    assert not call.hang_up_asked.is_set()  # waiting on a task isn't them leaving


async def test_nothing_reaches_a_call_that_ended(app: App) -> None:
    _, call, session = await on_a_call(app, VoiceResponder())
    call.closed = True
    await call.send("late", speak=False)
    assert session.sent == []


def test_a_connected_gmail_is_confirmed_and_its_inbox_kept_for_when_they_ask() -> None:
    item = InboxItem(id="m1", sender="ConEd", subject="Your bill is ready", snippet="$84.12")
    connected = ev(GmailEvent(phase=GmailPhase.CONNECTED, email="s@x.com", inbox=[item]))
    note = call_note(connected)
    assert note is not None and note.speak and "what's next" not in note.text  # the chain's job
    assert "once they want you to" in note.text and "ConEd: Your bill" in note.text
    turn = connected.payload.turn(connected.ts)
    assert turn is not None and "once they want you to" in turn.text


async def test_held_background_goes_in_when_they_start_talking(app: App) -> None:
    _, call, session = await on_a_call(app, VoiceResponder())
    await call.whisper("The Gmail link is in their texts.")
    assert session.sent == []  # the voice finished its turn: hold it
    await call.user_started()
    assert session.sent == [("The Gmail link is in their texts.", False)] and call.held == []


async def test_end_call_on_a_live_call_waits_for_the_goodbye(app: App) -> None:
    pipeline, call, _ = await on_a_call(app, app.voice)
    assert app.env.hang_up is not None and app.env.hang_up(PHONE)
    assert call.hang_up_after == 0
    assert (await pipeline.user(PHONE)).call.phase.value == "connected"  # not ended yet
    app.voice.calls.clear()
    assert not app.env.hang_up(PHONE)  # no live call: end_call falls back to a timer


async def test_on_the_voices_turn_background_goes_straight_in(app: App) -> None:
    _, call, session = await on_a_call(app, VoiceResponder())
    await call.user_started()  # they spoke; the voice owes a reply
    await call.whisper("Their need is saved: taxes.")
    assert session.sent == [("Their need is saved: taxes.", False)]


async def test_silence_gets_a_check_in_then_a_graceful_hang_up(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.voice import call as call_module

    _, call, session = await on_a_call(app, VoiceResponder())
    monkeypatch.setattr(call_module, "SILENCE", 0.0)
    monkeypatch.setattr(responder_module, "ANSWER_WAIT", 0.0)  # no voice here to answer
    watcher = asyncio.create_task(call_module._silence(call))  # pyright: ignore[reportPrivateUsage]
    await asyncio.wait_for(call.hang_up_asked.wait(), 5)
    watcher.cancel()
    spoken = [text for text, speak in session.sent if speak]
    assert "Check in" in spoken[0] and "text" in spoken[1]
    assert call.hang_up_asked.is_set() and call.hang_up_reason == "silence"


async def test_long_notes_go_to_the_voice_in_pieces(app: App) -> None:
    _, call, session = await on_a_call(app, VoiceResponder())
    note = "\n".join(f"line {i}: " + "x" * 90 for i in range(40))  # ~4000 chars
    await call.send(note, speak=True)
    assert len(session.sent) > 1 and all(len(text) <= 1200 for text, _ in session.sent)
    assert [speak for _, speak in session.sent] == [False] * (len(session.sent) - 1) + [True]
    assert "\n".join(text for text, _ in session.sent) == note


def test_call_lines_are_read_in_spoken_order() -> None:
    """Live logs a long agent line when it ends, after the "yeah" said halfway through it;
    the turn ids say the real order, per call, and other events stay put."""
    from datetime import UTC, datetime

    from app.agent.context import spoken_order, turns
    from app.agent.events import SlotChanged
    from app.events.event import Event
    from app.events.payload import Channel, Origin
    from app.voice.call_state import CallEvent, CallTransition
    from app.voice.events import Speaker, VoiceUtterance

    def ev(seq: int, payload: object) -> Event:
        return Event(
            seq=seq,
            ts=datetime.now(UTC),
            origin=Origin.CALL,
            channel=Channel.VOICE,
            payload=payload,  # pyright: ignore[reportArgumentType]
        )

    def line(seq: int, turn_id: str, text: str) -> Event:
        speaker = Speaker.AGENT if turn_id.startswith("agent") else Speaker.USER
        return ev(seq, VoiceUtterance(speaker=speaker, text=text, turn_id=turn_id))

    connected = CallEvent(transition=CallTransition.CONNECTED)
    events = [
        ev(1, connected),
        line(2, "agent-0", "what should i call you?"),
        line(3, "user-2", "yeah, sure"),  # said while the voice was still on its line
        ev(4, SlotChanged(slot="agent_name", new="Mila")),
        line(5, "agent-1", "how about mila? you good with that?"),
        ev(6, connected),  # a second call numbers from 0 again
        line(7, "user-1", "hi again"),
        line(8, "agent-0", "hey!"),
    ]
    ordered = [e.seq for e in spoken_order(events)]
    assert ordered == [1, 2, 5, 3, 4, 6, 8, 7]  # the save stays after their yes
    assert "mila" in turns(events, UTC)[0].text  # the question comes before their yes


async def test_a_re_offer_after_it_is_done_gets_caught() -> None:
    """Jev judges the line; its answer becomes a note naming what's already done."""
    from typing import cast

    from app.agent.slots import Slots
    from app.google.events import GmailPhase
    from app.jev import Choice
    from app.voice.intent import slip

    class FakeJev:
        def __init__(self, choice: str, p: float) -> None:
            self.answer = Choice(choice=choice, probabilities={choice: p})
            self.seen: dict[str, Any] = {}

        async def choice(self, question: str, criteria: object, state: dict[str, Any]) -> Choice:
            self.seen = state
            return self.answer

    sent = Slots(gmail=GmailPhase.LINK_SENT, user_name="Siobhan")
    jev = FakeJev("link", 0.9)
    note = await slip(cast(Jev, jev), sent, "want me to text you the link?")
    assert note and "already went out" in note
    assert jev.seen["facts"]["google_link"] == "sent"
    assert await slip(cast(Jev, FakeJev("link", 0.4)), sent, "the link?") is None  # unsure
    assert await slip(cast(Jev, FakeJev("none", 0.9)), sent, "what's up?") is None
    assert await slip(None, sent, "want the link?") is None  # no Jev: no check


async def test_an_outcome_they_are_waiting_on_is_said_when_the_voice_is_free() -> None:
    session = FakeSession()
    call = LiveCall(session)
    call.speaking = True
    await call.tell("The email is sent.")
    assert session.sent == []  # not over its own sentence
    await call.turn_complete(asked_question=False)
    assert session.sent == [("The email is sent.", True)]
    await call.tell("The draft is in their texts.")
    assert session.sent[-1] == ("The email is sent.", True)  # one at a time: it's answering
    call.speaking = True
    await call.turn_complete(asked_question=False)  # it said it; the next one goes
    assert session.sent[-1] == ("The draft is in their texts.", True)
    await call.user_started()  # they spoke: the voice owes them an answer first
    await call.tell("The event is on their calendar.")
    assert session.sent[-1] == ("The draft is in their texts.", True)  # not over its answer
    call.speaking, call.voice_owes_reply = True, False  # its answer began
    await call.turn_complete(asked_question=False)
    assert session.sent[-1] == ("The event is on their calendar.", True)


async def test_the_voice_hears_onboarding_move_on_in_the_objectives_words(app: App) -> None:
    from app.agent.events import SlotChanged
    from app.voice.call import StateNotes

    pipeline, call, session = await on_a_call(app, VoiceResponder())
    state = StateNotes(app.env, PHONE, call)
    pipeline.subscribe(PHONE, state.moved, kinds={"objective_moved"})
    await pipeline.submit(
        PHONE, Origin.VOICE_AGENT, Channel.VOICE, SlotChanged(slot="agent_name", new="Mino")
    )
    said, spoken = session.sent[-1]
    assert said.startswith("You've got a name: Mino!") and spoken
    assert "contact card" not in said
    await pipeline.submit(
        PHONE, Origin.VOICE_AGENT, Channel.VOICE, SlotChanged(slot="agent_name", new="Milo")
    )
    assert len(session.sent) == 1  # a rename moves nothing
    await call.user_started()  # "you got it": the voice's reply is forming
    await pipeline.submit(
        PHONE, Origin.VOICE_AGENT, Channel.VOICE, SlotChanged(slot="user_name", new="Billy")
    )
    said, spoken = session.sent[-1]
    assert said.startswith("You know their name now: Billy") and not spoken  # into that reply


def test_a_callback_opens_on_what_this_call_is_for() -> None:
    from datetime import UTC, datetime

    from app.agent.slots import Slots
    from app.users.user import User
    from app.voice.call import _opener  # pyright: ignore[reportPrivateUsage]
    from app.voice.call_state import CallPhase, CallState, Initiator

    call = CallState(
        phase=CallPhase.CONNECTED,
        reason="the email draft",
        initiated_by=Initiator.AGENT,
        started_at=datetime.now(UTC),
    )
    user = User(
        phone=PHONE, slots=Slots(agent_name="Mila", user_name="Sam"), call=call, floor=Medium.VOICE
    )
    opener = _opener(user)
    assert "the email draft" in opener and "left off" not in opener


async def test_a_text_sent_during_a_call_shows_typing_first(
    app: App, messenger: CapturingMessenger, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent import agent as agent_module

    monkeypatch.setattr(agent_module, "CALL_TYPING", 0.0)
    user = await app.pipeline.user(PHONE)
    await agent_module.say(app.env.deps(user, Medium.VOICE), "recap: aid form, NU-SHIP")
    assert messenger.typing == [True, False] and messenger.sent == ["recap: aid form, NU-SHIP"]


async def test_what_waits_goes_in_as_one_note_and_only_their_own_doing_cuts_in(app: App) -> None:
    pipeline, call, session = await on_a_call(app, VoiceResponder(jev=jev_answering("interrupt")))
    call.speaking = True
    for question in ("Which channel?", "Post it now or later?"):
        asked = JobAsked(job="j1", question=question)
        await pipeline.submit(PHONE, Origin.JOB, Channel.SYSTEM, asked)
    assert session.sent == [] and (await last_decision(pipeline))["verb"] == "defer"
    await call.turn_complete(asked_question=False)
    spoken = [text for text, speak in session.sent if speak]
    assert len(spoken) == 1 and "Which channel?" in spoken[0] and "Post it now" in spoken[0]


async def test_a_passing_line_is_dropped_once_they_speak() -> None:
    session = FakeSession()
    call = LiveCall(session)
    call.speaking = True
    await call.tell("They've gone quiet. Check in once.", passing=True)
    await call.tell("The email is sent.")
    await call.user_started()
    sent = session.sent[-1][0]
    assert "The email is sent." in sent and "gone quiet" not in sent  # owed stays, passing goes


def test_during_onboarding_a_connection_goes_in_silently_and_the_chain_moves_on() -> None:
    connected = ev(GmailEvent(phase=GmailPhase.CONNECTED, email="s@x.com"))
    during, after = call_note(connected, onboarding=True), call_note(connected)
    assert during is not None and not during.speak and "Say so" not in during.text
    assert after is not None and after.speak
    moved = ObjectiveMoved(left="google", how="done", got="s@x.com", now="help_need")
    said = ONBOARDING.announcement(moved)
    assert said is not None and "what they'd like help with" in said


async def test_what_waits_is_said_once_the_voice_is_quiet_whatever_lives_turn_says(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.voice import call as call_module

    session = FakeSession()
    call = LiveCall(session)
    call.speaking = True  # Live's turn is still open (its own backend is working)
    await call.tell("Evanston: sunny, 65°F.")
    assert session.sent == []
    monkeypatch.setattr(call_module, "VOICE_DONE", 0.0)
    watcher = asyncio.create_task(call_module._quiet(call))  # pyright: ignore[reportPrivateUsage]
    await asyncio.sleep(0.4)
    assert session.sent == [("Evanston: sunny, 65°F.", True)]
    await call.user_started()  # they ask something; the voice says nothing back
    await call.tell("Miami: no storms forecast next week.")
    monkeypatch.setattr(call_module, "REPLY_WAIT", 0.0)
    await asyncio.sleep(0.4)
    watcher.cancel()
    assert session.sent[-1] == ("Miami: no storms forecast next week.", True)  # its answer


def test_a_call_they_start_reads_as_theirs() -> None:
    from app.voice.call_state import CallEvent, CallTransition, Initiator

    theirs = ev(CallEvent(transition=CallTransition.CONNECTING, initiated_by=Initiator.USER))
    turn = theirs.payload.turn(theirs.ts)
    assert turn is not None and turn.text.startswith("they called you")
    ours = ev(CallEvent(transition=CallTransition.CONNECTING))
    assert ours.payload.turn(ours.ts) is None  # "you started calling" came with the ringing


@pytest.mark.parametrize(
    ("choice", "p", "move"),
    [
        ("goodbye", 0.9, "goodbye"),
        ("goodbye", 0.6, None),
        ("doing", 0.7, "doing"),
        ("neither", 0.9, None),
    ],
)
async def test_a_sign_off_needs_jev_to_be_sure(choice: str, p: float, move: str | None) -> None:
    from app.voice.intent import saying

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"answers": {"q": {"choice": choice, "probabilities": {choice: p}}}}
        )

    jev = Jev(api_key="k", client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    assert await saying(jev, ["user: gotta go"], "alright, talk soon, bye!") == move


@pytest.mark.parametrize(("p", "told"), [(0.93, True), (0.34, False)])
async def test_a_note_the_voice_already_said_is_not_said_again(p: float, told: bool) -> None:
    from app.voice.intent import already_told

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"answers": {"q": {"noul": p}}})

    jev = Jev(api_key="k", client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    said = ["assistant: the aid office says your application is incomplete"]
    assert await already_told(jev, said, "The aid application is incomplete.") is told
    assert await already_told(None, said, "anything") is False  # without Jev, it's news
