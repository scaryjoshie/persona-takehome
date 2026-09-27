"""The four things onboarding collects, plus graduation. The onboarding domain state."""

from __future__ import annotations

from enum import StrEnum
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from app.google.events import GmailPhase

DEFAULT_TZ = "America/New_York"  # until they tell us, or their calendar does


class TzSource(StrEnum):
    """How we know their timezone. Only what a texting assistant could really know: their
    Google Calendar's setting, or what they told us (which wins; they may be travelling)."""

    CALENDAR = "calendar"
    SAID = "said"


class Slots(BaseModel):
    model_config = ConfigDict(frozen=True, json_schema_serialization_defaults_required=True)

    agent_name: str | None = None
    user_name: str | None = None
    help_need: str | None = None
    gmail: GmailPhase | None = None  # None = not asked yet
    gmail_email: str | None = None
    graduated: bool = False
    no_calls: bool = False  # they declined a call; don't offer again unless they ask
    # Phone-only: what their phone has the agent saved as (None = not saved). Drives the
    # phone's header; the agent never sees it (not routed, not in any prompt).
    contact_name: str | None = None
    timezone: str | None = None  # IANA; counts only with a source (older rows: the browser's)
    timezone_source: TzSource | None = None

    def zone(self) -> ZoneInfo:
        """Their timezone if we know it, else the default guess."""
        return (
            ZoneInfo(self.timezone)
            if self.timezone and self.timezone_source
            else ZoneInfo(DEFAULT_TZ)
        )

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
