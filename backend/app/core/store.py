"""Per-user append-only event log plus slots, call state, and floor.

Pure in-memory. Persistence is a sink callback so the core never imports the DB.
See docs/proposed-design/03-architecture.md ("store before route") and 08-storage.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import Any, Protocol

from app.core.types import (
    CallState,
    Channel,
    Event,
    Floor,
    Origin,
    Payload,
    SlotChanged,
    Slots,
)

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


class Sink(Protocol):
    """Write-through target. Called after each in-memory mutation."""

    async def on_event(self, phone: str, event: Event) -> None: ...
    async def on_state(self, phone: str, slots: Slots, call: CallState, floor: Floor) -> None: ...


class NullSink:
    async def on_event(self, phone: str, event: Event) -> None:
        return None

    async def on_state(self, phone: str, slots: Slots, call: CallState, floor: Floor) -> None:
        return None


Subscriber = Callable[[Event], Awaitable[None] | None]


class Store:
    """One user's log and state. Not thread-safe by design: the actor serializes access."""

    def __init__(
        self,
        phone: str,
        *,
        clock: Clock = utc_now,
        sink: Sink | None = None,
        events: Iterable[Event] = (),
        slots: Slots | None = None,
        call: CallState | None = None,
        floor: Floor = "text",
    ) -> None:
        self.phone = phone
        self._clock = clock
        self._sink: Sink = sink or NullSink()
        self._events: list[Event] = sorted(events, key=lambda e: e.seq)
        self._seq = self._events[-1].seq if self._events else 0
        self.slots: Slots = slots or Slots()
        self.call: CallState = call or CallState()
        self.floor: Floor = floor
        self._subs: list[tuple[frozenset[str] | None, Subscriber]] = []

    # ---- reading -----------------------------------------------------------

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def recent(self, n: int) -> tuple[Event, ...]:
        return tuple(self._events[-n:]) if n > 0 else ()

    def last_seq(self) -> int:
        return self._seq

    def now(self) -> datetime:
        return self._clock()

    # ---- writing -----------------------------------------------------------

    async def append(self, origin: Origin, channel: Channel, payload: Payload) -> Event:
        self._seq += 1
        event = Event(
            seq=self._seq, ts=self._clock(), origin=origin, channel=channel, payload=payload
        )
        self._events.append(event)
        await self._sink.on_event(self.phone, event)
        await self._notify(event)
        return event

    async def set_slot(self, slot: str, value: Any, *, origin: Origin, channel: Channel) -> bool:
        """Idempotent. Returns True if the value changed. Logs a slot_changed event when it does."""
        old = getattr(self.slots, slot)
        if old == value:
            return False
        setattr(self.slots, slot, value)
        await self._persist_state()
        await self.append(origin, channel, SlotChanged(slot=slot, old=old, new=value))
        return True

    async def set_call(self, call: CallState) -> None:
        self.call = call
        await self._persist_state()

    async def set_floor(self, floor: Floor) -> None:
        if floor != self.floor:
            self.floor = floor
            await self._persist_state()

    async def _persist_state(self) -> None:
        await self._sink.on_state(self.phone, self.slots, self.call, self.floor)

    # ---- subscriptions -----------------------------------------------------

    def subscribe(
        self, fn: Subscriber, *, kinds: Iterable[str] | None = None
    ) -> Callable[[], None]:
        """Subscribe to appended events, optionally filtered by kind. Returns unsubscribe."""
        entry = (frozenset(kinds) if kinds is not None else None, fn)
        self._subs.append(entry)

        def unsubscribe() -> None:
            if entry in self._subs:
                self._subs.remove(entry)

        return unsubscribe

    async def _notify(self, event: Event) -> None:
        for kinds, fn in list(self._subs):
            if kinds is not None and event.kind not in kinds:
                continue
            result = fn(event)
            if asyncio.iscoroutine(result):
                await result
