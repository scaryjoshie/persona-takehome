"""Heads: one per medium. A head turns deltas into runs, and verbs into actions.

See docs/proposed-design/05-routing-and-decider.md (verbs per medium) and
06-turn-taking-policy.md (the text debounce). Both heads are pure control logic with
injected effects: the text head is given a `runner`, the voice head a `VoiceSink`.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol, assert_never

from app.core.store import Clock
from app.core.timers import TimerHandle, Timers
from app.core.types import (
    CallEvent,
    GmailEvent,
    Inbound,
    Medium,
    Run,
    Typing,
    UserMessage,
    Verb,
)

# ---------------------------------------------------------------------------
# Text head
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DebouncePolicy:
    """Seconds. Defaults from the turn-taking research (docs 06)."""

    quiet: float = 1.5  # after the last message, restart on each message
    quick_commit: float = 0.7  # if the buffer looks complete and nobody is typing
    fragment_extend: float = 4.0  # if the buffer ends in a fragment
    typing_extend_max: float = 5.0  # extend while typing, but not past this after last message
    hard_cap: float = 8.0  # from the first buffered message
    max_messages: int = 6


_COMPLETE = re.compile(r"[.?!]\s*$")
_FRAGMENT = re.compile(r"(\b(and|or|but|so|because|like|then)\s*$)|(,\s*$)|(\.\.\.\s*$)", re.I)


def looks_complete(text: str) -> bool:
    return bool(_COMPLETE.search(text))


def looks_fragment(text: str) -> bool:
    return bool(_FRAGMENT.search(text.rstrip()))


@dataclass(frozen=True)
class RunRequest:
    trigger: Inbound
    pending: tuple[Inbound, ...]


@dataclass(frozen=True)
class RunResult:
    last_agent_turn_was_question: bool = False


TextRunner = Callable[[RunRequest], Awaitable[RunResult]]


class TextHead:
    medium: Medium = "text"

    def __init__(
        self,
        *,
        runner: TextRunner,
        timers: Timers,
        clock: Clock,
        policy: DebouncePolicy | None = None,
    ) -> None:
        self._runner = runner
        self._timers = timers
        self._clock = clock
        self._policy = policy or DebouncePolicy()
        self._run: Run | None = None
        self._task: asyncio.Task[None] | None = None
        self._timer: TimerHandle | None = None
        self._pending: list[Inbound] = []
        self._first_pending_at: float | None = None  # monotonic seconds via clock
        self._last_message_at: float | None = None
        self._typing: bool = False
        self._deferred: Inbound | None = None
        self._last_question: bool = False

    # ---- state -------------------------------------------------------------

    @property
    def run(self) -> Run | None:
        return self._run

    @property
    def pending(self) -> tuple[Inbound, ...]:
        return tuple(self._pending)

    def mark_side_effect(self, in_flight: bool) -> None:
        if self._run is not None:
            self._run = self._run.model_copy(update={"side_effect_in_flight": in_flight})

    # ---- head protocol -----------------------------------------------------

    async def start(self, inbound: Inbound) -> None:
        match inbound.delta:
            case UserMessage():
                self._buffer(inbound)
                self._arm()
            case Typing(active=active):
                self._typing = active
                if self._pending:
                    self._arm()
            case CallEvent() | GmailEvent():
                # System outcomes are not "typing"; respond now.
                self._buffer(inbound)
                self._fire(inbound)
            case _:
                assert_never(inbound.delta)

    async def apply(self, verb: Verb, inbound: Inbound) -> None:
        match verb:
            case "interrupt":
                self._cancel_run()
                self._buffer(inbound)
                if isinstance(inbound.delta, UserMessage):
                    self._arm()
                else:
                    self._fire(inbound)
            case "absorb":
                match inbound.delta:
                    case Typing(active=active):
                        self._typing = active
                        if self._pending:
                            self._arm()
                    case _:
                        pass  # context only; the next run sees it in the store
            case "defer":
                self._deferred = inbound
            case _:
                assert_never(verb)

    # ---- internals ---------------------------------------------------------

    def _t(self) -> float:
        return self._clock().timestamp()

    def _buffer(self, inbound: Inbound) -> None:
        now = self._t()
        self._pending.append(inbound)
        if self._first_pending_at is None:
            self._first_pending_at = now
        if isinstance(inbound.delta, UserMessage):
            self._last_message_at = now

    def _delay(self) -> float:
        """How long to wait from now before firing, per the debounce policy."""
        p = self._policy
        now = self._t()
        assert self._first_pending_at is not None
        if len(self._pending) >= p.max_messages:
            return 0.0
        cap_left = max(0.0, self._first_pending_at + p.hard_cap - now)
        text = " ".join(
            i.delta.text for i in self._pending if isinstance(i.delta, UserMessage)
        ).strip()
        if self._typing:
            since_last = now - (self._last_message_at or now)
            want = max(0.0, p.typing_extend_max - since_last)
        elif text and looks_complete(text):
            want = p.quick_commit
        elif text and looks_fragment(text):
            want = p.fragment_extend
        else:
            want = p.quiet
        return min(want, cap_left)

    def _arm(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        trigger = self._pending[-1]
        self._timer = self._timers.call_later(self._delay(), lambda: self._fire(trigger))

    def _fire(self, trigger: Inbound) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._task is not None and not self._task.done():
            return  # a run is already going; it will re-arm on completion if pending
        request = RunRequest(trigger=trigger, pending=tuple(self._pending))
        self._pending.clear()
        self._first_pending_at = None
        self._run = Run(
            medium="text",
            started=self._clock(),
            last_agent_turn_was_question=self._last_question,
        )
        self._task = asyncio.create_task(self._execute(request))

    async def _execute(self, request: RunRequest) -> None:
        try:
            result = await self._runner(request)
            self._last_question = result.last_agent_turn_was_question
        except asyncio.CancelledError:
            raise
        finally:
            self._run = None
            self._task = None
            self._after_run()

    def _after_run(self) -> None:
        deferred, self._deferred = self._deferred, None
        if deferred is not None:
            self._buffer(deferred)
            self._fire(deferred)
        elif self._pending:
            self._arm()

    def _cancel_run(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None
        self._run = None

    async def wait_idle(self) -> None:
        """Test helper: wait for the current run task to finish."""
        task = self._task
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass


# ---------------------------------------------------------------------------
# Voice head
# ---------------------------------------------------------------------------


class VoiceSink(Protocol):
    """What the voice head can do to the live session. Implemented by the voice handler."""

    async def send(self, text: str, *, speak: bool) -> None: ...


NoteKind = Literal["speak", "silent"]


def render_note(inbound: Inbound) -> tuple[str, NoteKind] | None:
    """How a delta appears to the speaking model. None means nothing to inject."""
    delta = inbound.delta
    match delta:
        case UserMessage(text=text):
            return (
                f"Internal note: the user just texted: {text!r}. Work it in naturally.",
                "silent",
            )
        case Typing():
            if not delta.active:
                return None
            return (
                f"Internal note: the user has been typing a reply for {delta.seconds:.0f}s. "
                "If you asked them something, invite them to finish typing and wait.",
                "silent",
            )
        case GmailEvent():
            if delta.phase == "connected":
                return (f"Tell the user their Gmail ({delta.email}) is now connected.", "speak")
            if delta.phase == "link_sent":
                return ("Internal note: the Gmail link was just texted to the user.", "silent")
            if delta.phase == "skipped":
                return ("Internal note: the user skipped Gmail. Do not ask again.", "silent")
            return (
                "Tell the user the Gmail connection didn't go through and offer to retry.",
                "speak",
            )
        case CallEvent():
            return None  # call state changes are handled by the call manager, not spoken
        case _:
            assert_never(delta)


class VoiceHead:
    """On GPT-Live there is no cancel: interrupt = speak now, absorb = silent note,
    defer = wait for the inferred turn boundary, then speak. The run is inferred and
    is fed by the voice handler via on_agent_speaking / on_turn_complete."""

    medium: Medium = "voice"

    def __init__(self, *, sink: VoiceSink, clock: Clock) -> None:
        self._sink = sink
        self._clock = clock
        self._run: Run | None = None
        self._deferred: list[Inbound] = []
        self._last_question: bool = False

    @property
    def run(self) -> Run | None:
        return self._run

    # ---- fed by the voice handler -----------------------------------------

    def on_agent_speaking(self) -> None:
        if self._run is None:
            self._run = Run(
                medium="voice",
                started=self._clock(),
                inferred=True,
                last_agent_turn_was_question=self._last_question,
            )

    def on_delegation(self, in_flight: bool) -> None:
        if self._run is not None:
            self._run = self._run.model_copy(update={"side_effect_in_flight": in_flight})

    async def on_turn_complete(self, *, was_question: bool = False) -> None:
        self._run = None
        self._last_question = was_question
        deferred, self._deferred = self._deferred, []
        for inbound in deferred:
            await self._inject(inbound, force_speak=True)

    # ---- head protocol -----------------------------------------------------

    async def start(self, inbound: Inbound) -> None:
        await self._inject(inbound)

    async def apply(self, verb: Verb, inbound: Inbound) -> None:
        match verb:
            case "interrupt":
                await self._inject(inbound, force_speak=True)
            case "absorb":
                await self._inject(inbound, force_silent=True)
            case "defer":
                self._deferred.append(inbound)
            case _:
                assert_never(verb)

    async def _inject(
        self, inbound: Inbound, *, force_speak: bool = False, force_silent: bool = False
    ) -> None:
        rendered = render_note(inbound)
        if rendered is None:
            return
        text, kind = rendered
        speak = (kind == "speak" or force_speak) and not force_silent
        await self._sink.send(text, speak=speak)
