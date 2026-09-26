from __future__ import annotations

from app.agent.types import Slots
from app.calls.types import CallEvent, CallState, CallTransition
from app.events.base import Channel, Origin
from app.gmail.types import GmailEvent, GmailPhase
from app.routing.filter import DefaultDecider, Filter
from app.routing.router import Router
from app.routing.types import DecidedBy, Medium, Verb
from app.users.types import User
from tests.conftest import PHONE, FakeClock, ev, fake_drivers, typing, user_text


def user(floor: Medium = Medium.TEXT) -> User:
    return User(phone=PHONE, slots=Slots(), call=CallState(), floor=floor)


def router(clock: FakeClock) -> Router:
    return Router(Filter(DefaultDecider()), clock=clock)


async def test_message_when_idle_starts_a_text_run(clock: FakeClock) -> None:
    text, voice, drivers = fake_drivers(clock)
    d = await router(clock).route(drivers, user(), user_text("hey"), [])
    assert d.verb is Verb.START and d.by is DecidedBy.FIXED
    assert text.log == [("start", "user_message")] and voice.log == []


async def test_message_during_a_run_interrupts(clock: FakeClock) -> None:
    text, _, drivers = fake_drivers(clock)
    text.begin()
    d = await router(clock).route(drivers, user(), user_text("wait"), [])
    assert d.verb is Verb.INTERRUPT and text.log == [("interrupt", "user_message")]


async def test_interrupt_degrades_to_defer_with_side_effect(clock: FakeClock) -> None:
    text, _, drivers = fake_drivers(clock)
    text.begin(side_effect=True)
    d = await router(clock).route(drivers, user(), user_text("nvm"), [])
    assert d.verb is Verb.DEFER and d.note and "side effect" in d.note


async def test_voice_floor_routes_to_voice_driver(clock: FakeClock) -> None:
    text, voice, drivers = fake_drivers(clock)
    await router(clock).route(drivers, user(Medium.VOICE), user_text("texting during the call"), [])
    assert voice.log == [("start", "user_message")] and text.log == []


async def test_typing_during_voice_run_defaults_to_absorb(clock: FakeClock) -> None:
    _, voice, drivers = fake_drivers(clock)
    voice.begin(question=True)
    d = await router(clock).route(drivers, user(Medium.VOICE), typing(True, 2.5), [])
    assert d.verb is Verb.ABSORB and d.by is DecidedBy.DEFAULT


async def test_gmail_during_voice_run_defers(clock: FakeClock) -> None:
    _, voice, drivers = fake_drivers(clock)
    voice.begin()
    e = ev(GmailEvent(phase=GmailPhase.CONNECTED, email="a@b.c"), Origin.GOOGLE, Channel.SYSTEM)
    assert (await router(clock).route(drivers, user(Medium.VOICE), e, [])).verb is Verb.DEFER


async def test_call_outcome_interrupts(clock: FakeClock) -> None:
    text, _, drivers = fake_drivers(clock)
    text.begin()
    e = ev(
        CallEvent(transition=CallTransition.ENDED, reason="user_hangup"),
        Origin.CALL,
        Channel.SYSTEM,
    )
    assert (await router(clock).route(drivers, user(), e, [])).verb is Verb.INTERRUPT


async def test_decision_latency_is_measured(clock: FakeClock) -> None:
    text, _, drivers = fake_drivers(clock)

    async def slow_start(event: object) -> None:
        clock.advance(0.25)
        text.begin()

    text.start = slow_start  # type: ignore[method-assign]
    assert (await router(clock).route(drivers, user(), user_text("x"), [])).ms == 250
