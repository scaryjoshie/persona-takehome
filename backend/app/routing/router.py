"""Hand a routable event to the floor holder's driver, via the filter."""

from __future__ import annotations

from typing import Protocol

from app.events.base import Channel, Origin
from app.events.envelope import Event
from app.routing.filter import Filter
from app.routing.types import DecidedBy, Decision, Medium, RoutingContext, Run, Verb
from app.user import User

RECENT_FOR_DECIDER = 12


class Driver(Protocol):
    """A per-medium run controller. Starts runs and applies verbs to them."""

    medium: Medium

    @property
    def run(self) -> Run | None: ...
    async def start(self, event: Event) -> None: ...
    async def apply(self, verb: Verb, event: Event) -> None: ...


class Router:
    def __init__(self, user: User, drivers: dict[Medium, Driver], filter_: Filter) -> None:
        self._user = user
        self._drivers = drivers
        self._filter = filter_

    async def route(self, event: Event) -> Decision:
        user, store = self._user, self._user.store
        driver = self._drivers[user.floor]
        t0 = store.now()
        if driver.run is None:
            await driver.start(event)
            verb, by, confidence, note = Verb.START, DecidedBy.FIXED, 1.0, f"idle {user.floor}"
        else:
            ctx = RoutingContext(
                trigger=event,
                run=driver.run,
                floor=user.floor,
                call=user.call,
                still_missing=user.slots.missing(),
                recent=store.recent(RECENT_FOR_DECIDER),
                now=t0,
            )
            v = await self._filter.verdict(ctx)
            await driver.apply(v.verb, event)
            verb, by, confidence, note = v.verb, v.by, v.confidence, v.note
        decision = Decision(
            trigger_kind=event.kind,
            verb=verb,
            by=by,
            confidence=confidence,
            ms=int((store.now() - t0).total_seconds() * 1000),
            note=note,
        )
        await store.append(Origin.SYSTEM, Channel.SYSTEM, decision)
        return decision
