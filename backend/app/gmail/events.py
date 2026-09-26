from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from app.events.payload import Payload, Role, Turn


class GmailPhase(StrEnum):
    LINK_SENT = "link_sent"
    CONNECTED = "connected"
    FAILED = "failed"
    SKIPPED = "skipped"


class InboxItem(BaseModel):
    """One inbox message as seen at connect time: headers and Gmail's snippet, no body."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    sender: str
    subject: str
    snippet: str


class GmailEvent(Payload):
    kind: Literal["gmail"] = "gmail"

    phase: GmailPhase
    email: str | None = None
    demo: bool = False  # the demo inbox, not their real one
    inbox: list[InboxItem] = []  # the latest messages, read once when it connected

    OUTCOMES: ClassVar[frozenset[GmailPhase]] = frozenset({GmailPhase.CONNECTED, GmailPhase.FAILED})

    def should_route(self) -> bool:
        """Connected and failed need a reaction; link_sent and skipped are the agent's own doing."""
        return self.phase in self.OUTCOMES

    def describe(self) -> str:
        if self.phase is GmailPhase.CONNECTED:
            return "the user's Gmail just finished connecting"
        return f"Gmail: {self.phase.value.replace('_', ' ')}"

    def turn(self, at: datetime) -> Turn | None:
        match self.phase:
            case GmailPhase.CONNECTED:
                which = "a demo inbox (tell them it's sample mail)" if self.demo else self.email
                text = f"Gmail connected: {which}."
                if self.inbox:
                    text += f" Their latest inbox messages:\n{inbox_lines(self)}"
                return Turn(Role.NOTE, text)
            case GmailPhase.LINK_SENT:
                return Turn(Role.NOTE, "Gmail link sent by text")
            case GmailPhase.SKIPPED:
                return Turn(Role.NOTE, "user skipped Gmail; do not ask again")
            case GmailPhase.FAILED:
                return Turn(Role.NOTE, "Gmail connection failed")


def inbox_lines(event: GmailEvent) -> str:
    return "\n".join(f"- {m.sender}: {m.subject} ({m.snippet[:90]})" for m in event.inbox)
