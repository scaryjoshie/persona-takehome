"""Start everyone over: every user's conversation and progress, their connected Google
accounts (revoked at Google, not just forgotten), and uploaded voice notes.

    uv run python -m app.reset          # says what would go
    uv run python -m app.reset --yes    # does it; the database is backed up first

On Fly: fly ssh console -a persona-onboarding -C "python -m app.reset --yes"
It refuses while a call is in progress (a call that started over 11 minutes ago is stale).
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, func
from sqlmodel import col, select

from app.database import create_schema, make_engine, make_sessions
from app.events.models import EventRow
from app.google.accounts import Google
from app.google.models import GoogleAccountRow
from app.integrations.models import IntegrationRow
from app.jobs.models import JobRow
from app.memory.service import delete_memory
from app.settings import get_settings
from app.users.models import UserRow

LIVE_CALL = ("ringing", "connecting", "connected")
STALE_CALL = timedelta(minutes=11)  # calls are capped at 10 minutes


async def main(really: bool) -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    await create_schema(engine)
    db = make_sessions(engine)
    async with db() as s:
        users = (await s.exec(select(func.count()).select_from(UserRow))).one()
        events = (await s.exec(select(func.count()).select_from(EventRow))).one()
        phones = list((await s.exec(select(GoogleAccountRow.phone))).all())
        calls = await s.exec(select(UserRow).where(col(UserRow.call_phase).in_(LIVE_CALL)))
        cutoff = datetime.now(UTC).replace(tzinfo=None) - STALE_CALL
        live = [u.phone for u in calls if u.call_started_at and u.call_started_at > cutoff]
    notes = Path(settings.data_dir) / "voice_notes"
    note_files = list(notes.glob("*")) if notes.exists() else []
    print(
        f"{users} users, {events} events, {len(phones)} Google accounts, "
        f"{len(note_files)} voice notes"
    )
    if live:
        sys.exit(f"a call is in progress ({', '.join(live)}); try again when it's over")
    if not really:
        print("nothing changed; run with --yes to reset")
        return

    backup = _backup(settings.database_url)
    if backup:
        print(f"backed up the database to {backup}")
    google = Google(
        db,
        creds=(settings.google_client_id, settings.google_client_secret.get_secret_value())
        if settings.google_client_id and settings.google_client_secret
        else None,
        key=settings.credentials_key.get_secret_value() if settings.credentials_key else None,
    )
    for phone in phones:  # revoke at Google, then forget the token
        try:
            await google.disconnect(phone)
        except Exception as exc:  # a dead token or a changed key: forget it anyway
            print(f"couldn't revoke {phone}'s Google access ({type(exc).__name__}); forgetting it")
            async with db() as s, s.begin():
                gone = delete(GoogleAccountRow).where(col(GoogleAccountRow.phone) == phone)
                await s.exec(gone)  # pyright: ignore[reportArgumentType]
    async with db() as s, s.begin():
        await s.exec(delete(EventRow))  # pyright: ignore[reportArgumentType]
        await delete_memory(s)
        await s.exec(delete(UserRow))  # pyright: ignore[reportArgumentType]
        await s.exec(delete(JobRow))  # pyright: ignore[reportArgumentType]
        await s.exec(delete(IntegrationRow))  # pyright: ignore[reportArgumentType]
    for path in note_files:
        path.unlink(missing_ok=True)
    await engine.dispose()
    print("reset: everyone starts fresh")


def _backup(database_url: str) -> Path | None:
    """A copy of a SQLite database next to it; None for anything else."""
    path = database_url.split(":///", 1)[1] if ":///" in database_url else None
    if path is None or not Path(path).exists():
        return None
    target = Path(f"{path}.bak-reset-{time.strftime('%Y%m%d-%H%M%S')}")
    with sqlite3.connect(path) as source, sqlite3.connect(target) as copy:
        source.backup(copy)  # consistent even while the server is writing
    return target


if __name__ == "__main__":
    asyncio.run(main(really="--yes" in sys.argv[1:]))
