"""The text responder: when the debounce says now, run one reply; handle events that
arrive while a reply is running."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.events.event import Event
from app.routing.responder import Responder
from app.routing.types import Medium, Run, Verb
from app.text.debounce import Debounce, DebouncePolicy
from app.text.events import Typing, UserMessage
from app.timers import Clock, TimerHandle, Timers


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
        self._debounce = Debounce(clock, policy)
        self._run: Run | None = None
        self._task: asyncio.Task[None] | None = None
        self._timer: TimerHandle | None = None
        self._deferred: Event | None = None
        self._asked_question = False

    @property
    def run(self) -> Run | None:
        return self._run

    @property
    def task(self) -> asyncio.Task[None] | None:
        """The reply in progress, for callers that need to await it (tests do)."""
        return self._task

    # ---- responder --------------------------------------------------------------

    async def start(self, event: Event) -> None:
        match event.payload:
            case UserMessage():
                self._debounce.add(event)
                self._arm()
            case Typing(active=active):
                self._debounce.typing = active
                if self._debounce:
                    self._arm()
            case _:  # system outcomes (call ended, gmail connected) are answered now
                self._debounce.add(event)
                self._reply(event)

    async def apply(self, verb: Verb, event: Event) -> None:
        match verb:
            case Verb.INTERRUPT:
                self._cancel()
                self._debounce.add(event)
                if isinstance(event.payload, UserMessage):
                    self._arm()
                else:
                    self._reply(event)
            case Verb.ABSORB:
                if isinstance(event.payload, Typing):
                    self._debounce.typing = event.payload.active
                    if self._debounce:
                        self._arm()
            case Verb.DEFER:
                self._deferred = event
            case Verb.START:
                raise ValueError("START is not a verb a responder applies")

    # ---- reply lifecycle ------------------------------------------------------------

    def _arm(self) -> None:
        """(Re)schedule a reply for when the debounce says so."""
        if self._timer:
            self._timer.cancel()
        trigger = self._debounce.latest
        self._timer = self._timers.call_later(self._debounce.delay(), lambda: self._reply(trigger))

    def _reply(self, trigger: Event) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if self._reply_in_progress():
            return  # _after picks the buffer up when the current reply ends
        request = RunRequest(trigger=trigger, buffered=self._debounce.take())
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
            self._debounce.add(deferred)
            self._reply(deferred)
        elif self._debounce:
            self._arm()

    def _reply_in_progress(self) -> bool:
        return self._task is not None and not self._task.done()

    def _cancel(self) -> None:
        if self._reply_in_progress():
            assert self._task is not None
            self._task.cancel()
        self._task = None
        self._run = None
