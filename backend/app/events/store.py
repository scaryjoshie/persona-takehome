"""Per-user append-only log. Pure in-memory; persistence is a sink callback."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel

from app.events.base import Channel, Origin, Payload
from app.events.envelope import Event

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


class Sink(Protocol):
    async def on_event(self, phone: str, event: Event) -> None: ...
    async def on_state(self, phone: str, state: BaseModel) -> None: ...


class NullSink:
    async def on_event(self, phone: str, event: Event) -> None:
        return None

    async def on_state(self, phone: str, state: BaseModel) -> None:
        return None


Subscriber = Callable[[Event], Awaitable[None] | None]


class Store:
    """The log plus a subscription list. Sections keep their own state objects and
    call `save_state` when they change; the store only knows how to persist them."""

    def __init__(
        self,
        phone: str,
        *,
        clock: Clock = utc_now,
        sink: Sink | None = None,
        events: Iterable[Event] = (),
    ) -> None:
        self.phone = phone
        self._clock = clock
        self._sink: Sink = sink or NullSink()
        self._events: list[Event] = sorted(events, key=lambda e: e.seq)
        self._seq = self._events[-1].seq if self._events else 0
        self._subs: list[tuple[frozenset[str] | None, Subscriber]] = []

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def recent(self, n: int) -> tuple[Event, ...]:
        return tuple(self._events[-n:]) if n > 0 else ()

    def last_seq(self) -> int:
        return self._seq

    def now(self) -> datetime:
        return self._clock()

    async def append(self, origin: Origin, channel: Channel, payload: Payload) -> Event:
        """Persist and publish. For transient payloads, see `transient`."""
        self._seq += 1
        event = Event(
            seq=self._seq, ts=self._clock(), origin=origin, channel=channel, payload=payload
        )
        self._events.append(event)
        await self._sink.on_event(self.phone, event)
        await self._publish(event)
        return event

    def transient(self, origin: Origin, channel: Channel, payload: Payload) -> Event:
        """An event that exists only to be routed. Not logged, not published."""
        return Event(seq=0, ts=self._clock(), origin=origin, channel=channel, payload=payload)

    async def save_state(self, state: BaseModel) -> None:
        await self._sink.on_state(self.phone, state)

    def subscribe(
        self, fn: Subscriber, *, kinds: Iterable[str] | None = None
    ) -> Callable[[], None]:
        entry = (frozenset(kinds) if kinds is not None else None, fn)
        self._subs.append(entry)

        def unsubscribe() -> None:
            if entry in self._subs:
                self._subs.remove(entry)

        return unsubscribe

    async def _publish(self, event: Event) -> None:
        for kinds, fn in list(self._subs):
            if kinds is None or event.kind in kinds:
                result = fn(event)
                if asyncio.iscoroutine(result):
                    await result
