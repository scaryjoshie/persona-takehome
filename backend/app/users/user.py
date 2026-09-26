"""A user as a frozen value: identity plus state. Replaced whole, never mutated."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.agent.slots import Slots
from app.database import aware
from app.gmail.events import GmailPhase
from app.users.models import UserRow
from app.voice.call_state import CallPhase, CallState, Initiator


class Medium(StrEnum):
    """Which medium has the floor: voice while a call is connected, text otherwise."""

    TEXT = "text"
    VOICE = "voice"


class User(BaseModel):
    model_config = ConfigDict(frozen=True)

    phone: str
    slots: Slots
    call: CallState
    floor: Medium
    typing_since: datetime | None = None

    @classmethod
    def of(cls, row: UserRow) -> User:
        return cls(
            phone=row.phone,
            slots=Slots(
                agent_name=row.agent_name,
                user_name=row.user_name,
                help_need=row.help_need,
                gmail=GmailPhase(row.gmail) if row.gmail else None,
                gmail_email=row.gmail_email,
                graduated=row.graduated,
                contact_name=row.contact_name,
                no_calls=row.no_calls,
            ),
            call=CallState(
                phase=CallPhase(row.call_phase),
                reason=row.call_reason,
                call_id=row.call_id,
                initiated_by=Initiator(row.call_initiated_by) if row.call_initiated_by else None,
                started_at=aware(row.call_started_at) if row.call_started_at else None,
                ended_at=aware(row.call_ended_at) if row.call_ended_at else None,
            ),
            floor=Medium(row.floor),
            typing_since=aware(row.typing_since) if row.typing_since else None,
        )
