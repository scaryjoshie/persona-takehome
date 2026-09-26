"""Deciders answer the judgment cases the filter cannot fix by event kind.

One method: `decide(ctx) -> Verdict`. `DefaultDecider` is the only one today. A Jev
decider (TypeSafe AI's decision model; access pending) will be a second class here with
the same interface; the filter does not know which it is talking to.
"""

from app.routing.deciders.default import DefaultDecider
from app.routing.deciders.protocol import Decider

__all__ = ["Decider", "DefaultDecider"]
