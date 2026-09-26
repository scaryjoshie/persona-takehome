from __future__ import annotations

from app.calls.events import CallEvent, CallTransition, Initiator
from app.events.event import Event
from app.events.payload import Channel, Origin
from app.gmail.events import GmailEvent, GmailPhase
from app.main import App
from app.routing.types import Medium
from app.text.events import AgentMessage, Typing, UserMessage
from tests.conftest import PHONE, FakeClock, FakeTimers, fake_responders


def swap_responders(app: App, clock: FakeClock):  # type: ignore[no-untyped-def]
    text, voice, responders = fake_responders(clock)
    app.live_users.get(PHONE).responders = responders
    return text, voice


async def test_submit_persists_routes_and_orders(app: App, clock: FakeClock) -> None:
    text, voice = swap_responders(app, clock)
    a = app.actions
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
    assert voice.log == [("start", "user_message")]  # only "two", while connected
    assert text.log == [
        ("start", "user_message"),
        ("absorb", "typing"),
        ("interrupt", "call"),
        ("interrupt", "user_message"),
    ]
    user = await a.user(PHONE)
    assert user.floor is Medium.TEXT and user.call.reason == "user_hangup"


async def test_publish_reaches_subscribers_with_kind_filter(app: App) -> None:
    seen: list[str] = []
    thread: list[str] = []
    rt = app.live_users.get(PHONE)
    rt.subscribe(lambda e: seen.append(e.kind))
    rt.subscribe(lambda e: thread.append(e.kind), kinds={"user_message", "agent_message"})
    await app.actions.submit(PHONE, Origin.USER, Channel.TEXT, UserMessage(text="x"))
    await app.actions.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, AgentMessage(text="y"))
    assert seen == ["user_message", "decision", "agent_message"]
    assert thread == ["user_message", "agent_message"]


async def test_route_override_and_record_only_kinds(app: App, clock: FakeClock) -> None:
    text, _ = swap_responders(app, clock)
    await app.actions.submit(
        PHONE, Origin.USER, Channel.TEXT, UserMessage(text="replayed"), route=False
    )
    await app.actions.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, AgentMessage(text="bubble"))
    assert text.log == []
    assert [e.kind for e in await app.actions.history(PHONE)] == ["user_message", "agent_message"]


async def test_invalid_call_transition_is_dropped(app: App, clock: FakeClock) -> None:
    text, _ = swap_responders(app, clock)
    result = await app.actions.submit(
        PHONE, Origin.CALL, Channel.SYSTEM, CallEvent(transition=CallTransition.ENDED)
    )
    assert result is None and text.log == [] and await app.actions.history(PHONE) == []


async def test_gmail_outcomes_route_but_link_sent_does_not(app: App, clock: FakeClock) -> None:
    text, _ = swap_responders(app, clock)
    await app.actions.submit(
        PHONE, Origin.TEXT_AGENT, Channel.TEXT, GmailEvent(phase=GmailPhase.LINK_SENT)
    )
    await app.actions.submit(
        PHONE, Origin.GOOGLE, Channel.SYSTEM, GmailEvent(phase=GmailPhase.CONNECTED, email="a@b.c")
    )
    assert text.log == [("start", "gmail")]


async def test_set_slot_is_idempotent_and_logged(app: App) -> None:
    a = app.actions
    assert await a.set_slot(
        PHONE, "user_name", "Siobhan", origin=Origin.VOICE_AGENT, channel=Channel.VOICE
    )
    assert not await a.set_slot(
        PHONE, "user_name", "Siobhan", origin=Origin.TEXT_AGENT, channel=Channel.TEXT
    )
    assert [e.kind for e in await a.history(PHONE)] == ["slot_changed"]
    assert (await a.user(PHONE)).slots.missing() == ("agent_name", "help_need", "gmail")


async def test_events_never_overlap_across_awaits(app: App, timers: FakeTimers) -> None:
    import asyncio

    a = app.actions
    await asyncio.gather(
        *(a.submit(PHONE, Origin.USER, Channel.TEXT, UserMessage(text=str(i))) for i in range(5))
    )
    seqs = [e.seq for e in await a.history(PHONE)]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert isinstance((await a.history(PHONE))[0], Event)
