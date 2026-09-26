"""SQLite write-through and load. The store's Sink implementation. See docs 08.

Synchronous SQLModel sessions: SQLite writes take about a millisecond, and the actor
serializes everything per user, so blocking the loop briefly is acceptable here.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.store import Store
from app.core.types import PAYLOAD_ADAPTER, CallState, Event, Floor, Slots
from app.db.models import EventRow, User


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
                    origin=event.origin,
                    channel=event.channel,
                    kind=event.kind,
                    payload=event.payload.model_dump(mode="json"),
                )
            )
            s.commit()

    async def on_state(self, phone: str, slots: Slots, call: CallState, floor: Floor) -> None:
        with Session(self._engine) as s:
            user = s.get(User, phone)
            if user is None:
                user = User(phone=phone, created_at=datetime.now(UTC))
            user.agent_name = slots.agent_name
            user.user_name = slots.user_name
            user.help_need = slots.help_need
            user.gmail_status = slots.gmail
            user.gmail_email = slots.gmail_email
            user.graduated = slots.graduated
            user.call_phase = call.phase
            user.call_reason = call.reason
            user.call_id = call.call_id
            user.call_initiated_by = call.initiated_by
            user.call_started_at = call.started_at
            user.call_ended_at = call.ended_at
            user.floor = floor
            s.add(user)
            s.commit()


def ensure_user(engine: Engine, phone: str) -> None:
    with Session(engine) as s:
        if s.get(User, phone) is None:
            s.add(User(phone=phone, created_at=datetime.now(UTC)))
            s.commit()


def load_store(engine: Engine, phone: str, *, sink: SqlSink) -> Store:
    """Rebuild a user's in-memory store from the tables (creating the user if new)."""
    ensure_user(engine, phone)
    with Session(engine) as s:
        user = s.get(User, phone)
        assert user is not None
        rows = s.exec(
            select(EventRow).where(EventRow.user_phone == phone).order_by(EventRow.seq)  # type: ignore[arg-type]
        ).all()
        events = [
            Event(
                seq=r.seq,
                ts=_aware(r.ts),
                origin=r.origin,  # type: ignore[arg-type]
                channel=r.channel,  # type: ignore[arg-type]
                payload=PAYLOAD_ADAPTER.validate_python(r.payload),
            )
            for r in rows
        ]
        slots = Slots(
            agent_name=user.agent_name,
            user_name=user.user_name,
            help_need=user.help_need,
            gmail=user.gmail_status,  # type: ignore[arg-type]
            gmail_email=user.gmail_email,
            graduated=user.graduated,
        )
        # A call that was live at restart is over now: the socket died with the process.
        call = CallState(
            phase=user.call_phase,  # type: ignore[arg-type]
            reason=user.call_reason,
            call_id=user.call_id,
            initiated_by=user.call_initiated_by,  # type: ignore[arg-type]
            started_at=_opt_aware(user.call_started_at),
            ended_at=_opt_aware(user.call_ended_at),
        )
        floor: Floor = "voice" if user.floor == "voice" else "text"
    return Store(phone, sink=sink, events=events, slots=slots, call=call, floor=floor)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _opt_aware(dt: datetime | None) -> datetime | None:
    return None if dt is None else _aware(dt)
