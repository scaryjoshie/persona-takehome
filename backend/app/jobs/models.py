"""The job table: a background job's goal, where it's at, and its own conversation so far."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class JobRow(SQLModel, table=True):
    __tablename__ = "job"  # pyright: ignore[reportAssignmentType]

    id: str = Field(primary_key=True)
    phone: str = Field(index=True)
    goal: str
    status: str = "running"  # running, waiting, done, failed, cancelled
    messages: str = "[]"  # its model messages as JSON, so it resumes after a pause or restart
    question: str | None = None  # what it's waiting on them to answer
    waiting_on: str | None = None  # that question's tool call ids, comma-separated
    asked_at: datetime | None = None  # when it asked; the question expires a day later
    # The answer that resumed it (JSON, tool call id → text), until the next run saves its
    # messages: a restart in between resumes with it.
    answer: str | None = None
    created_at: datetime
