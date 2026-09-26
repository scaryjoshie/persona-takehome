"""The user table: identity plus per-user state as columns. Persistence only."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class UserRow(SQLModel, table=True):
    __tablename__ = "user"  # pyright: ignore[reportAssignmentType]

    phone: str = Field(primary_key=True)
    created_at: datetime
    # slots
    agent_name: str | None = None
    user_name: str | None = None
    help_need: str | None = None
    gmail: str | None = None  # GmailPhase value, None = not asked
    gmail_email: str | None = None
    graduated: bool = False
    # current call
    call_phase: str = "none"
    call_reason: str | None = None
    call_id: str | None = None
    call_initiated_by: str | None = None
    call_started_at: datetime | None = None
    call_ended_at: datetime | None = None
    floor: str = "text"
    typing_since: datetime | None = None  # set while the user is typing a text
