"""The four things onboarding collects, plus graduation. The onboarding domain state."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.gmail.events import GmailPhase


class Slots(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent_name: str | None = None
    user_name: str | None = None
    help_need: str | None = None
    gmail: GmailPhase | None = None  # None = not asked yet
    gmail_email: str | None = None
    graduated: bool = False

    def missing(self) -> tuple[str, ...]:
        out: list[str] = []
        if self.agent_name is None:
            out.append("agent_name")
        if self.user_name is None:
            out.append("user_name")
        if self.help_need is None:
            out.append("help_need")
        if self.gmail in (None, GmailPhase.LINK_SENT):
            out.append("gmail")
        return tuple(out)
