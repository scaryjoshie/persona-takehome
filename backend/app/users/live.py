"""Per-user live: only the things that can live nowhere but in a process.

A lock so a user's events are handled one at a time, the responders (which hold the text
debounce timer and the running task), the live voice session, and the sockets to push to.
No data. If the process restarts, all of this is gone, and that is correct.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from typing import Protocol

from app.events.event import Event
from app.routing.responder import Responder
from app.routing.types import Medium

Subscriber = Callable[[Event], Awaitable[None] | None]


class VoiceSession(Protocol):
    async def send(self, text: str, *, speak: bool) -> None: ...
    async def close(self) -> None: ...


class LiveUser:
    def __init__(self, phone: str, responders: dict[Medium, Responder]) -> None:
        self.phone = phone
        self.lock = asyncio.Lock()  # not reentrant: never submit while holding it
        self.responders = responders
        self.voice: VoiceSession | None = None
        self._subs: list[tuple[frozenset[str] | None, Subscriber]] = []

    def subscribe(
        self, fn: Subscriber, *, kinds: Iterable[str] | None = None
    ) -> Callable[[], None]:
        entry = (frozenset(kinds) if kinds is not None else None, fn)
        self._subs.append(entry)

        def unsubscribe() -> None:
            if entry in self._subs:
                self._subs.remove(entry)

        return unsubscribe

    async def publish(self, event: Event) -> None:
        for kinds, fn in list(self._subs):
            if kinds is None or event.kind in kinds:
                result = fn(event)
                if asyncio.iscoroutine(result):
                    await result


class LiveUsers:
    """Registry: one live per active user, built on first contact."""

    def __init__(self, factory: Callable[[str], LiveUser]) -> None:
        self._factory = factory
        self._by_phone: dict[str, LiveUser] = {}

    def get(self, phone: str) -> LiveUser:
        rt = self._by_phone.get(phone)
        if rt is None:
            rt = self._by_phone[phone] = self._factory(phone)
        return rt

    def drop(self, phone: str) -> None:
        self._by_phone.pop(phone, None)
