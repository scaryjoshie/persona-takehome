"""User persistence. Functions take a session and never commit."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.calls.types import CallPhase, CallState
from app.routing.types import Medium
from app.users.models import UserRow
from app.users.types import User


async def ensure_user(session: AsyncSession, phone: str, *, now: datetime) -> User:
    row = await session.get(UserRow, phone)
    if row is None:
        row = UserRow(phone=phone, created_at=now)
        session.add(row)
        await session.flush()
    return User.of(row)


async def get_user(session: AsyncSession, phone: str) -> User | None:
    row = await session.get(UserRow, phone)
    return None if row is None else User.of(row)


async def set_slot(session: AsyncSession, phone: str, slot: str, value: Any) -> User:
    row = await _row(session, phone)
    setattr(row, slot, value.value if hasattr(value, "value") else value)
    await session.flush()
    return User.of(row)


async def set_call(session: AsyncSession, phone: str, call: CallState) -> User:
    """Sets the call columns and the floor: voice while connected, text otherwise."""
    row = await _row(session, phone)
    row.call_phase = call.phase.value
    row.call_reason = call.reason
    row.call_id = call.call_id
    row.call_initiated_by = call.initiated_by.value if call.initiated_by else None
    row.call_started_at = call.started_at
    row.call_ended_at = call.ended_at
    row.floor = (Medium.VOICE if call.phase is CallPhase.CONNECTED else Medium.TEXT).value
    await session.flush()
    return User.of(row)


async def _row(session: AsyncSession, phone: str) -> UserRow:
    row = await session.get(UserRow, phone)
    if row is None:
        raise LookupError(f"no user {phone}")
    return row
