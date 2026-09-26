from __future__ import annotations

from app.agent.events import CallOptOut, ContactSaved, Graduated, SlotChanged
from app.events.event import Event
from app.events.payload import Channel, Origin
from app.gmail.events import GmailEvent, GmailPhase
from app.pipeline import Pipeline
from app.text.events import AgentMessage, Typing, UserMessage
from app.users.user import Medium
from app.voice.call_events import CallEvent, CallTransition, Initiator
from tests.conftest import PHONE, FakeClock, FakeResponder, FakeTimers


def swap_responders(pipeline: Pipeline, clock: FakeClock) -> tuple[FakeResponder, FakeResponder]:
    text, voice = FakeResponder(), FakeResponder()
    pipeline.responders[Medium.TEXT] = text
    pipeline.responders[Medium.VOICE] = voice
    return text, voice


async def test_submit_persists_routes_and_orders(pipeline: Pipeline, clock: FakeClock) -> None:
    text, voice = swap_responders(pipeline, clock)
    a = pipeline
    await a.submit(PHONE, Origin.USER, Channel.TEXT, UserMessage(text="one"))
    await a.submit(PHONE, Origin.USER, Channel.TEXT, Typing(active=True, seconds=1))
    await a.submit(
        PHONE,
        Origin.CALL,
        Channel.SYSTEM,
        CallEvent(transition=CallTransition.RINGING, initiated_by=Initiator.AGENT),
    )
    await a.submit(
        PHONE, Origin.CALL, Channel.SYSTEM, CallEvent(transition=CallTransition.CONNECTING)
    )
    await a.submit(
        PHONE,
        Origin.CALL,
        Channel.SYSTEM,
        CallEvent(transition=CallTransition.CONNECTED, call_id="c1"),
    )
    await a.submit(PHONE, Origin.USER, Channel.TEXT, UserMessage(text="two"))
    await a.submit(
        PHONE,
        Origin.CALL,
        Channel.SYSTEM,
        CallEvent(transition=CallTransition.ENDED, reason="user_hangup"),
    )
    await a.submit(PHONE, Origin.USER, Channel.TEXT, UserMessage(text="three"))
    history = await a.history(PHONE)
    kinds = [e.kind for e in history if e.kind != "decision"]
    assert kinds == ["user_message", "call", "call", "call", "user_message", "call", "user_message"]
    assert "typing" not in [e.kind for e in history]
    assert [e.seq for e in history] == list(range(1, len(history) + 1))
    assert voice.handled == ["user_message"]  # only "two", while connected
    assert text.handled == ["user_message", "typing", "call", "user_message"]
    user = await a.user(PHONE)
    assert user.floor is Medium.TEXT and user.call.reason == "user_hangup"


async def test_publish_reaches_subscribers_with_kind_filter(pipeline: Pipeline) -> None:
    seen: list[str] = []
    thread: list[str] = []
    swap_responders(pipeline, FakeClock())
    pipeline.subscribe(PHONE, lambda e: seen.append(e.kind))
    pipeline.subscribe(
        PHONE, lambda e: thread.append(e.kind), kinds={"user_message", "agent_message"}
    )
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, UserMessage(text="x"))
    await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, AgentMessage(text="y"))
    assert seen == ["user_message", "decision", "agent_message"]
    assert thread == ["user_message", "agent_message"]


async def test_route_override_and_record_only_kinds(pipeline: Pipeline, clock: FakeClock) -> None:
    text, _ = swap_responders(pipeline, clock)
    await pipeline.submit(
        PHONE, Origin.USER, Channel.TEXT, UserMessage(text="replayed"), route=False
    )
    await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, AgentMessage(text="bubble"))
    assert text.handled == []
    assert [e.kind for e in await pipeline.history(PHONE)] == ["user_message", "agent_message"]


async def test_invalid_call_transition_is_dropped(pipeline: Pipeline, clock: FakeClock) -> None:
    text, _ = swap_responders(pipeline, clock)
    result = await pipeline.submit(
        PHONE, Origin.CALL, Channel.SYSTEM, CallEvent(transition=CallTransition.ENDED)
    )
    assert result is None and text.handled == [] and await pipeline.history(PHONE) == []


async def test_gmail_outcomes_route_but_link_sent_does_not(
    pipeline: Pipeline, clock: FakeClock
) -> None:
    text, _ = swap_responders(pipeline, clock)
    await pipeline.submit(
        PHONE, Origin.TEXT_AGENT, Channel.TEXT, GmailEvent(phase=GmailPhase.LINK_SENT)
    )
    await pipeline.submit(
        PHONE, Origin.GOOGLE, Channel.SYSTEM, GmailEvent(phase=GmailPhase.CONNECTED, email="a@b.c")
    )
    assert text.handled == ["gmail"]


async def test_slot_events_update_the_user_and_repeat_values_are_dropped(
    pipeline: Pipeline,
) -> None:
    named = SlotChanged(slot="user_name", new="Siobhan")
    first = await pipeline.submit(PHONE, Origin.VOICE_AGENT, Channel.VOICE, named)
    again = await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, named)
    assert first is not None and again is None
    assert isinstance(first.payload, SlotChanged) and first.payload.old is None
    assert [e.kind for e in await pipeline.history(PHONE)] == ["slot_changed"]
    assert (await pipeline.user(PHONE)).slots.missing() == ("agent_name", "help_need", "gmail")


async def test_gmail_and_graduation_events_update_slots(pipeline: Pipeline) -> None:
    connected = GmailEvent(phase=GmailPhase.CONNECTED, email="s@x.com")
    await pipeline.submit(PHONE, Origin.GOOGLE, Channel.SYSTEM, connected, route=False)
    await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, Graduated())
    assert await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, Graduated()) is None
    slots = (await pipeline.user(PHONE)).slots
    assert slots.gmail is GmailPhase.CONNECTED and slots.gmail_email == "s@x.com"
    assert slots.graduated


async def test_events_never_overlap_across_awaits(pipeline: Pipeline, timers: FakeTimers) -> None:
    import asyncio

    a = pipeline
    await asyncio.gather(
        *(a.submit(PHONE, Origin.USER, Channel.TEXT, UserMessage(text=str(i))) for i in range(5))
    )
    seqs = [e.seq for e in await a.history(PHONE)]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert isinstance((await a.history(PHONE))[0], Event)


async def test_the_gmail_link_goes_out_once(pipeline: Pipeline) -> None:
    sent = GmailEvent(phase=GmailPhase.LINK_SENT)
    assert await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, sent) is not None
    assert await pipeline.submit(PHONE, Origin.VOICE_AGENT, Channel.VOICE, sent) is None


async def test_contact_saved_sets_what_the_phone_calls_the_agent(pipeline: Pipeline) -> None:
    saved = ContactSaved(name="Mila")
    assert await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, saved) is not None
    assert await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, saved) is None  # no change
    assert (await pipeline.user(PHONE)).slots.contact_name == "Mila"


async def test_declining_a_call_is_remembered(pipeline: Pipeline) -> None:
    for t in (CallTransition.RINGING, CallTransition.DECLINED):
        await pipeline.submit(PHONE, Origin.CALL, Channel.SYSTEM, CallEvent(transition=t))
    assert (await pipeline.user(PHONE)).slots.no_calls
    opt_out = CallOptOut()
    assert await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, opt_out) is None
