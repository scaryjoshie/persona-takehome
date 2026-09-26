"""SQLite tables, write-through sink, and loader. The DB knows events and one JSON
state blob per user; it does not know what the sections keep in that blob."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import JSON, Column, Engine
from sqlmodel import Field, Session, SQLModel, create_engine, select

from app.events.base import Channel, Origin, Payload
from app.events.envelope import Event


class UserRow(SQLModel, table=True):
    __tablename__ = "user"  # pyright: ignore[reportAssignmentType]

    phone: str = Field(primary_key=True)
    created_at: datetime
    state: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


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


def make_engine(database_url: str) -> Engine:
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


class SqlSink:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def on_event(self, phone: str, event: Event) -> None:
        with Session(self._engine) as s:
            s.add(
                EventRow(
                    user_phone=phone,
                    seq=event.seq,
                    ts=event.ts,
                    origin=event.origin.value,
                    channel=event.channel.value,
                    kind=event.kind,
                    payload=event.payload.model_dump(mode="json"),
                )
            )
            s.commit()

    async def on_state(self, phone: str, state: BaseModel) -> None:
        with Session(self._engine) as s:
            row = s.get(UserRow, phone) or UserRow(phone=phone, created_at=datetime.now(UTC))
            row.state = state.model_dump(mode="json")
            s.add(row)
            s.commit()


class Loaded[S: BaseModel]:
    def __init__(self, events: list[Event], state: S) -> None:
        self.events = events
        self.state = state


def load_user[S: BaseModel](
    engine: Engine, phone: str, *, payloads: TypeAdapter[Payload], state_type: type[S]
) -> Loaded[S]:
    """Read a user's log and state, creating the user if new."""
    with Session(engine) as s:
        row = s.get(UserRow, phone)
        if row is None:
            row = UserRow(phone=phone, created_at=datetime.now(UTC))
            s.add(row)
            s.commit()
        state = state_type.model_validate(row.state) if row.state else state_type()
        rows = s.exec(
            select(EventRow).where(EventRow.user_phone == phone).order_by(EventRow.seq)  # pyright: ignore[reportArgumentType]
        ).all()
    events = [
        Event(
            seq=r.seq,
            ts=r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=UTC),
            origin=Origin(r.origin),
            channel=Channel(r.channel),
            payload=payloads.validate_python(r.payload),
        )
        for r in rows
    ]
    return Loaded(events, state)
