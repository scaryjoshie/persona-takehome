from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Literal

from app.events.base import Payload, Role, Turn


class GmailPhase(StrEnum):
    LINK_SENT = "link_sent"
    CONNECTED = "connected"
    FAILED = "failed"
    SKIPPED = "skipped"


class GmailEvent(Payload):
    kind: Literal["gmail"] = "gmail"

    phase: GmailPhase
    email: str | None = None

    OUTCOMES: ClassVar[frozenset[GmailPhase]] = frozenset({GmailPhase.CONNECTED, GmailPhase.FAILED})

    def should_route(self) -> bool:
        """Connected and failed need a reaction; link_sent and skipped are the agent's own doing."""
        return self.phase in self.OUTCOMES

    def turn(self, at: datetime) -> Turn | None:
        match self.phase:
            case GmailPhase.CONNECTED:
                return Turn(Role.NOTE, f"Gmail connected as {self.email}")
            case GmailPhase.LINK_SENT:
                return Turn(Role.NOTE, "Gmail link sent by text")
            case GmailPhase.SKIPPED:
                return Turn(Role.NOTE, "user skipped Gmail; do not ask again")
            case GmailPhase.FAILED:
                return Turn(Role.NOTE, "Gmail connection failed")
