from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from app.events.base import Channel, Origin
from app.events.envelope import Event
from app.events.store import Store
from app.routing.types import Medium, Run
from app.text.driver import RunRequest, RunResult
from app.text.types import Typing, UserMessage
from app.user import User


class FakeClock:
    def __init__(self) -> None:
        self.t = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)

    def set_ts(self, ts: float) -> None:
        self.t = datetime.fromtimestamp(ts, tz=UTC)


class FakeTimers:
    """Records timers; tests fire them explicitly and the clock jumps to the due time."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self._entries: list[tuple[float, Callable[[], None], bool]] = []

    def call_later(self, delay: float, cb: Callable[[], None]) -> _Handle:
        self._entries.append((self.clock().timestamp() + delay, cb, False))
        return _Handle(self, len(self._entries) - 1)

    def _cancel(self, idx: int) -> None:
        due, cb, _ = self._entries[idx]
        self._entries[idx] = (due, cb, True)

    @property
    def pending(self) -> list[float]:
        now = self.clock().timestamp()
        return [round(due - now, 3) for due, _, cancelled in self._entries if not cancelled]

    def fire_next(self) -> None:
        live = [(i, due) for i, (due, _, c) in enumerate(self._entries) if not c]
        assert live, "nothing scheduled"
        i, due = min(live, key=lambda x: x[1])
        cb = self._entries[i][1]
        self._cancel(i)
        if due > self.clock().timestamp():
            self.clock.set_ts(due)
        cb()


class _Handle:
    def __init__(self, timers: FakeTimers, idx: int) -> None:
        self._timers, self._idx = timers, idx

    def cancel(self) -> None:
        self._timers._cancel(self._idx)  # pyright: ignore[reportPrivateUsage]


class FakeRunner:
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
        return RunResult(asked_question=True)


class FakeDriver:
    def __init__(self, medium: Medium, clock: FakeClock) -> None:
        self.medium = medium
        self._clock = clock
        self._run: Run | None = None
        self.log: list[tuple[str, str]] = []

    @property
    def run(self) -> Run | None:
        return self._run

    def begin(self, *, side_effect: bool = False, question: bool = False) -> None:
        self._run = Run(
            medium=self.medium,
            started=self._clock(),
            side_effect_in_flight=side_effect,
            last_agent_turn_was_question=question,
        )

    async def start(self, event: Event) -> None:
        self.log.append(("start", event.kind))
        self.begin()

    async def apply(self, verb: object, event: Event) -> None:
        self.log.append((str(verb), event.kind))


def user_text(store: Store, text: str) -> Event:
    return store.transient(Origin.USER, Channel.TEXT, UserMessage(text=text))


def typing(store: Store, active: bool, seconds: float = 0) -> Event:
    return store.transient(Origin.USER, Channel.TEXT, Typing(active=active, seconds=seconds))


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(clock: FakeClock) -> Store:
    return Store("+15550001111", clock=clock)


@pytest.fixture
def user(store: Store) -> User:
    return User(store)
