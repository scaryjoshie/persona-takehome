"""When to reply to a stream of texts. Timing only, no judgment (docs 06).

People send several messages in a row and start typing and stop. This waits for a quiet
window after the last message, extends it while the user is typing, and caps the wait
on both time since the first buffered message and message count. Whether a buffer
"looks finished" is a decider question, not a rule here.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.events.event import Event
from app.text.events import UserMessage
from app.timers import Clock


@dataclass(frozen=True)
class DebouncePolicy:
    quiet: float = 1.5  # seconds after the last message
    typing_extend_max: float = 5.0  # while typing, wait at most this long past the last message
    hard_cap: float = 8.0  # from the first buffered message
    max_messages: int = 6


class Debounce:
    """Holds the events waiting for a reply and says how long to wait before sending them."""

    def __init__(self, clock: Clock, policy: DebouncePolicy | None = None) -> None:
        self._clock = clock
        self._policy = policy or DebouncePolicy()
        self._buffer: list[Event] = []
        self._first_at: float | None = None
        self._last_message_at: float | None = None
        self.typing = False

    def __bool__(self) -> bool:
        return bool(self._buffer)

    @property
    def latest(self) -> Event:
        return self._buffer[-1]

    def add(self, event: Event) -> None:
        now = self._clock().timestamp()
        self._buffer.append(event)
        if self._first_at is None:
            self._first_at = now
        if isinstance(event.payload, UserMessage):
            self._last_message_at = now

    def take(self) -> tuple[Event, ...]:
        """Empty the buffer and return what was in it."""
        events, self._buffer = tuple(self._buffer), []
        self._first_at = None
        return events

    def delay(self) -> float:
        """Seconds to wait from now before replying to what is buffered."""
        p, now = self._policy, self._clock().timestamp()
        assert self._first_at is not None, "nothing buffered"
        if len(self._buffer) >= p.max_messages:
            return 0.0
        cap_left = max(0.0, self._first_at + p.hard_cap - now)
        if self.typing:
            since_last = now - (self._last_message_at if self._last_message_at else now)
            return min(max(0.0, p.typing_extend_max - since_last), cap_left)
        return min(p.quiet, cap_left)
