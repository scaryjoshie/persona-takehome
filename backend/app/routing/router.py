"""Hand a routable event to the floor holder's driver, via the filter. No I/O:
the caller supplies the user and recent events and persists the decision."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from app.events.envelope import Event
from app.routing.filter import Filter
from app.routing.types import DecidedBy, Decision, Medium, RoutingContext, Run, Verb
from app.users.types import User

Clock = Callable[[], datetime]


class Driver(Protocol):
    """A per-medium run controller. Starts runs and applies verbs to them."""

    medium: Medium

    @property
    def run(self) -> Run | None: ...
    async def start(self, event: Event) -> None: ...
    async def apply(self, verb: Verb, event: Event) -> None: ...


class Router:
    def __init__(self, filter_: Filter, *, clock: Clock) -> None:
        self._filter = filter_
        self._clock = clock

    async def route(
        self, drivers: dict[Medium, Driver], user: User, event: Event, recent: list[Event]
    ) -> Decision:
        driver = drivers[user.floor]
        t0 = self._clock()
        if driver.run is None:
            await driver.start(event)
            verb, by, confidence, note = (
                Verb.START,
                DecidedBy.FIXED,
                1.0,
                f"idle {user.floor.value}",
            )
        else:
            ctx = RoutingContext(
                trigger=event,
                run=driver.run,
                floor=user.floor,
                call=user.call,
                still_missing=user.slots.missing(),
                recent=tuple(recent),
                now=t0,
            )
            v = await self._filter.verdict(ctx)
            await driver.apply(v.verb, event)
            verb, by, confidence, note = v.verb, v.by, v.confidence, v.note
        return Decision(
            trigger_kind=event.kind,
            verb=verb,
            by=by,
            confidence=confidence,
            ms=int((self._clock() - t0).total_seconds() * 1000),
            note=note,
        )
