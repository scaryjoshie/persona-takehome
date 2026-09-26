"""The pipeline: every event goes through `submit`.

    1. Save it, first applying it to the user's state if it is a kind that changes state.
    2. Publish it to whoever is subscribed (the browser sockets).
    3. If it routes, hand it to the medium that has the floor (voice during a call, text
       otherwise). The medium decides what to do; its decision is logged.

Media talk back only through the context they are handed: record another event now, or
submit one later. Nothing leaves this chain.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import TypeAdapter
from sqlmodel.ext.asyncio.session import AsyncSession

from app.agent.events import Graduated, SlotChanged
from app.database import SessionFactory
from app.events import service as events
from app.events.decision import Decision
from app.events.event import Event
from app.events.payload import Channel, Origin, Payload
from app.gmail.events import GmailEvent, GmailPhase
from app.text.events import Typing
from app.timers import Clock, Timers
from app.users import service as users
from app.users.user import Medium, User
from app.voice.call_events import CallEvent
from app.voice.call_state import next_state

log = logging.getLogger(__name__)

RECENT = 40  # events a medium sees when handling one
Subscriber = Callable[[Event], Awaitable[None] | None]


class Responder(Protocol):
    """A medium: text or voice. Handles an event routed to it; says what it did."""

    async def handle(self, event: Event, user: User, ctx: Context) -> Decision | None: ...


@dataclass
class Context:
    """What a medium can do while handling an event."""

    pipeline: Pipeline
    phone: str
    recent: list[Event]

    async def record(self, origin: Origin, channel: Channel, payload: Payload) -> Event | None:
        """Save and publish an event now, without routing it."""
        return await self.pipeline._save_and_publish(self.phone, origin, channel, payload)  # pyright: ignore[reportPrivateUsage]

    def later(self, seconds: float, origin: Origin, channel: Channel, payload: Payload) -> None:
        """Submit an event after a delay (the one timer the system uses)."""
        self.pipeline.later(seconds, self.phone, origin, channel, payload)


class Pipeline:
    def __init__(
        self,
        db: SessionFactory,
        *,
        payloads: TypeAdapter[Payload],
        clock: Clock,
        timers: Timers,
    ) -> None:
        self.responders: dict[Medium, Responder] = {}
        self._db = db
        self._payloads = payloads
        self._clock = clock
        self._timers = timers
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._subscribers: dict[str, list[tuple[frozenset[str] | None, Subscriber]]] = defaultdict(
            list
        )
        self._background: set[asyncio.Task[object]] = set()

    # ---- reads ----------------------------------------------------------------

    async def user(self, phone: str) -> User:
        async with self._db() as s, s.begin():
            return await users.ensure_user(s, phone, now=self._clock())

    async def history(self, phone: str, *, limit: int | None = None) -> list[Event]:
        async with self._db() as s:
            return await events.list_events(s, phone, payloads=self._payloads, limit=limit)

    def now(self) -> datetime:
        return self._clock()

    # ---- the one door -----------------------------------------------------------

    async def submit(
        self,
        phone: str,
        origin: Origin,
        channel: Channel,
        payload: Payload,
        *,
        route: bool | None = None,
    ) -> Event | None:
        """Save, publish, route. Returns None if the event changed nothing and was dropped."""
        async with self._locks[phone]:
            event = await self._save_and_publish(phone, origin, channel, payload)
            if event is None:
                return None
            if route if route is not None else payload.should_route():
                await self._route(phone, event)
            return event

    def later(
        self, seconds: float, phone: str, origin: Origin, channel: Channel, payload: Payload
    ) -> None:
        def fire() -> None:
            self.spawn(self.submit(phone, origin, channel, payload))

        self._timers.call_later(seconds, fire)

    def spawn(self, work: Awaitable[object]) -> None:
        """Run work in the background, keeping a reference and logging failures."""

        async def run() -> object:
            try:
                return await work
            except Exception:
                log.exception("background work failed")
                return None

        task = asyncio.ensure_future(run())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def subscribe(
        self, phone: str, fn: Subscriber, *, kinds: Iterable[str] | None = None
    ) -> Callable[[], None]:
        entry = (frozenset(kinds) if kinds is not None else None, fn)
        self._subscribers[phone].append(entry)

        def unsubscribe() -> None:
            if entry in self._subscribers[phone]:
                self._subscribers[phone].remove(entry)

        return unsubscribe

    async def reset(self, phone: str) -> None:
        """Debug: forget everything about a user."""
        async with self._locks[phone], self._db() as s, s.begin():
            await events.delete_events(s, phone)
            await users.delete_user(s, phone)

    # ---- steps --------------------------------------------------------------------

    async def _save_and_publish(
        self, phone: str, origin: Origin, channel: Channel, payload: Payload
    ) -> Event | None:
        event = await self._save(phone, origin, channel, payload)
        if event is not None and payload.persists:
            await self._publish(phone, event)
        return event

    async def _save(
        self, phone: str, origin: Origin, channel: Channel, payload: Payload
    ) -> Event | None:
        now = self._clock()
        async with self._db() as s, s.begin():
            user = await users.ensure_user(s, phone, now=now)
            applied = await _apply(s, user, payload, now)
            if applied is None:
                log.info("%s: dropped %s (no change or invalid)", phone, payload.kind_name)
                return None
            if not applied.persists:
                return Event(seq=0, ts=now, origin=origin, channel=channel, payload=applied)
            return await events.append(s, phone, origin, channel, applied, ts=now)

    async def _publish(self, phone: str, event: Event) -> None:
        for kinds, fn in list(self._subscribers[phone]):
            if kinds is None or event.kind in kinds:
                result = fn(event)
                if asyncio.iscoroutine(result):
                    await result

    async def _route(self, phone: str, event: Event) -> None:
        async with self._db() as s:
            user = await users.get_user(s, phone)
            recent = await events.list_events(s, phone, payloads=self._payloads, limit=RECENT)
        assert user is not None
        responder = self.responders[user.floor]
        decision = await responder.handle(event, user, Context(self, phone, recent))
        if decision is not None:
            await self._save_and_publish(phone, Origin.SYSTEM, Channel.SYSTEM, decision)


async def _apply(s: AsyncSession, user: User, payload: Payload, now: datetime) -> Payload | None:
    """The events that change user state, and how. Returns the event to record (possibly
    filled in), or None to drop it: an impossible call transition, a slot set to the value
    it already has, a second "link sent", or a second graduation."""
    match payload:
        case CallEvent():
            call = next_state(user.call, payload, now)
            if call is None:
                return None
            await users.set_call(s, user.phone, call)
        case SlotChanged(slot=slot, new=new):
            old: str | None = getattr(user.slots, slot)
            if old == new:
                return None
            await users.set_slots(s, user.phone, **{slot: new})
            return payload.model_copy(update={"old": old})
        case GmailEvent(phase=phase, email=email):
            already = (GmailPhase.LINK_SENT, GmailPhase.CONNECTED)
            if phase is GmailPhase.LINK_SENT and user.slots.gmail in already:
                return None  # the link goes out once
            email = email or user.slots.gmail_email
            await users.set_slots(s, user.phone, gmail=phase, gmail_email=email)
        case Graduated():
            if user.slots.graduated:
                return None
            await users.set_slots(s, user.phone, graduated=True)
        case Typing(active=active):
            await users.set_typing(s, user.phone, now if active else None)
        case _:
            pass
    return payload
