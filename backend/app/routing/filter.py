"""The filter: given an event that arrived during a run, pick a verb.

Fixed cases come from the event itself (`Payload.fixed_verb`); judgment cases go to
the decider (see `routing.deciders`).
"""

from __future__ import annotations

from app.routing.deciders import Decider
from app.routing.types import DecidedBy, RoutingContext, Verb, Verdict


class Filter:
    def __init__(self, decider: Decider) -> None:
        self._decider = decider

    async def verdict(self, ctx: RoutingContext) -> Verdict:
        fixed = ctx.trigger.payload.fixed_verb(ctx)
        if fixed is not None:
            verdict = Verdict(verb=fixed, confidence=1.0, by=DecidedBy.FIXED)
        else:
            verdict = await self._decider.decide(ctx)
        # A run with a side effect in flight is never interrupted: interrupt degrades to
        # defer (the "steer" policy) and the responder re-asks once the run ends.
        if verdict.verb is Verb.INTERRUPT and ctx.run and ctx.run.side_effect_in_flight:
            return verdict.model_copy(
                update={"verb": Verb.DEFER, "note": "interrupt degraded: side effect in flight"}
            )
        return verdict
