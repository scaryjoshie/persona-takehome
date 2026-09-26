"""Everything that changes a user goes through here. Each action runs under the user's
lock, writes in one short transaction, then tells the live user (browser push, routing)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import TypeAdapter

from app.agent.events import SlotChanged
from app.calls.events import CallEvent
from app.calls.state import next_state
from app.database import SessionFactory
from app.events import service as events
from app.events.event import Event
from app.events.payload import Channel, Origin, Payload
from app.routing.route import route
from app.users import service as users
from app.users.live import LiveUsers
from app.users.user import User

log = logging.getLogger(__name__)

RECENT = 12  # events the decider sees


class Actions:
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

    async def set_slot(
        self, phone: str, slot: str, value: Any, *, origin: Origin, channel: Channel
    ) -> bool:
        """Write one slot. Returns False (and logs nothing) if it already had that value."""
        live = self.live_users.get(phone)
        async with live.lock:
            async with self._db() as s, s.begin():
                user = await users.ensure_user(s, phone, now=self._clock())
                old = getattr(user.slots, slot)
                if old == value:
                    return False
                await users.set_slot(s, phone, slot, value)
                change = SlotChanged(slot=slot, old=old, new=value)
                event = await events.append(s, phone, origin, channel, change, ts=self._clock())
            await live.publish(event)
            return True

    # ---- steps ------------------------------------------------------------------

    async def _save(
        self, phone: str, origin: Origin, channel: Channel, payload: Payload
    ) -> Event | None:
        """Apply a call event to the call state, then append the event (if it persists)."""
        now = self._clock()
        async with self._db() as s, s.begin():
            user = await users.ensure_user(s, phone, now=now)
            if isinstance(payload, CallEvent):
                call = next_state(user.call, payload, now)
                if call is None:
                    log.info(
                        "%s: ignored call %s during %s", phone, payload.transition, user.call.phase
                    )
                    return None
                await users.set_call(s, phone, call)
            if not payload.persists:
                return Event(seq=0, ts=now, origin=origin, channel=channel, payload=payload)
            return await events.append(s, phone, origin, channel, payload, ts=now)

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
