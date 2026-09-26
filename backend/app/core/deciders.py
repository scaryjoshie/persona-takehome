"""Deciders answer the judgment cases of the policy. See docs/proposed-design/05.

Decision 2026-09-25: Jev only for routing; no rule-based decider. Until Jev access
exists, `DefaultDecider` returns the fixed default verb for the delta kind and says so.
"""

from __future__ import annotations

from typing import Protocol

from app.core.types import RoutingContext, Verb, Verdict


class Decider(Protocol):
    async def decide(self, ctx: RoutingContext) -> Verdict: ...


DEFAULT_VERB: dict[str, Verb] = {
    "typing": "absorb",  # a typing signal never cuts anyone off by default
    "gmail": "defer",  # finish the sentence, then react
}


class DefaultDecider:
    """No judgment: the fixed default for the kind, logged as by='default'."""

    async def decide(self, ctx: RoutingContext) -> Verdict:
        verb = DEFAULT_VERB.get(ctx.trigger.kind, "absorb")
        return Verdict(verb=verb, confidence=0.5, by="default", note="no decider configured")
