from __future__ import annotations

from typing import Protocol

from app.routing.types import RoutingContext, Verdict


class Decider(Protocol):
    async def decide(self, ctx: RoutingContext) -> Verdict: ...
