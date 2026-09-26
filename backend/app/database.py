"""One async engine and one session factory. Tables live in each section's models
module; this file only knows how to open sessions and create the schema."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from importlib import import_module

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

SQLModel.metadata.naming_convention = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
).naming_convention

SessionFactory = Callable[[], AsyncSession]


def make_engine(database_url: str) -> AsyncEngine:
    """Like sqlite+aiosqlite:///./onboarding.db; a plain sqlite:// URL is upgraded."""
    if database_url.startswith("sqlite:///"):
        database_url = "sqlite+aiosqlite:///" + database_url.removeprefix("sqlite:///")
    return create_async_engine(database_url)


def make_sessions(engine: AsyncEngine) -> SessionFactory:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def create_schema(engine: AsyncEngine) -> None:
    for module in ("app.events.models", "app.users.models", "app.google.models"):
        import_module(module)  # register each section's tables on the metadata
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


def utc_now() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime) -> datetime:
    """SQLite drops timezones; everything we store is UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
