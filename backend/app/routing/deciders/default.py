"""No judgment: a conservative default per event kind, logged as by=DEFAULT."""

from __future__ import annotations

from app.routing.types import DecidedBy, RoutingContext, Verb, Verdict


class DefaultDecider:
    DEFAULTS: dict[str, Verb] = {
        "typing": Verb.ABSORB,  # never cut anyone off for a typing signal
        "gmail": Verb.DEFER,  # finish the sentence, then react
    }

    async def decide(self, ctx: RoutingContext) -> Verdict:
        verb = self.DEFAULTS.get(ctx.trigger.kind, Verb.ABSORB)
        return Verdict(verb=verb, confidence=0.5, by=DecidedBy.DEFAULT, note="no decider")
