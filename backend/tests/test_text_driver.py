from __future__ import annotations

import asyncio

from app.events.base import Channel, Origin
from app.gmail.types import GmailEvent, GmailPhase
from app.routing.types import Verb
from app.text.driver import TextDriver
from app.text.types import UserMessage
from tests.conftest import FakeClock, FakeRunner, FakeTimers, ev, typing, user_text


def make(clock: FakeClock, *, block: bool = False) -> tuple[TextDriver, FakeTimers, FakeRunner]:
    timers, runner = FakeTimers(clock), FakeRunner(block=block)
    return TextDriver(runner=runner, timers=timers, clock=clock), timers, runner


def texts(runner: FakeRunner, i: int) -> list[str]:
    out: list[str] = []
    for e in runner.requests[i].buffered:
        assert isinstance(e.payload, UserMessage)
        out.append(e.payload.text)
    return out


def gmail_connected():  # type: ignore[no-untyped-def]
    return ev(GmailEvent(phase=GmailPhase.CONNECTED, email="a@b.c"), Origin.GOOGLE, Channel.SYSTEM)


async def test_message_waits_quiet_window_then_runs(clock: FakeClock) -> None:
    driver, timers, runner = make(clock)
    await driver.start(user_text("hey there"))
    assert timers.pending == [1.5] and driver.run is None
    timers.fire_next()
    assert driver.run is not None
    await driver.wait_idle()
    assert texts(runner, 0) == ["hey there"] and driver.run is None


async def test_burst_is_one_run(clock: FakeClock) -> None:
    driver, timers, runner = make(clock)
    await driver.start(user_text("one"))
    for t in ("two", "three"):
        clock.advance(0.5)
        await driver.apply(Verb.INTERRUPT, user_text(t))
    assert len(timers.pending) == 1
    timers.fire_next()
    await driver.wait_idle()
    assert len(runner.requests) == 1 and texts(runner, 0) == ["one", "two", "three"]


async def test_hard_cap_bounds_a_continuous_typer(clock: FakeClock) -> None:
    driver, timers, _ = make(clock)
    await driver.start(user_text("a"))
    for _ in range(4):
        clock.advance(1.75)
        await driver.apply(Verb.INTERRUPT, user_text("more"))
    assert timers.pending == [1.0]  # 7.0 s since the first message; cap is 8.0


async def test_message_count_cap_fires_now(clock: FakeClock) -> None:
    driver, timers, _ = make(clock)
    await driver.start(user_text("1"))
    for n in range(2, 7):
        await driver.apply(Verb.INTERRUPT, user_text(str(n)))
    assert timers.pending == [0.0]


async def test_typing_extends_but_not_forever(clock: FakeClock) -> None:
    driver, timers, _ = make(clock)
    await driver.start(user_text("i think"))
    await driver.apply(Verb.ABSORB, typing(True, 1))
    assert timers.pending == [5.0]
    clock.advance(3.0)
    await driver.apply(Verb.ABSORB, typing(True, 4))
    assert timers.pending == [2.0]
    await driver.apply(Verb.ABSORB, typing(False))
    assert timers.pending == [1.5]


async def test_typing_alone_does_nothing(clock: FakeClock) -> None:
    driver, timers, runner = make(clock)
    await driver.start(typing(True, 2))
    assert timers.pending == [] and driver.run is None and runner.requests == []


async def test_interrupt_cancels_and_reruns(clock: FakeClock) -> None:
    driver, timers, runner = make(clock, block=True)
    await driver.start(user_text("book a dentist"))
    timers.fire_next()
    await asyncio.sleep(0)
    await driver.apply(Verb.INTERRUPT, user_text("actually nvm"))
    await asyncio.sleep(0)
    assert runner.cancelled == 1 and driver.run is None and timers.pending == [1.5]
    runner.block = False
    timers.fire_next()
    await driver.wait_idle()
    assert texts(runner, 1) == ["actually nvm"]


async def test_defer_runs_after_current(clock: FakeClock) -> None:
    driver, timers, runner = make(clock, block=True)
    await driver.start(user_text("hi"))
    timers.fire_next()
    await asyncio.sleep(0)
    await driver.apply(Verb.DEFER, gmail_connected())
    runner.release.set()
    await driver.wait_idle()
    await asyncio.sleep(0)
    assert len(runner.requests) == 2 and runner.requests[1].trigger.kind == "gmail"
    await driver.wait_idle()


async def test_system_outcome_when_idle_runs_now(clock: FakeClock) -> None:
    driver, timers, runner = make(clock)
    await driver.start(gmail_connected())
    assert timers.pending == [] and driver.run is not None
    await driver.wait_idle()
    assert runner.requests[0].trigger.kind == "gmail"


async def test_run_carries_last_question(clock: FakeClock) -> None:
    driver, timers, _ = make(clock)
    await driver.start(user_text("hi"))
    timers.fire_next()
    await driver.wait_idle()  # the fake runner reports it asked a question
    await driver.start(user_text("yes"))
    timers.fire_next()
    assert driver.run is not None and driver.run.last_agent_turn_was_question
    await driver.wait_idle()
