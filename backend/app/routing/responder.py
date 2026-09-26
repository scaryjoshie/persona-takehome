"""A responder decides when and how its medium answers a routed event.

The text responder waits for the user to finish typing, then runs the agent once, and
can cancel or re-run. The voice responder forwards the event into the live call as a
note, now or after the current sentence. There is one of each per user.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.events.event import Event
from app.routing.deciders import Decider
from app.routing.types import Medium, Run, Verb


class Responder(ABC):
    medium: Medium
    decider: Decider  # judges events that arrive mid-run; each medium brings its own

    @property
    @abstractmethod
    def run(self) -> Run | None:
        """The response in progress, if any. What the filter looks at."""

    @abstractmethod
    async def start(self, event: Event) -> None:
        """No run was in progress: begin one for this event."""

    @abstractmethod
    async def apply(self, verb: Verb, event: Event) -> None:
        """A run is in progress: interrupt it, absorb the event, or defer the event."""
