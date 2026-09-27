"""The fact and summary tables. Persistence only."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class FactRow(SQLModel, table=True):
    """Something the agent remembered about them. Forgetting keeps the row, stamped."""

    __tablename__ = "fact"  # pyright: ignore[reportAssignmentType]

    id: int | None = Field(default=None, primary_key=True)
    user_phone: str = Field(foreign_key="user.phone", index=True)
    text: str
    app: str | None = None  # the service it's about ("DoorDash"), or None for them in general
    created_at: datetime
    forgotten_at: datetime | None = None


class SummaryRow(SQLModel, table=True):
    """The running summary of their conversation, through one event. Derived from the log:
    losing it only means the next reply sees more of the conversation word for word."""

    __tablename__ = "summary"  # pyright: ignore[reportAssignmentType]

    user_phone: str = Field(primary_key=True)
    through_seq: int
    text: str
    updated_at: datetime
