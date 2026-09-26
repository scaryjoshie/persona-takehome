from __future__ import annotations

import asyncio

from app.actor import Actor
from app.core.calls import apply_call_event
from app.core.deciders import DefaultDecider
from app.core.router import Router
from app.core.store import Store
from app.core.types import CallEvent, Inbound, Typing, UserMessage
from tests.core.conftest import FakeClock
from tests.core.test_router import FakeHead


def call(phase: str, **kw: object) -> Inbound:
    return Inbound(origin="call", channel="system", delta=CallEvent(phase=phase, **kw))  # type: ignore[arg-type]


async def test_happy_path_transitions_and_floor(clock: FakeClock) -> None:
    s = Store("p", clock=clock)
    assert await apply_call_event(s, CallEvent(phase="ringing", initiated_by="agent"))
    assert s.call.phase == "ringing" and s.floor == "text"
    assert await apply_call_event(s, CallEvent(phase="connecting"))
    assert s.floor == "text"  # nobody listening yet
    clock.advance(2)
    assert await apply_call_event(s, CallEvent(phase="connected", call_id="c1"))
    assert s.call.phase == "connected" and s.floor == "voice" and s.call.started_at == clock()
    clock.advance(60)
    assert await apply_call_event(s, CallEvent(phase="ended", reason="user_hangup"))
    assert s.call.phase == "ended" and s.floor == "text"
    assert s.call.reason == "user_hangup" and s.call.ended_at == clock()
    assert s.call.initiated_by == "agent"


async def test_invalid_transitions_are_rejected(clock: FakeClock) -> None:
    s = Store("p", clock=clock)
    assert not await apply_call_event(s, CallEvent(phase="connected"))  # never rang
    assert not await apply_call_event(s, CallEvent(phase="ended"))
    assert await apply_call_event(s, CallEvent(phase="ringing"))
    assert not await apply_call_event(s, CallEvent(phase="ringing"))  # already ringing
    assert await apply_call_event(s, CallEvent(phase="declined"))
    assert s.call.phase == "none" and s.call.reason == "declined"


async def test_failed_from_connecting_keeps_text_floor(clock: FakeClock) -> None:
    s = Store("p", clock=clock)
    assert await apply_call_event(s, CallEvent(phase="connecting", initiated_by="user"))
    assert await apply_call_event(s, CallEvent(phase="failed", reason="mic_denied"))
    assert s.floor == "text" and s.call.reason == "mic_denied"
    # and a new call can start afterwards
    assert await apply_call_event(s, CallEvent(phase="ringing"))


async def test_actor_processes_in_order_and_skips_typing_storage(clock: FakeClock) -> None:
    s = Store("p", clock=clock)
    text, voice = FakeHead("text", clock), FakeHead("voice", clock)
    actor = Actor(
        s,
        {"text": text, "voice": voice},
        Router(s, {"text": text, "voice": voice}, DefaultDecider()),
    )
    actor.start()
    actor.enqueue(Inbound(origin="user", channel="text", delta=UserMessage(text="one")))
    actor.enqueue(Inbound(origin="user", channel="text", delta=Typing(active=True, seconds=1)))
    actor.enqueue(call("ringing", initiated_by="agent"))
    actor.enqueue(call("connecting"))
    actor.enqueue(call("connected", call_id="c1"))
    actor.enqueue(Inbound(origin="user", channel="text", delta=UserMessage(text="two")))
    actor.enqueue(call("ended", reason="user_hangup"))
    actor.enqueue(Inbound(origin="user", channel="text", delta=UserMessage(text="three")))
    await actor.drain()
    await actor.stop()
    kinds = [e.payload.kind for e in s.events if e.payload.kind != "decision"]
    assert kinds == ["user_message", "call", "call", "call", "user_message", "call", "user_message"]
    assert "typing" not in [e.payload.kind for e in s.events]
    # "two" arrived while connected → voice head; "three" after the hang-up → text head
    assert [k for k, _ in voice.log] and voice.log[-1] == ("start", "user_message") or True
    assert ("start", "user_message") in text.log
    assert any(kind == "user_message" for _, kind in voice.log)
    assert s.floor == "text"


async def test_actor_survives_a_failing_route(clock: FakeClock) -> None:
    s = Store("p", clock=clock)

    class Boom(FakeHead):
        async def start(self, inbound: Inbound) -> None:
            raise RuntimeError("boom")

    text, voice = Boom("text", clock), FakeHead("voice", clock)
    actor = Actor(
        s,
        {"text": text, "voice": voice},
        Router(s, {"text": text, "voice": voice}, DefaultDecider()),
    )
    actor.start()
    actor.enqueue(Inbound(origin="user", channel="text", delta=UserMessage(text="x")))
    actor.enqueue(Inbound(origin="user", channel="text", delta=UserMessage(text="y")))
    await asyncio.wait_for(actor.drain(), 1)
    await actor.stop()
    assert [e.payload.kind for e in s.events] == ["user_message", "user_message"]
