"""Deciders answer the judgment cases the filter cannot fix by event kind.

One method: `decide(ctx) -> Verdict`. `JevDecider` asks TypeSafe's Jev through OpenRouter
and falls back to `DefaultDecider` (a fixed verb per event kind) if the call fails. The
filter does not know which it is talking to.
"""

from app.routing.deciders.default import DefaultDecider
from app.routing.deciders.jev import JevDecider
from app.routing.deciders.protocol import Decider

__all__ = ["Decider", "DefaultDecider", "JevDecider"]
