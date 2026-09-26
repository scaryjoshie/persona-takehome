"""A user's connected Google account, real or demo, behind one small interface the agent's
tools use. Real accounts keep a Fernet-encrypted refresh token in the google_account table
and refresh access on demand. The demo account lives in memory (writes are simulated) so
anyone can try drafting, sending and scheduling without a real Google test account.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx
from cryptography.fernet import Fernet

from app.agent.slots import Slots
from app.database import SessionFactory, utc_now
from app.google import api
from app.google.events import GmailPhase, InboxItem
from app.google.models import GoogleAccountRow

DEMO_EMAIL = "demo inbox"
DEFAULT_TZ = "America/Chicago"


class Account(Protocol):
    demo: bool

    async def search(self, query: str) -> list[InboxItem]: ...
    async def read(self, message_id: str) -> str: ...
    async def draft(self, *, to: str, subject: str, body: str) -> str: ...
    async def send(self, draft_id: str) -> None: ...
    async def upcoming(self, days: int) -> list[dict[str, str]]: ...
    async def create_event(self, *, title: str, start: datetime, end: datetime) -> None: ...


class Google:
    def __init__(
        self,
        db: SessionFactory,
        *,
        creds: tuple[str, str] | None = None,
        key: str | None = None,
        tz: str = DEFAULT_TZ,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.creds = creds  # OAuth client id and secret; None = demo only
        self.tz = ZoneInfo(tz)
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._db = db
        self._fernet = Fernet(key) if key else None
        self._tokens: dict[str, tuple[str, float]] = {}  # phone → (access token, expires)
        self._demos: dict[str, DemoAccount] = {}

    @property
    def real(self) -> bool:
        """Can real Google accounts connect here?"""
        return self.creds is not None and self._fernet is not None

    async def save(self, phone: str, email: str, refresh_token: str) -> None:
        assert self._fernet is not None
        async with self._db() as s, s.begin():
            row = await s.get(GoogleAccountRow, phone) or GoogleAccountRow(phone=phone)
            row.email, row.connected_at = email, utc_now()
            row.refresh_token = self._fernet.encrypt(refresh_token.encode()).decode()
            s.add(row)

    async def disconnect(self, phone: str) -> None:
        """Revoke and forget. Also clears a demo account."""
        self._demos.pop(phone, None)
        self._tokens.pop(phone, None)
        async with self._db() as s, s.begin():
            row = await s.get(GoogleAccountRow, phone)
            if row is None:
                return
            refresh_token = self._decrypt(row.refresh_token)
            await s.delete(row)
        await api.revoke(self.client, refresh_token)

    async def account(self, phone: str, slots: Slots) -> Account | None:
        """Their connected account, or None if Gmail isn't connected."""
        if slots.gmail is not GmailPhase.CONNECTED:
            return None
        if slots.gmail_email == DEMO_EMAIL:
            return self._demos.setdefault(phone, DemoAccount(self.tz))
        return RealAccount(self, phone) if self.real else None

    async def access_token(self, phone: str) -> str:
        cached = self._tokens.get(phone)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        assert self.creds is not None
        async with self._db() as s:
            row = await s.get(GoogleAccountRow, phone)
        if row is None:
            raise LookupError("no Google account stored")
        token = await api.refresh(self.client, self._decrypt(row.refresh_token), self.creds)
        self._tokens[phone] = (token, time.monotonic() + 50 * 60)  # tokens last an hour
        return token

    def _decrypt(self, value: str) -> str:
        assert self._fernet is not None
        return self._fernet.decrypt(value.encode()).decode()


@dataclass
class RealAccount:
    google: Google
    phone: str
    demo: bool = False

    async def search(self, query: str) -> list[InboxItem]:
        return await api.search(self.google.client, await self._token(), query)

    async def read(self, message_id: str) -> str:
        return await api.read(self.google.client, await self._token(), message_id)

    async def draft(self, *, to: str, subject: str, body: str) -> str:
        token = await self._token()
        return await api.create_draft(self.google.client, token, to=to, subject=subject, body=body)

    async def send(self, draft_id: str) -> None:
        await api.send_draft(self.google.client, await self._token(), draft_id)

    async def upcoming(self, days: int) -> list[dict[str, str]]:
        return await api.upcoming(self.google.client, await self._token(), days)

    async def create_event(self, *, title: str, start: datetime, end: datetime) -> None:
        token = await self._token()
        await api.create_event(
            self.google.client, token, title=title, start=start.isoformat(), end=end.isoformat()
        )

    async def _token(self) -> str:
        return await self.google.access_token(self.phone)


DEMO_INBOX = [
    ("Prof. Alvarez", "Problem set 4 due Friday", "PS4 is due Friday at 5pm."),
    ("ConEd", "Your bill is ready: $142.18 due Oct 3", "Your September statement is ready."),
    ("Bright Smiles Dental", "Time for your cleaning", "It's been 7 months since your visit."),
    ("Maria (landlord)", "Re: heater", "I'll send someone next week, sorry for the delay."),
    ("Netflix", "Your membership renews on Oct 1", "Your plan renews for $15.49 on Oct 1."),
    ("Jordan", "dinner saturday?", "still on for saturday? thinking 7 at that thai place"),
    ("UPS", "Your package was delivered", "Delivered to front door at 2:14 PM."),
]


@dataclass
class DemoAccount:
    """Sample mail and a sample week. Drafts, sends and new events are kept in memory."""

    tz: ZoneInfo
    demo: bool = True
    inbox: list[InboxItem] = field(default_factory=lambda: [])
    drafts: dict[str, tuple[str, str, str]] = field(default_factory=lambda: {})
    sent: list[str] = field(default_factory=lambda: [])
    events: list[dict[str, str]] = field(default_factory=lambda: [])

    def __post_init__(self) -> None:
        self.inbox = [
            InboxItem(id=f"demo{i}", sender=f, subject=s, snippet=p)
            for i, (f, s, p) in enumerate(DEMO_INBOX)
        ]
        today = datetime.now(self.tz).replace(minute=0, second=0, microsecond=0)
        for days, hour, title in [(1, 10, "Team standup"), (2, 15, "Office hours, Prof. Alvarez")]:
            start = (today + timedelta(days=days)).replace(hour=hour)
            self._add(title, start, start + timedelta(hours=1))

    async def search(self, query: str) -> list[InboxItem]:
        words = [w for w in query.lower().split() if ":" not in w]  # Gmail operators: ignore
        hits = [
            m
            for m in self.inbox
            if all(w in f"{m.sender} {m.subject} {m.snippet}".lower() for w in words)
        ]
        return hits or self.inbox

    async def read(self, message_id: str) -> str:
        found = next((m for m in self.inbox if m.id == message_id), None)
        return f"{found.subject}\n\n{found.snippet}" if found else "no such message"

    async def draft(self, *, to: str, subject: str, body: str) -> str:
        draft_id = f"draft-{uuid.uuid4().hex[:6]}"
        self.drafts[draft_id] = (to, subject, body)
        return draft_id

    async def send(self, draft_id: str) -> None:
        if draft_id not in self.drafts:
            raise LookupError("no such draft")
        self.sent.append(draft_id)
        del self.drafts[draft_id]

    async def upcoming(self, days: int) -> list[dict[str, str]]:
        return self.events

    async def create_event(self, *, title: str, start: datetime, end: datetime) -> None:
        self._add(title, start, end)

    def _add(self, title: str, start: datetime, end: datetime) -> None:
        self.events.append({"title": title, "start": start.isoformat(), "end": end.isoformat()})
