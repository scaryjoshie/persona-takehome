"""A user's connected Google account, for the agent's email and calendar tools. The refresh
token is kept Fernet-encrypted in the google_account table; access tokens are refreshed on
demand and cached for their hour.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

import httpx
from cryptography.fernet import Fernet

from app.agent.slots import Slots
from app.database import SessionFactory, utc_now
from app.google import api
from app.google.events import GmailPhase, InboxItem
from app.google.models import GoogleAccountRow


class Google:
    def __init__(
        self,
        db: SessionFactory,
        *,
        creds: tuple[str, str] | None = None,  # OAuth client id and secret
        key: str | None = None,  # Fernet key for stored tokens
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.creds = creds
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._db = db
        self._fernet = Fernet(key) if key else None
        self._tokens: dict[str, tuple[str, float]] = {}  # phone → (access token, expires)

    @property
    def configured(self) -> bool:
        return self.creds is not None and self._fernet is not None

    async def save(self, phone: str, email: str, refresh_token: str) -> None:
        assert self._fernet is not None
        async with self._db() as s, s.begin():
            row = await s.get(GoogleAccountRow, phone) or GoogleAccountRow(phone=phone)
            row.email, row.connected_at = email, utc_now()
            row.refresh_token = self._fernet.encrypt(refresh_token.encode()).decode()
            s.add(row)

    async def disconnect(self, phone: str) -> None:
        """Revoke access and forget the token."""
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
        if slots.gmail is not GmailPhase.CONNECTED or not self.configured:
            return None
        async with self._db() as s:
            stored = await s.get(GoogleAccountRow, phone)
        return Account(self, phone) if stored else None  # e.g. connected before tokens were kept

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
class Account:
    google: Google
    phone: str

    async def search(self, query: str) -> list[InboxItem]:
        return await api.search(self.google.client, await self._token(), query)

    async def read(self, message_id: str) -> str:
        return await api.read(self.google.client, await self._token(), message_id)

    async def draft(self, *, to: str, subject: str, body: str, draft_id: str | None = None) -> str:
        token = await self._token()
        return await api.save_draft(
            self.google.client, token, to=to, subject=subject, body=body, draft_id=draft_id
        )

    async def send(self, draft_id: str) -> None:
        await api.send_draft(self.google.client, await self._token(), draft_id)

    async def upcoming(self, days: int) -> list[dict[str, str]]:
        return await api.upcoming(self.google.client, await self._token(), days)

    async def create_event(self, *, title: str, start: datetime, end: datetime) -> None:
        token = await self._token()
        await api.create_event(
            self.google.client, token, title=title, start=start.isoformat(), end=end.isoformat()
        )

    async def move_event(self, event_id: str, *, start: datetime, minutes: int | None) -> str:
        token = await self._token()
        return await api.move_event(
            self.google.client, token, event_id, start=start, minutes=minutes
        )

    async def cancel_event(self, event_id: str) -> None:
        await api.cancel_event(self.google.client, await self._token(), event_id)

    async def _token(self) -> str:
        return await self.google.access_token(self.phone)
