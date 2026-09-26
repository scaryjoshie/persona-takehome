"""A user as a frozen value: identity plus state. Replaced whole, never mutated."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.agent.types import Slots
from app.calls.types import CallPhase, CallState, Initiator
from app.database import aware
from app.gmail.types import GmailPhase
from app.routing.types import Medium
from app.users.models import UserRow


class User(BaseModel):
    model_config = ConfigDict(frozen=True)

    phone: str
    slots: Slots
    call: CallState
    floor: Medium

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
        )
