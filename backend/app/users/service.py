"""User persistence. Functions take a session and never commit."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel.ext.asyncio.session import AsyncSession

from app.users.models import UserRow
from app.users.user import Medium, User
from app.voice.call_state import CallPhase, CallState


async def ensure_user(session: AsyncSession, phone: str, *, now: datetime) -> User:
    """Get the user, creating them if new. Safe when two requests create the same user at
    once (two browser tabs connecting): the insert is skipped if the row already exists."""
    row = await session.get(UserRow, phone)
    if row is None:
        await session.exec(  # pyright: ignore[reportCallIssue, reportArgumentType]
            sqlite_insert(UserRow).values(phone=phone, created_at=now).on_conflict_do_nothing()
        )
        row = await session.get(UserRow, phone)
        assert row is not None
    return User.of(row)


async def get_user(session: AsyncSession, phone: str) -> User | None:
    row = await session.get(UserRow, phone)
    return None if row is None else User.of(row)


async def set_slots(session: AsyncSession, phone: str, **values: Any) -> User:
    """Write slot (or device) columns by name. Enum values are stored as their string value."""
    row = await _row(session, phone)
    for slot, value in values.items():
        setattr(row, slot, value.value if isinstance(value, StrEnum) else value)
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


async def set_typing(session: AsyncSession, phone: str, since: datetime | None) -> User:
    row = await _row(session, phone)
    row.typing_since = since
    await session.flush()
    return User.of(row)


async def _row(session: AsyncSession, phone: str) -> UserRow:
    row = await session.get(UserRow, phone)
    if row is None:
        raise LookupError(f"no user {phone}")
    return row


async def delete_user(session: AsyncSession, phone: str) -> None:
    """Debug reset: remove the user row (their events must be deleted first)."""
    row = await session.get(UserRow, phone)
    if row is not None:
        await session.delete(row)
