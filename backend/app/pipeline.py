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
from datetime import datetime, tzinfo
from typing import Protocol

from sqlalchemy import delete
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession

from app.agent.events import (
    CallOptOut,
    ContactSaved,
    Graduated,
    SlotChanged,
    StepSetAside,
    TimezoneLearned,
)
from app.agent.slots import TzSource
from app.database import SessionFactory
from app.events import service as events
from app.events.decision import Decision
from app.events.event import Event
from app.events.payload import Channel, Origin, Payload
from app.google.events import GmailEvent, GmailPhase
from app.jobs.models import JobRow
from app.memory import service as memories
from app.memory.events import Forgot, Remembered
from app.memory.service import Fact, Memory, Summary
from app.text.events import Typing
from app.timers import Clock, Timers
from app.users import service as users
from app.users.user import Medium, User
from app.voice.call_state import CallEvent, CallPhase, CallTransition, next_state

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
    tz: tzinfo  # theirs, for showing times

    async def record(self, origin: Origin, channel: Channel, payload: Payload) -> Event | None:
        """Save and publish an event now, without routing it."""
        return await self.pipeline.record(self.phone, origin, channel, payload)

    def later(self, seconds: float, origin: Origin, channel: Channel, payload: Payload) -> None:
        """Submit an event after a delay (the one timer the system uses)."""
        self.pipeline.later(seconds, self.phone, origin, channel, payload)


class Pipeline:
    def __init__(
        self,
        db: SessionFactory,
        *,
        clock: Clock,
        timers: Timers,
    ) -> None:
        self.responders: dict[Medium, Responder] = {}
        self._db = db
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

    async def history(
        self, phone: str, *, limit: int | None = None, after_seq: int = 0
    ) -> list[Event]:
        async with self._db() as s:
            return await events.list_events(s, phone, limit=limit, after_seq=after_seq)

    async def memory(self, phone: str) -> Memory:
        async with self._db() as s:
            return await memories.memory(s, phone)

    async def facts(self, phone: str, *, app: str | None = None) -> tuple[Fact, ...]:
        """What's remembered about them; with `app`, only the facts about that service."""
        async with self._db() as s:
            return await memories.facts(s, phone, app=app)

    async def conversation(self, phone: str) -> tuple[Memory, list[Event]]:
        """What a model sees of them: the memory, and the events its summary doesn't cover."""
        memory = await self.memory(phone)
        return memory, await self.history(phone, after_seq=memory.after_seq)

    async def save_summary(self, phone: str, summary: Summary) -> None:
        async with self._db() as s, s.begin():
            await memories.set_summary(s, phone, summary, now=self._clock())

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
            event = await self.record(phone, origin, channel, payload)
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

    def spawn(self, work: Awaitable[object]) -> asyncio.Future[object]:
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
        return task

    def subscribe(
        self, phone: str, fn: Subscriber, *, kinds: Iterable[str] | None = None
    ) -> Callable[[], None]:
        entry = (frozenset(kinds) if kinds is not None else None, fn)
        self._subscribers[phone].append(entry)

        def unsubscribe() -> None:
            if entry in self._subscribers[phone]:
                self._subscribers[phone].remove(entry)

        return unsubscribe

    async def close_open_calls(self) -> None:
        """At startup nothing is on a call, whatever the rows say: a restart mid-call never
        wrote its "ended". Close them, so texts go to the text side again and it picks up."""
        async with self._db() as s:
            open_calls = await users.in_calls(s)
        for phone, phase in open_calls:
            ringing = phase is CallPhase.RINGING
            end = CallTransition.FAILED if ringing else CallTransition.ENDED
            closed = CallEvent(transition=end, reason="server_restart")
            await self.submit(phone, Origin.SYSTEM, Channel.SYSTEM, closed)

    async def reset(self, phone: str) -> None:
        """Debug: forget everything about a user."""
        async with self._locks[phone], self._db() as s, s.begin():
            await events.delete_events(s, phone)
            await memories.delete_memory(s, phone)
            await s.exec(delete(JobRow).where(col(JobRow.phone) == phone))  # pyright: ignore[reportArgumentType]
            await users.delete_user(s, phone)

    async def record(
        self, phone: str, origin: Origin, channel: Channel, payload: Payload
    ) -> Event | None:
        """Save and publish, without routing."""
        event = await self._save(phone, origin, channel, payload)
        if event is not None and payload.persists:
            for kinds, fn in list(self._subscribers[phone]):
                if kinds is None or event.kind in kinds:
                    result = fn(event)
                    if asyncio.iscoroutine(result):
                        await result
        return event

    # ---- steps --------------------------------------------------------------------

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

    async def _route(self, phone: str, event: Event) -> None:
        async with self._db() as s:
            user = await users.get_user(s, phone)
            recent = await events.list_events(s, phone, limit=RECENT)
        assert user is not None
        responder = self.responders[user.floor]
        context = Context(self, phone, recent, user.slots.zone())
        decision = await responder.handle(event, user, context)
        if decision is not None:
            await self.record(phone, Origin.SYSTEM, Channel.SYSTEM, decision)


async def _apply(s: AsyncSession, user: User, payload: Payload, now: datetime) -> Payload | None:
    """The events that change user state, and how. Returns the event to record (possibly
    filled in), or None to drop it: an impossible call transition, a slot set to the value
    it already has, a second "link sent", a second graduation, a fact already remembered, or
    forgetting one that isn't."""
    match payload:
        case CallEvent():
            call = next_state(user.call, payload, now)
            if call is None:
                return None
            await users.set_call(s, user.phone, call)
            if payload.transition is CallTransition.DECLINED:
                await users.set_slots(s, user.phone, no_calls=True)
        case CallOptOut():
            if user.slots.no_calls:
                return None
            await users.set_slots(s, user.phone, no_calls=True)
        case StepSetAside(step=step):
            if step in user.slots.set_aside:
                return None
            await users.set_slots(s, user.phone, set_aside=",".join((*user.slots.set_aside, step)))
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
        case ContactSaved(name=name):
            if user.slots.contact_name == name:
                return None
            await users.set_slots(s, user.phone, contact_name=name)
        case TimezoneLearned(tz=tz, source=source):
            slots = user.slots
            if (slots.timezone, slots.timezone_source) == (tz, source):
                return None
            if source is TzSource.CALENDAR and slots.timezone_source is TzSource.SAID:
                return None  # what they said wins
            await users.set_slots(s, user.phone, timezone=tz, timezone_source=source)
        case Graduated():
            if user.slots.graduated:
                return None
            await users.set_slots(s, user.phone, graduated=True)
        case Remembered(fact=fact, app=app):
            fact_id = await memories.add_fact(s, user.phone, fact, app=app, now=now)
            if fact_id is None:
                return None
            return payload.model_copy(update={"fact_id": fact_id})
        case Forgot(fact_id=fact_id):
            fact = await memories.forget_fact(s, user.phone, fact_id, now=now)
            if fact is None:
                return None
            return payload.model_copy(update={"fact": fact})
        case Typing(active=active):
            await users.set_typing(s, user.phone, now if active else None)
        case _:
            pass
    return payload
