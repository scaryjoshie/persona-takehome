"""The filter: given an event and an active run, pick a verb.

Fixed cases come from the event itself (`Payload.fixed_verb`); judgment cases go to
the decider. Decision 2026-09-25: Jev only for routing, no rule-based decider, so
until Jev is wired the judgment cases fall back to a per-kind default and say so.
"""

from __future__ import annotations

from typing import Protocol

from app.routing.types import DecidedBy, RoutingContext, Verb, Verdict


class Decider(Protocol):
    async def decide(self, ctx: RoutingContext) -> Verdict: ...


class DefaultDecider:
    """No judgment: a conservative default per kind, logged as by=DEFAULT."""

    DEFAULTS: dict[str, Verb] = {
        "typing": Verb.ABSORB,  # never cut anyone off for a typing signal
        "gmail": Verb.DEFER,  # finish the sentence, then react
    }

    async def decide(self, ctx: RoutingContext) -> Verdict:
        verb = self.DEFAULTS.get(ctx.trigger.kind, Verb.ABSORB)
        return Verdict(verb=verb, confidence=0.5, by=DecidedBy.DEFAULT, note="no decider")


class Filter:
    def __init__(self, decider: Decider) -> None:
        self._decider = decider

    async def verdict(self, ctx: RoutingContext) -> Verdict:
        fixed = ctx.trigger.payload.fixed_verb(ctx)
        if fixed is not None:
            verdict = Verdict(verb=fixed, confidence=1.0, by=DecidedBy.FIXED)
        else:
            verdict = await self._decider.decide(ctx)
        # Interrupt is only safe at a step boundary: with a side effect in flight it
        # degrades to defer (the "steer" policy) and the responder re-asks afterwards.
        if verdict.verb is Verb.INTERRUPT and ctx.run and ctx.run.side_effect_in_flight:
            return verdict.model_copy(
                update={"verb": Verb.DEFER, "note": "interrupt degraded: side effect in flight"}
            )
        return verdict
