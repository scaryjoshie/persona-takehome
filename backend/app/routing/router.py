"""Hand a routable event to the floor holder's responder, via the filter. No I/O:
the caller supplies the user and recent events and persists the decision."""

from __future__ import annotations

from app.events.event import Event
from app.routing.filter import Filter
from app.routing.responder import Responder
from app.routing.types import DecidedBy, Decision, Medium, RoutingContext, Verb
from app.timers import Clock
from app.users.user import User


class Router:
    def __init__(self, filter_: Filter, *, clock: Clock) -> None:
        self._filter = filter_
        self._clock = clock

    async def route(
        self, responders: dict[Medium, Responder], user: User, event: Event, recent: list[Event]
    ) -> Decision:
        responder = responders[user.floor]
        t0 = self._clock()
        if responder.run is None:
            await responder.start(event)
            verb, by, confidence, note = (
                Verb.START,
                DecidedBy.FIXED,
                1.0,
                f"idle {user.floor.value}",
            )
        else:
            ctx = RoutingContext(
                trigger=event,
                run=responder.run,
                floor=user.floor,
                call=user.call,
                still_missing=user.slots.missing(),
                recent=tuple(recent),
                now=t0,
            )
            v = await self._filter.verdict(ctx)
            await responder.apply(v.verb, event)
            verb, by, confidence, note = v.verb, v.by, v.confidence, v.note
        return Decision(
            trigger_kind=event.kind,
            verb=verb,
            by=by,
            confidence=confidence,
            ms=int((self._clock() - t0).total_seconds() * 1000),
            note=note,
        )
