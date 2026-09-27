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
    DISCONNECTED = "disconnected"


class InboxItem(BaseModel):
    """One inbox message as seen at connect time: headers and Gmail's snippet, no body."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: str = ""  # Gmail's message id, so the agent can open it
    sender: str
    subject: str
    snippet: str


class GmailEvent(Payload):
    kind: Literal["gmail"] = "gmail"

    phase: GmailPhase
    email: str | None = None
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
                text = f"Gmail connected: {self.email}."
                if self.inbox:
                    text += (
                        " Go through it once they want you to; their latest inbox messages, "
                        "for then:\n"
                        f"{inbox_lines(self)}"
                    )
                return Turn(Role.NOTE, text)
            case GmailPhase.LINK_SENT:
                return Turn(Role.NOTE, "Gmail link sent by text")
            case GmailPhase.SKIPPED:
                return Turn(Role.NOTE, "user skipped Gmail; do not ask again")
            case GmailPhase.FAILED:
                return Turn(Role.NOTE, "Gmail connection failed")
            case GmailPhase.DISCONNECTED:
                return Turn(Role.NOTE, "they disconnected their Google account")


def inbox_lines(event: GmailEvent) -> str:
    return "\n".join(f"- [{m.id}] {m.sender}: {m.subject} ({m.snippet[:90]})" for m in event.inbox)


class EmailDraft(Payload):
    kind: Literal["email_draft"] = "email_draft"
    routes = False  # the agent's own doing

    ref: str  # ours; the same across versions of one draft
    to: str = ""
    subject: str = ""
    body: str = ""
    gmail_id: str = ""  # the draft in their Gmail
    status: Literal["draft", "sent"] = "draft"

    @property
    def missing(self) -> list[str]:
        return [name for name in ("to", "subject", "body") if not getattr(self, name).strip()]

    def turn(self, at: datetime) -> Turn | None:
        if self.status == "sent":
            return Turn(Role.NOTE, f"email {self.ref} sent to {self.to}: {self.subject}")
        gaps = f" (missing: {', '.join(self.missing)})" if self.missing else ""
        return Turn(
            Role.NOTE,
            f"you texted them a picture of email draft {self.ref}{gaps}:\n"
            f"To: {self.to}\nSubject: {self.subject}\n{self.body}",
        )
