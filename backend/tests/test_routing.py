from __future__ import annotations

from app.calls.types import CallEvent, CallTransition
from app.events.base import Channel, Origin
from app.gmail.types import GmailEvent, GmailPhase
from app.routing.filter import DefaultDecider, Filter
from app.routing.router import Router
from app.routing.types import DecidedBy, Medium, Verb
from app.user import User
from tests.conftest import FakeClock, FakeDriver, typing, user_text


def make(user: User, clock: FakeClock) -> tuple[FakeDriver, FakeDriver, Router]:
    text, voice = FakeDriver(Medium.TEXT, clock), FakeDriver(Medium.VOICE, clock)
    router = Router(user, {Medium.TEXT: text, Medium.VOICE: voice}, Filter(DefaultDecider()))
    return text, voice, router


async def test_message_when_idle_starts_a_text_run(user: User, clock: FakeClock) -> None:
    text, voice, router = make(user, clock)
    d = await router.route(user_text(user.store, "hey"))
    assert d.verb is Verb.START and d.by is DecidedBy.FIXED
    assert text.log == [("start", "user_message")] and voice.log == []
    assert user.store.events[-1].kind == "decision"


async def test_message_during_a_run_interrupts(user: User, clock: FakeClock) -> None:
    text, _, router = make(user, clock)
    text.begin()
    d = await router.route(user_text(user.store, "wait"))
    assert d.verb is Verb.INTERRUPT and d.by is DecidedBy.FIXED
    assert text.log == [("interrupt", "user_message")]


async def test_interrupt_degrades_to_defer_with_side_effect(user: User, clock: FakeClock) -> None:
    text, _, router = make(user, clock)
    text.begin(side_effect=True)
    d = await router.route(user_text(user.store, "nvm"))
    assert d.verb is Verb.DEFER and d.note and "side effect" in d.note


async def test_voice_floor_routes_to_voice_driver(user: User, clock: FakeClock) -> None:
    text, voice, router = make(user, clock)
    await user.set_floor(Medium.VOICE)
    await router.route(user_text(user.store, "texting during the call"))
    assert voice.log == [("start", "user_message")] and text.log == []


async def test_typing_during_voice_run_defaults_to_absorb(user: User, clock: FakeClock) -> None:
    _, voice, router = make(user, clock)
    await user.set_floor(Medium.VOICE)
    voice.begin(question=True)
    d = await router.route(typing(user.store, True, 2.5))
    assert d.verb is Verb.ABSORB and d.by is DecidedBy.DEFAULT


async def test_gmail_during_voice_run_defers(user: User, clock: FakeClock) -> None:
    _, voice, router = make(user, clock)
    await user.set_floor(Medium.VOICE)
    voice.begin()
    e = user.store.transient(
        Origin.GOOGLE, Channel.SYSTEM, GmailEvent(phase=GmailPhase.CONNECTED, email="a@b.c")
    )
    assert (await router.route(e)).verb is Verb.DEFER


async def test_call_outcomes_route_and_interrupt_but_transitions_do_not(
    user: User, clock: FakeClock
) -> None:
    text, _, router = make(user, clock)
    text.begin()
    ended = CallEvent(transition=CallTransition.ENDED, reason="user_hangup")
    assert ended.should_route()
    assert not CallEvent(transition=CallTransition.RINGING).should_route()
    assert not CallEvent(transition=CallTransition.CONNECTED).should_route()
    e = user.store.transient(Origin.CALL, Channel.SYSTEM, ended)
    assert (await router.route(e)).verb is Verb.INTERRUPT


async def test_decision_latency_is_measured(user: User, clock: FakeClock) -> None:
    class Slow(FakeDriver):
        async def start(self, event: object) -> None:
            clock.advance(0.25)
            self.begin()

    slow = Slow(Medium.TEXT, clock)
    router = Router(user, {Medium.TEXT: slow, Medium.VOICE: slow}, Filter(DefaultDecider()))
    assert (await router.route(user_text(user.store, "x"))).ms == 250
