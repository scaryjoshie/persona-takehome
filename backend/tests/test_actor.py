from __future__ import annotations

import asyncio

from app.actor import Actor
from app.calls.hook import call_machine_hook
from app.calls.types import CallEvent, CallTransition, Initiator
from app.events.base import Channel, Origin
from app.routing.filter import DefaultDecider, Filter
from app.routing.router import Router
from app.routing.types import Medium
from app.text.types import Typing, UserMessage
from app.user import User
from tests.conftest import FakeClock, FakeDriver


def make(user: User, clock: FakeClock) -> tuple[Actor, FakeDriver, FakeDriver]:
    text, voice = FakeDriver(Medium.TEXT, clock), FakeDriver(Medium.VOICE, clock)
    router = Router(user, {Medium.TEXT: text, Medium.VOICE: voice}, Filter(DefaultDecider()))
    return Actor(user, router, hooks=[call_machine_hook]), text, voice


async def test_submit_persists_routes_and_orders(user: User, clock: FakeClock) -> None:
    actor, text, voice = make(user, clock)
    actor.start()
    actor.submit(Origin.USER, Channel.TEXT, UserMessage(text="one"))
    actor.submit(Origin.USER, Channel.TEXT, Typing(active=True, seconds=1))
    actor.submit(
        Origin.CALL,
        Channel.SYSTEM,
        CallEvent(transition=CallTransition.RINGING, initiated_by=Initiator.AGENT),
    )
    actor.submit(Origin.CALL, Channel.SYSTEM, CallEvent(transition=CallTransition.CONNECTING))
    actor.submit(
        Origin.CALL, Channel.SYSTEM, CallEvent(transition=CallTransition.CONNECTED, call_id="c1")
    )
    actor.submit(Origin.USER, Channel.TEXT, UserMessage(text="two"))
    actor.submit(
        Origin.CALL,
        Channel.SYSTEM,
        CallEvent(transition=CallTransition.ENDED, reason="user_hangup"),
    )
    actor.submit(Origin.USER, Channel.TEXT, UserMessage(text="three"))
    await actor.drain()
    await actor.stop()
    kinds = [e.kind for e in user.store.events if e.kind != "decision"]
    assert kinds == ["user_message", "call", "call", "call", "user_message", "call", "user_message"]
    assert "typing" not in [e.kind for e in user.store.events]
    assert voice.log == [("start", "user_message")]  # only "two", while connected
    # The fake driver never ends a run, so after "one" every text event applies a verb:
    assert text.log == [
        ("start", "user_message"),  # "one"
        ("absorb", "typing"),
        ("interrupt", "call"),  # the hang-up outcome, answered by text
        ("interrupt", "user_message"),  # "three"
    ]
    assert user.floor == "text" and user.call.reason == "user_hangup"


async def test_route_override_and_record_only_kinds(user: User, clock: FakeClock) -> None:
    actor, text, _ = make(user, clock)
    actor.start()
    actor.submit(Origin.USER, Channel.TEXT, UserMessage(text="replayed"), route=False)
    from app.text.types import AgentMessage

    actor.submit(Origin.TEXT_AGENT, Channel.TEXT, AgentMessage(text="bubble"))
    await actor.drain()
    await actor.stop()
    assert text.log == []
    assert [e.kind for e in user.store.events] == ["user_message", "agent_message"]


async def test_invalid_call_transition_is_dropped(user: User, clock: FakeClock) -> None:
    actor, text, _ = make(user, clock)
    actor.start()
    actor.submit(Origin.CALL, Channel.SYSTEM, CallEvent(transition=CallTransition.ENDED))
    await actor.drain()
    await actor.stop()
    assert text.log == [] and user.call.phase == "none"


async def test_actor_survives_a_failing_route(user: User, clock: FakeClock) -> None:
    class Boom(FakeDriver):
        async def start(self, event: object) -> None:
            raise RuntimeError("boom")

    text = Boom(Medium.TEXT, clock)
    router = Router(user, {Medium.TEXT: text, Medium.VOICE: text}, Filter(DefaultDecider()))
    actor = Actor(user, router)
    actor.start()
    actor.submit(Origin.USER, Channel.TEXT, UserMessage(text="x"))
    actor.submit(Origin.USER, Channel.TEXT, UserMessage(text="y"))
    await asyncio.wait_for(actor.drain(), 1)
    await actor.stop()
    assert [e.kind for e in user.store.events] == ["user_message", "user_message"]
