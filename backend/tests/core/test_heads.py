from __future__ import annotations

import asyncio
from collections.abc import Callable

from app.core.heads import (
    DebouncePolicy,
    RunRequest,
    RunResult,
    TextHead,
    VoiceHead,
    looks_complete,
    looks_fragment,
)
from app.core.types import GmailEvent, Inbound, Typing, UserMessage
from tests.core.conftest import FakeClock


class FakeTimers:
    """Records timers; tests fire them explicitly."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.scheduled: list[tuple[float, Callable[[], None], bool]] = []  # (due, cb, cancelled)

    def call_later(self, delay: float, cb: Callable[[], None]) -> _Handle:
        due = self.clock().timestamp() + delay
        idx = len(self.scheduled)
        self.scheduled.append((due, cb, False))
        return _Handle(self, idx)

    @property
    def pending_delays(self) -> list[float]:
        now = self.clock().timestamp()
        return [round(due - now, 3) for due, _, cancelled in self.scheduled if not cancelled]

    def fire_next(self) -> None:
        live = [(i, d) for i, (d, _, c) in enumerate(self.scheduled) if not c]
        assert live, "nothing scheduled"
        i, due = min(live, key=lambda x: x[1])
        _, cb, _ = self.scheduled[i]
        self.scheduled[i] = (due, cb, True)
        if due > self.clock().timestamp():
            self.clock.t = self.clock.t.__class__.fromtimestamp(due, tz=self.clock.t.tzinfo)
        cb()


class _Handle:
    def __init__(self, timers: FakeTimers, idx: int) -> None:
        self._timers, self._idx = timers, idx

    def cancel(self) -> None:
        due, cb, _ = self._timers.scheduled[self._idx]
        self._timers.scheduled[self._idx] = (due, cb, True)


class Runner:
    def __init__(self, *, block: bool = False) -> None:
        self.requests: list[RunRequest] = []
        self.block = block
        self.release = asyncio.Event()
        self.cancelled = 0

    async def __call__(self, request: RunRequest) -> RunResult:
        self.requests.append(request)
        if self.block:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        return RunResult(last_agent_turn_was_question=True)


def user(text: str) -> Inbound:
    return Inbound(origin="user", channel="text", delta=UserMessage(text=text))


def typing(active: bool, seconds: float = 0) -> Inbound:
    return Inbound(origin="user", channel="text", delta=Typing(active=active, seconds=seconds))


def make(clock: FakeClock, *, block: bool = False) -> tuple[TextHead, FakeTimers, Runner]:
    timers, runner = FakeTimers(clock), Runner(block=block)
    head = TextHead(runner=runner, timers=timers, clock=clock, policy=DebouncePolicy())
    return head, timers, runner


# ---- heuristics -------------------------------------------------------------


def test_completion_heuristics() -> None:
    assert looks_complete("what can you do?")
    assert looks_complete("ok.")
    assert not looks_complete("i was thinking")
    assert looks_fragment("i was thinking and")
    assert looks_fragment("so basically,")
    assert looks_fragment("hmm...")
    assert not looks_fragment("hey there")


# ---- text head ------------------------------------------------------------------


async def test_message_arms_quiet_window_then_runs(clock: FakeClock) -> None:
    head, timers, runner = make(clock)
    await head.start(user("hey there"))
    assert timers.pending_delays == [1.5]
    assert head.run is None
    timers.fire_next()
    assert head.run is not None
    await head.wait_idle()
    assert [i.delta.text for i in runner.requests[0].pending] == ["hey there"]  # type: ignore[union-attr]
    assert head.run is None


async def test_complete_sentence_commits_quickly(clock: FakeClock) -> None:
    head, timers, _ = make(clock)
    await head.start(user("what can you do?"))
    assert timers.pending_delays == [0.7]


async def test_fragment_extends(clock: FakeClock) -> None:
    head, timers, _ = make(clock)
    await head.start(user("so i was thinking and"))
    assert timers.pending_delays == [4.0]


async def test_burst_is_one_run_and_resets_timer(clock: FakeClock) -> None:
    head, timers, runner = make(clock)
    await head.start(user("one"))
    clock.advance(0.5)
    await head.apply("interrupt", user("two"))  # router says interrupt while a run... no run yet
    clock.advance(0.5)
    await head.apply("interrupt", user("three"))
    assert len(timers.pending_delays) == 1  # earlier timers cancelled
    timers.fire_next()
    await head.wait_idle()
    assert len(runner.requests) == 1
    assert [i.delta.text for i in runner.requests[0].pending] == ["one", "two", "three"]  # type: ignore[union-attr]


async def test_hard_cap_bounds_a_continuous_typer(clock: FakeClock) -> None:
    head, timers, _ = make(clock)
    await head.start(user("a"))
    for _ in range(4):
        clock.advance(1.75)
        await head.apply("interrupt", user("more"))
    # 5 messages (under the count cap), 7.0 s since the first; cap is 8.0 → 1.0 s left
    assert timers.pending_delays == [1.0]


async def test_max_messages_fires_immediately(clock: FakeClock) -> None:
    head, timers, _ = make(clock)
    await head.start(user("1"))
    for n in range(2, 7):
        await head.apply("interrupt", user(str(n)))
    assert timers.pending_delays == [0.0]


async def test_typing_extends_but_not_forever(clock: FakeClock) -> None:
    head, timers, _ = make(clock)
    await head.start(user("i think"))
    await head.apply("absorb", typing(True, 1))
    assert timers.pending_delays == [5.0]
    clock.advance(3.0)
    await head.apply("absorb", typing(True, 4))
    assert timers.pending_delays == [2.0]  # 5.0 past the last message, not past now
    await head.apply("absorb", typing(False))
    assert timers.pending_delays == [1.5]  # typing stopped: back to the quiet window


async def test_typing_without_pending_does_nothing(clock: FakeClock) -> None:
    head, timers, runner = make(clock)
    await head.start(typing(True, 2))
    assert timers.pending_delays == [] and head.run is None and runner.requests == []


async def test_interrupt_cancels_running_generation_and_reruns(clock: FakeClock) -> None:
    head, timers, runner = make(clock, block=True)
    await head.start(user("book me a dentist"))
    timers.fire_next()
    await asyncio.sleep(0)  # let the run task start
    assert head.run is not None
    await head.apply("interrupt", user("actually nvm"))
    await asyncio.sleep(0)
    assert runner.cancelled == 1
    assert head.run is None
    assert timers.pending_delays == [1.5]
    runner.block = False
    timers.fire_next()
    await head.wait_idle()
    assert [i.delta.text for i in runner.requests[1].pending] == ["actually nvm"]  # type: ignore[union-attr]


async def test_defer_runs_again_after_current_run(clock: FakeClock) -> None:
    head, timers, runner = make(clock, block=True)
    await head.start(user("hi"))
    timers.fire_next()
    await asyncio.sleep(0)
    gmail = Inbound(
        origin="google", channel="system", delta=GmailEvent(phase="connected", email="a@b.c")
    )
    await head.apply("defer", gmail)
    runner.release.set()
    await head.wait_idle()
    await asyncio.sleep(0)
    assert len(runner.requests) == 2
    assert runner.requests[1].trigger.delta.kind == "gmail"
    await head.wait_idle()


async def test_system_outcome_when_idle_runs_immediately(clock: FakeClock) -> None:
    head, timers, runner = make(clock)
    gmail = Inbound(
        origin="google", channel="system", delta=GmailEvent(phase="connected", email="a@b.c")
    )
    await head.start(gmail)
    assert timers.pending_delays == []
    assert head.run is not None
    await head.wait_idle()
    assert runner.requests[0].trigger.delta.kind == "gmail"


async def test_run_carries_last_question_flag(clock: FakeClock) -> None:
    head, timers, _ = make(clock)
    await head.start(user("hi"))
    timers.fire_next()
    await head.wait_idle()  # runner reports the agent asked a question
    await head.start(user("yes"))
    timers.fire_next()
    assert head.run is not None and head.run.last_agent_turn_was_question
    await head.wait_idle()


# ---- voice head -----------------------------------------------------------------


class Sink:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bool]] = []

    async def send(self, text: str, *, speak: bool) -> None:
        self.sent.append((text, speak))


async def test_voice_text_delta_is_a_silent_note(clock: FakeClock) -> None:
    sink = Sink()
    head = VoiceHead(sink=sink, clock=clock)
    await head.start(user("here's my email btw"))
    assert len(sink.sent) == 1
    text, speak = sink.sent[0]
    assert "texted" in text and not speak


async def test_voice_interrupt_speaks_absorb_is_silent(clock: FakeClock) -> None:
    sink = Sink()
    head = VoiceHead(sink=sink, clock=clock)
    head.on_agent_speaking()
    assert head.run is not None and head.run.inferred
    await head.apply("interrupt", typing(True, 2))
    await head.apply("absorb", typing(True, 3))
    assert [s for _, s in sink.sent] == [True, False]


async def test_voice_defer_waits_for_turn_boundary(clock: FakeClock) -> None:
    sink = Sink()
    head = VoiceHead(sink=sink, clock=clock)
    head.on_agent_speaking()
    gmail = Inbound(
        origin="google", channel="system", delta=GmailEvent(phase="connected", email="a@b.c")
    )
    await head.apply("defer", gmail)
    assert sink.sent == []
    await head.on_turn_complete(was_question=True)
    assert head.run is None
    assert len(sink.sent) == 1 and sink.sent[0][1] is True
    head.on_agent_speaking()
    assert head.run is not None and head.run.last_agent_turn_was_question


async def test_voice_delegation_marks_side_effect(clock: FakeClock) -> None:
    head = VoiceHead(sink=Sink(), clock=clock)
    head.on_agent_speaking()
    head.on_delegation(True)
    assert head.run is not None and head.run.side_effect_in_flight
    head.on_delegation(False)
    assert head.run is not None and not head.run.side_effect_in_flight
