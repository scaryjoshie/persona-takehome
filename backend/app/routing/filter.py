"""The filter: given an event that arrived during a run, pick a verb.

The shape is the same for every medium: a fixed verb from the event itself when it has
one (`Payload.fixed_verb`), otherwise the responder's own decider. Then one safety rule:
a run with a side effect in flight is never interrupted.
"""

from __future__ import annotations

from app.routing.deciders import Decider
from app.routing.types import DecidedBy, RoutingContext, Verb, Verdict


async def verdict(ctx: RoutingContext, decider: Decider) -> Verdict:
    fixed = ctx.trigger.payload.fixed_verb(ctx)
    if fixed is not None:
        result = Verdict(verb=fixed, confidence=1.0, by=DecidedBy.FIXED)
    else:
        result = await decider.decide(ctx)
    if result.verb is Verb.INTERRUPT and ctx.run and ctx.run.side_effect_in_flight:
        return result.model_copy(
            update={"verb": Verb.DEFER, "note": "interrupt degraded: side effect in flight"}
        )
    return result
