"""The in-process part of a user: only what can live nowhere but in a running process.

A lock so a user's events are handled one at a time, the responders (which hold the text
debounce timer and the running reply), the voice session while a call is up, and the
browser sockets to push to. No data: that is all in SQLite. A restart loses exactly this,
which is correct, since the sockets and the call died with the process.
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
    def __init__(self, phone: str) -> None:
        self.phone = phone
        self.lock = asyncio.Lock()  # not reentrant: never submit while holding it
        self.responders: dict[Medium, Responder] = {}
        self.voice: VoiceSession | None = None
        self._subs: list[tuple[frozenset[str] | None, Subscriber]] = []

    async def send_to_call(self, text: str, *, speak: bool) -> None:
        """Pass a note into the call, if one is up. The voice responder's output."""
        if self.voice is not None:
            await self.voice.send(text, speak=speak)

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
    """One LiveUser per phone, built on first contact."""

    def __init__(self, factory: Callable[[str], LiveUser]) -> None:
        self._factory = factory
        self._by_phone: dict[str, LiveUser] = {}

    def get(self, phone: str) -> LiveUser:
        live = self._by_phone.get(phone)
        if live is None:
            live = self._by_phone[phone] = self._factory(phone)
        return live
