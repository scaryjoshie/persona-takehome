"""Event persistence. Functions take a session and never commit."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import aware
from app.events.event import Event
from app.events.models import EventRow
from app.events.payload import Channel, Origin, Payload
from app.payloads import PAYLOADS


async def append(
    session: AsyncSession,
    phone: str,
    origin: Origin,
    channel: Channel,
    payload: Payload,
    *,
    ts: datetime,
) -> Event:
    """Next seq for this user is max+1; the caller serializes per user."""
    last = (
        await session.exec(select(func.max(EventRow.seq)).where(EventRow.user_phone == phone))
    ).one()
    seq = (last or 0) + 1
    session.add(
        EventRow(
            user_phone=phone,
            seq=seq,
            ts=ts,
            origin=origin.value,
            channel=channel.value,
            kind=payload.kind_name,
            payload=payload.model_dump(mode="json"),
        )
    )
    return Event(seq=seq, ts=ts, origin=origin, channel=channel, payload=payload)


async def list_events(
    session: AsyncSession, phone: str, *, limit: int | None = None
) -> list[Event]:
    """All of a user's events in order, or the most recent `limit` of them."""
    query = select(EventRow).where(EventRow.user_phone == phone)
    if limit is None:
        rows = (await session.exec(query.order_by(col(EventRow.seq)))).all()
    else:
        rows = (await session.exec(query.order_by(col(EventRow.seq).desc()).limit(limit))).all()
        rows = list(reversed(rows))
    return [
        Event(
            seq=r.seq,
            ts=aware(r.ts),
            origin=Origin(r.origin),
            channel=Channel(r.channel),
            payload=PAYLOADS.validate_python(r.payload),
        )
        for r in rows
    ]


async def delete_events(session: AsyncSession, phone: str) -> None:
    """Debug reset: forget a user's whole log."""
    await session.exec(delete(EventRow).where(col(EventRow.user_phone) == phone))  # pyright: ignore[reportArgumentType]
