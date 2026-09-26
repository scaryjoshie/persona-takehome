"""What happens to an event after it is saved. Called by `Actions.submit`.

    1. Pick the responder: voice if a call is connected, text otherwise (the user's floor).
    2. Nothing in progress on that responder: it starts responding to the event.
    3. Something in progress: decide a verb (interrupt, absorb, defer). The event's own
       fixed verb if it has one, otherwise the responder's decider (Jev).
    4. The responder applies the verb.

Returns a Decision, which the caller logs for the debug panel.
"""

from __future__ import annotations

from datetime import datetime

from app.events.event import Event
from app.routing.responder import Responder
from app.routing.types import DecidedBy, Decision, Medium, RoutingContext, Verb, Verdict
from app.timers import Clock
from app.users.user import User


async def route(
    event: Event,
    user: User,
    responders: dict[Medium, Responder],
    recent: list[Event],
    clock: Clock,
) -> Decision:
    started = clock()
    responder = responders[user.floor]  # 1

    if responder.run is None:  # 2
        await responder.start(event)
        verdict = Verdict(verb=Verb.START, confidence=1.0, by=DecidedBy.FIXED)
    else:
        verdict = await decide(event, user, responder, recent, started)  # 3
        await responder.apply(verdict.verb, event)  # 4

    return Decision(
        trigger_kind=event.kind,
        verb=verdict.verb,
        by=verdict.by,
        confidence=verdict.confidence,
        ms=int((clock() - started).total_seconds() * 1000),
        note=verdict.note,
    )


async def decide(
    event: Event, user: User, responder: Responder, recent: list[Event], now: datetime
) -> Verdict:
    ctx = RoutingContext(
        trigger=event,
        run=responder.run,
        floor=user.floor,
        call=user.call,
        still_missing=user.slots.missing(),
        recent=tuple(recent),
        now=now,
    )
    fixed = event.payload.fixed_verb(ctx)
    if fixed is not None:
        verdict = Verdict(verb=fixed, confidence=1.0, by=DecidedBy.FIXED)
    else:
        verdict = await responder.decider.decide(ctx)
    # Never interrupt a response that is in the middle of doing something external.
    if verdict.verb is Verb.INTERRUPT and responder.run and responder.run.side_effect_in_flight:
        return verdict.model_copy(
            update={"verb": Verb.DEFER, "note": "interrupt degraded: side effect in flight"}
        )
    return verdict
