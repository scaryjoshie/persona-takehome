"""The voice medium: what happens to an event that arrives during a call."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.events.payload import Channel, Origin
from app.google.events import GmailEvent, GmailPhase
from app.jev import Jev
from app.main import App
from app.pipeline import Pipeline
from app.text.events import Typing, UserMessage
from app.users.user import Medium
from app.voice.responder import LiveCall, VoiceResponder
from tests.conftest import PHONE


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


async def test_nothing_reaches_a_call_that_ended(app: App) -> None:
    _, call, session = await on_a_call(app, VoiceResponder())
    call.closed = True
    await call.send("late", speak=False)
    assert session.sent == []


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
    from app.jev import Choice, Jev
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
