"""Actions own transactions. Each one: validate, one short transaction, then notify
the live and route. This is the only place that composes sections."""

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
from app.routing.router import Router
from app.users import service as users
from app.users.live import LiveUsers
from app.users.user import User

log = logging.getLogger(__name__)

RECENT = 12


class Actions:
    def __init__(
        self,
        db: SessionFactory,
        live_users: LiveUsers,
        router: Router,
        *,
        payloads: TypeAdapter[Payload],
        clock: Callable[[], datetime],
    ) -> None:
        self._db = db
        self._live_users = live_users
        self._router = router
        self._payloads = payloads
        self._clock = clock

    # ---- reads ----------------------------------------------------------------

    async def user(self, phone: str) -> User:
        async with self._db() as s, s.begin():
            return await users.ensure_user(s, phone, now=self._clock())

    async def history(self, phone: str, *, limit: int | None = None) -> list[Event]:
        async with self._db() as s:
            return await events.list_events(s, phone, payloads=self._payloads, limit=limit)

    # ---- the one door for events -----------------------------------------------

    async def submit(
        self,
        phone: str,
        origin: Origin,
        channel: Channel,
        payload: Payload,
        *,
        route: bool | None = None,
    ) -> Event | None:
        """Persist (if the kind persists), apply call transitions, publish, route.
        Returns None if a call transition was invalid and the event was dropped."""
        rt = self._live_users.get(phone)
        async with rt.lock:
            now = self._clock()
            async with self._db() as s, s.begin():
                user = await users.ensure_user(s, phone, now=now)
                if isinstance(payload, CallEvent):
                    nxt = next_state(user.call, payload, now)
                    if nxt is None:
                        log.info(
                            "%s: dropped call %s from %s",
                            phone,
                            payload.transition,
                            user.call.phase,
                        )
                        return None
                    user = await users.set_call(s, phone, nxt)
                if payload.persists:
                    event = await events.append(s, phone, origin, channel, payload, ts=now)
                else:
                    event = Event(seq=0, ts=now, origin=origin, channel=channel, payload=payload)
                recent = await events.list_events(s, phone, payloads=self._payloads, limit=RECENT)
            if payload.persists:
                await rt.publish(event)
            if route if route is not None else payload.should_route():
                decision = await self._router.route(rt.responders, user, event, recent)
                async with self._db() as s, s.begin():
                    logged = await events.append(
                        s, phone, Origin.SYSTEM, Channel.SYSTEM, decision, ts=self._clock()
                    )
                await rt.publish(logged)
            return event

    # ---- state changes ---------------------------------------------------------

    async def set_slot(
        self, phone: str, slot: str, value: Any, *, origin: Origin, channel: Channel
    ) -> bool:
        """Idempotent. Logs a slot_changed event when the value actually changes."""
        rt = self._live_users.get(phone)
        async with rt.lock:
            now = self._clock()
            async with self._db() as s, s.begin():
                user = await users.ensure_user(s, phone, now=now)
                old = getattr(user.slots, slot)
                if old == value:
                    return False
                await users.set_slot(s, phone, slot, value)
                event = await events.append(
                    s, phone, origin, channel, SlotChanged(slot=slot, old=old, new=value), ts=now
                )
            await rt.publish(event)
            return True
