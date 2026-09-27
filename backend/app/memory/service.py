"""Fact and summary persistence. Functions take a session and never commit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.memory.models import FactRow, SummaryRow

MAX_FACTS = 50  # the most recent ones, if it ever remembers more


@dataclass(frozen=True)
class Fact:
    id: int
    text: str


@dataclass(frozen=True)
class Summary:
    through_seq: int
    text: str


@dataclass(frozen=True)
class Memory:
    """What the agent carries beyond the latest messages: the summary of everything before
    them, and the facts it remembered."""

    summary: Summary | None
    facts: tuple[Fact, ...]

    @property
    def after_seq(self) -> int:
        """Events after this one are the conversation word for word."""
        return self.summary.through_seq if self.summary else 0


async def add_fact(session: AsyncSession, phone: str, text: str, *, now: datetime) -> int | None:
    """The new fact's id, or None if it's already remembered."""
    same = select(FactRow).where(
        FactRow.user_phone == phone, FactRow.text == text, col(FactRow.forgotten_at).is_(None)
    )
    if (await session.exec(same)).first() is not None:
        return None
    row = FactRow(user_phone=phone, text=text, created_at=now)
    session.add(row)
    await session.flush()
    return row.id


async def forget_fact(
    session: AsyncSession, phone: str, fact_id: int, *, now: datetime
) -> str | None:
    """The forgotten fact's text, or None if they have no such fact."""
    row = await session.get(FactRow, fact_id)
    if row is None or row.user_phone != phone or row.forgotten_at is not None:
        return None
    row.forgotten_at = now
    await session.flush()
    return row.text


async def memory(session: AsyncSession, phone: str) -> Memory:
    rows = await session.exec(
        select(FactRow)
        .where(FactRow.user_phone == phone, col(FactRow.forgotten_at).is_(None))
        .order_by(col(FactRow.id).desc())
        .limit(MAX_FACTS)
    )
    facts = tuple(Fact(r.id, r.text) for r in reversed(rows.all()) if r.id is not None)
    row = await session.get(SummaryRow, phone)
    summary = Summary(row.through_seq, row.text) if row else None
    return Memory(summary, facts)


async def set_summary(
    session: AsyncSession, phone: str, summary: Summary, *, now: datetime
) -> None:
    row = await session.get(SummaryRow, phone)
    if row is None:
        session.add(
            SummaryRow(
                user_phone=phone, through_seq=summary.through_seq, text=summary.text, updated_at=now
            )
        )
    else:
        row.through_seq, row.text, row.updated_at = summary.through_seq, summary.text, now
    await session.flush()


async def delete_memory(session: AsyncSession, phone: str | None = None) -> None:
    """Debug reset: forget their facts and summary (everyone's, with no phone)."""
    for table in (FactRow, SummaryRow):
        query = delete(table)
        if phone is not None:
            query = query.where(col(table.user_phone) == phone)
        await session.exec(query)  # pyright: ignore[reportArgumentType]
