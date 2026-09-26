"""The policy function: fixed cases first, then the decider. See docs 05.

Returns the verb to apply to an existing run. The null-run case ("start") is handled by
the router before the policy is consulted.
"""

from __future__ import annotations

from typing import assert_never

from app.core.deciders import Decider
from app.core.types import CallEvent, GmailEvent, RoutingContext, Typing, UserMessage, Verdict


async def policy(ctx: RoutingContext, decider: Decider) -> Verdict:
    verdict = await _raw_policy(ctx, decider)
    # Interrupt is only safe at step boundaries: a side-effecting tool in flight degrades
    # interrupt to defer (the "steer" policy). The head re-asks once the tool returns.
    if verdict.verb == "interrupt" and ctx.run is not None and ctx.run.side_effect_in_flight:
        return Verdict(
            verb="defer",
            confidence=verdict.confidence,
            by=verdict.by,
            note="interrupt degraded to defer: side effect in flight",
        )
    return verdict


async def _raw_policy(ctx: RoutingContext, decider: Decider) -> Verdict:
    trigger = ctx.trigger
    match trigger:
        case UserMessage():
            return Verdict(
                verb="interrupt", confidence=1.0, by="fixed", note="a message always wins"
            )
        case CallEvent(phase=phase):
            if phase in ("declined", "failed", "ended"):
                return Verdict(
                    verb="interrupt", confidence=1.0, by="fixed", note="call outcome needs a reply"
                )
            return Verdict(verb="absorb", confidence=1.0, by="fixed", note="call state is context")
        case Typing() | GmailEvent():
            return await decider.decide(ctx)
        case _:
            assert_never(trigger)
