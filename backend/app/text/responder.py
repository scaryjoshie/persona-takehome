"""The text responder: buffers messages, waits for the user to finish, runs the agent.

Timing only, no judgment (docs 06): a quiet window after the last message, extended
while the user is typing, bounded by a hard cap on time and on message count.
Whether a buffer "looks finished" is a decider question, not a rule here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.events.event import Event
from app.routing.responder import Responder
from app.routing.router import Clock
from app.routing.types import Medium, Run, Verb
from app.text.events import Typing, UserMessage
from app.timers import TimerHandle, Timers


@dataclass(frozen=True)
class DebouncePolicy:
    quiet: float = 1.5  # seconds after the last message
    typing_extend_max: float = 5.0  # while typing, wait at most this long past the last message
    hard_cap: float = 8.0  # from the first buffered message
    max_messages: int = 6


@dataclass(frozen=True)
class RunRequest:
    trigger: Event
    buffered: tuple[Event, ...]


@dataclass(frozen=True)
class RunResult:
    asked_question: bool = False


Runner = Callable[[RunRequest], Awaitable[RunResult]]


class TextResponder(Responder):
    medium: Medium = Medium.TEXT

    def __init__(
        self, *, runner: Runner, timers: Timers, clock: Clock, policy: DebouncePolicy | None = None
    ) -> None:
        self._runner = runner
        self._timers = timers
        self._clock = clock
        self._policy = policy or DebouncePolicy()
        self._run: Run | None = None
        self._task: asyncio.Task[None] | None = None
        self._timer: TimerHandle | None = None
        self._buffer: list[Event] = []
        self._first_at: float | None = None
        self._last_message_at: float | None = None
        self._typing = False
        self._deferred: Event | None = None
        self._asked_question = False

    @property
    def run(self) -> Run | None:
        return self._run

    @property
    def buffered(self) -> tuple[Event, ...]:
        return tuple(self._buffer)

    def mark_side_effect(self, in_flight: bool) -> None:
        if self._run:
            self._run = self._run.model_copy(update={"side_effect_in_flight": in_flight})

    # ---- responder protocol ---------------------------------------------------

    async def start(self, event: Event) -> None:
        match event.payload:
            case UserMessage():
                self._add(event)
                self._arm()
            case Typing(active=active):
                self._typing = active
                if self._buffer:
                    self._arm()
            case _:  # system outcomes (call ended, gmail connected) are answered now
                self._add(event)
                self._fire(event)

    async def apply(self, verb: Verb, event: Event) -> None:
        match verb:
            case Verb.INTERRUPT:
                self._cancel()
                self._add(event)
                if isinstance(event.payload, UserMessage):
                    self._arm()
                else:
                    self._fire(event)
            case Verb.ABSORB:
                if isinstance(event.payload, Typing):
                    self._typing = event.payload.active
                    if self._buffer:
                        self._arm()
            case Verb.DEFER:
                self._deferred = event
            case Verb.START:
                raise ValueError("START is not a verb a responder applies")

    # ---- timing --------------------------------------------------------------

    def _now(self) -> float:
        return self._clock().timestamp()

    def _add(self, event: Event) -> None:
        now = self._now()
        self._buffer.append(event)
        self._first_at = self._first_at if self._first_at is not None else now
        if isinstance(event.payload, UserMessage):
            self._last_message_at = now

    def _delay(self) -> float:
        p, now = self._policy, self._now()
        assert self._first_at is not None
        if len(self._buffer) >= p.max_messages:
            return 0.0
        cap_left = max(0.0, self._first_at + p.hard_cap - now)
        if self._typing:
            since_last = now - (self._last_message_at if self._last_message_at else now)
            return min(max(0.0, p.typing_extend_max - since_last), cap_left)
        return min(p.quiet, cap_left)

    def _arm(self) -> None:
        if self._timer:
            self._timer.cancel()
        trigger = self._buffer[-1]
        self._timer = self._timers.call_later(self._delay(), lambda: self._fire(trigger))

    def _fire(self, trigger: Event) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if self._task and not self._task.done():
            return  # the running task re-arms on completion if anything is buffered
        request = RunRequest(trigger=trigger, buffered=tuple(self._buffer))
        self._buffer.clear()
        self._first_at = None
        self._run = Run(
            medium=Medium.TEXT,
            started=self._clock(),
            last_agent_turn_was_question=self._asked_question,
        )
        self._task = asyncio.create_task(self._execute(request))

    async def _execute(self, request: RunRequest) -> None:
        try:
            result = await self._runner(request)
            self._asked_question = result.asked_question
        finally:
            self._run = None
            self._task = None
            self._after()

    def _after(self) -> None:
        deferred, self._deferred = self._deferred, None
        if deferred:
            self._add(deferred)
            self._fire(deferred)
        elif self._buffer:
            self._arm()

    def _cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self._run = None

    async def wait_idle(self) -> None:
        """Test helper."""
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
