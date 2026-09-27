"""Events for what the agent remembers about them, beyond the onboarding slots."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.events.payload import Payload, Role, Turn


class Remembered(Payload):
    """The agent remembered a fact. The pipeline fills in `fact_id` and drops a repeat."""

    kind: Literal["remembered"] = "remembered"
    routes = False

    fact: str
    app: str | None = None  # the service it's about, so work in that app can pull just these
    fact_id: int | None = None

    def turn(self, at: datetime) -> Turn | None:
        about = f" ({self.app})" if self.app else ""
        return Turn(Role.NOTE, f"you remembered{about}: {self.fact}")


class Forgot(Payload):
    """The agent forgot a fact, by its number. The pipeline fills in `fact` and drops the
    event if there is no such fact."""

    kind: Literal["forgot"] = "forgot"
    routes = False

    fact_id: int
    fact: str | None = None

    def turn(self, at: datetime) -> Turn | None:
        return Turn(Role.NOTE, f"you forgot: {self.fact}")
