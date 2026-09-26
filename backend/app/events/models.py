"""The event table. Persistence only."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


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
