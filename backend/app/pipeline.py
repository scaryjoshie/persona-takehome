"""The pipeline: every event goes through `submit`. It is saved (applying it to the user's
state if it changes any), pushed to the browser, and routed to whoever has the floor."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from pydantic import TypeAdapter
from sqlmodel.ext.asyncio.session import AsyncSession

from app.agent.events import Graduated, SlotChanged
from app.calls.events import CallEvent
from app.calls.state import next_state
from app.database import SessionFactory
from app.events import service as events
from app.events.event import Event
from app.events.payload import Channel, Origin, Payload
from app.gmail.events import GmailEvent
from app.routing.route import route
from app.users import service as users
from app.users.live import LiveUsers
from app.users.user import User

log = logging.getLogger(__name__)

RECENT = 12  # events the decider sees


class Pipeline:
    def __init__(
        self,
        db: SessionFactory,
        live_users: LiveUsers,
        *,
        payloads: TypeAdapter[Payload],
        clock: Callable[[], datetime],
    ) -> None:
        self.live_users = live_users
        self._db = db
        self._payloads = payloads
        self._clock = clock

    # ---- reads ----------------------------------------------------------------

    async def user(self, phone: str) -> User:
        async with self._db() as s, s.begin():
            return await users.ensure_user(s, phone, now=self._clock())

    async def history(self, phone: str, *, limit: int | None = None) -> list[Event]:
        async with self._db() as s:
            return await events.list_events(s, phone, payloads=self._payloads, limit=limit)

    async def reset(self, phone: str) -> None:
        """Debug: forget everything about a user. Live state (sockets, responders) is kept."""
        async with self.live_users.get(phone).lock, self._db() as s, s.begin():
            await events.delete_events(s, phone)
            await users.delete_user(s, phone)

    # ---- writes ---------------------------------------------------------------

    async def submit(
        self,
        phone: str,
        origin: Origin,
        channel: Channel,
        payload: Payload,
        *,
        route: bool | None = None,
    ) -> Event | None:
        """The one door for events: save it, push it to the browser, route it.
        Returns None if it was a call event that makes no sense from the current call state."""
        live = self.live_users.get(phone)
        async with live.lock:
            event = await self._save(phone, origin, channel, payload)
            if event is None:
                return None
            if payload.persists:
                await live.publish(event)
            if route if route is not None else payload.should_route():
                await self._route(phone, event)
            return event

    # ---- steps ------------------------------------------------------------------

    async def _save(
        self, phone: str, origin: Origin, channel: Channel, payload: Payload
    ) -> Event | None:
        """Apply the event to the user's state, then append it (if it persists).
        Returns None if the event changes nothing it should have (see `_apply`)."""
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
        """Hand the event to the responder with the floor, and log what it decided."""
        async with self._db() as s:
            user = await users.get_user(s, phone)
            recent = await events.list_events(s, phone, payloads=self._payloads, limit=RECENT)
        assert user is not None
        live = self.live_users.get(phone)
        decision = await route(event, user, live.responders, recent, self._clock)
        logged = await self._save(phone, Origin.SYSTEM, Channel.SYSTEM, decision)
        assert logged is not None
        await live.publish(logged)


async def _apply(s: AsyncSession, user: User, payload: Payload, now: datetime) -> Payload | None:
    """The events that change user state, and how. Returns the event to record (possibly
    filled in), or None to drop it: an impossible call transition, or a slot set to the
    value it already has."""
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
            await users.set_slots(
                s, user.phone, gmail=phase, gmail_email=email or user.slots.gmail_email
            )
        case Graduated():
            if user.slots.graduated:
                return None
            await users.set_slots(s, user.phone, graduated=True)
        case _:
            pass
    return payload
