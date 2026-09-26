"""No judgment: a fixed verb per event kind, logged as by=DEFAULT.

Each medium supplies its own table (see text/decider.py, voice/decider.py)."""

from __future__ import annotations

from collections.abc import Mapping

from app.routing.types import DecidedBy, RoutingContext, Verb, Verdict


class DefaultDecider:
    def __init__(self, defaults: Mapping[str, Verb], *, otherwise: Verb = Verb.ABSORB) -> None:
        self._defaults = dict(defaults)
        self._otherwise = otherwise

    async def decide(self, ctx: RoutingContext) -> Verdict:
        verb = self._defaults.get(ctx.trigger.kind, self._otherwise)
        return Verdict(verb=verb, confidence=0.5, by=DecidedBy.DEFAULT, note="no decider")
