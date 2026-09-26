"""SQLModel tables. See docs/proposed-design/08-storage.md.

The event log records that things happened; Integration and Call hold the records.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    phone: str = Field(primary_key=True)
    created_at: datetime
    # slots, written only by tools
    agent_name: str | None = None
    user_name: str | None = None
    help_need: str | None = None
    gmail_status: str = "not_asked"
    gmail_email: str | None = None
    graduated: bool = False
    # call state and floor
    call_phase: str = "none"
    call_reason: str | None = None
    call_id: str | None = None
    call_initiated_by: str | None = None
    call_started_at: datetime | None = None
    call_ended_at: datetime | None = None
    floor: str = "text"


class EventRow(SQLModel, table=True):
    __tablename__ = "event"  # pyright: ignore[reportAssignmentType]

    id: int | None = Field(default=None, primary_key=True)
    user_phone: str = Field(foreign_key="user.phone", index=True)
    seq: int = Field(index=True)
    ts: datetime
    origin: str
    channel: str
    kind: str
    payload: dict[str, Any] = Field(sa_column=Column(JSON))


class Integration(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_phone: str = Field(foreign_key="user.phone", index=True)
    provider: str
    account_id: str
    status: str
    credentials: bytes  # Fernet-encrypted, provider-specific shape
    scopes: str
    meta: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    connected_at: datetime
    revoked_at: datetime | None = None


class Call(SQLModel, table=True):
    id: str = Field(primary_key=True)
    user_phone: str = Field(foreign_key="user.phone", index=True)
    reason: str | None = None
    initiated_by: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    end_reason: str | None = None
