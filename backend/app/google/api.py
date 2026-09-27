"""Google over plain httpx: sign-in (with a refresh token), Gmail, and Calendar.

Scopes: gmail.modify (read, draft, send; never permanent delete) and calendar.events.
See docs/proposed-design/research/google-oauth-gmail.md.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlencode

import httpx

from app.google.events import InboxItem

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SCOPES = " ".join(
    [
        "openid",
        "email",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/calendar.events",
    ]
)


# ---- sign-in ----------------------------------------------------------------------


def pkce() -> tuple[str, str]:
    """A code verifier and its S256 challenge."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def consent_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    return f"{AUTH_URL}?" + urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",  # a refresh token, so the connection lasts
            "prompt": "consent select_account",
        }
    )


async def exchange(
    client: httpx.AsyncClient,
    *,
    code: str,
    verifier: str,
    creds: tuple[str, str],
    redirect_uri: str,
) -> tuple[str, str, str]:
    """The authorization code → (access token, refresh token, email)."""
    client_id, client_secret = creds
    response = await client.post(
        TOKEN_URL,
        data={
            "code": code,
            "code_verifier": verifier,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    response.raise_for_status()
    body = response.json()
    return body["access_token"], body["refresh_token"], _email_of(body["id_token"])


async def refresh(client: httpx.AsyncClient, refresh_token: str, creds: tuple[str, str]) -> str:
    client_id, client_secret = creds
    response = await client.post(
        TOKEN_URL,
        data={
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


async def revoke(client: httpx.AsyncClient, token: str) -> None:
    await client.post(REVOKE_URL, data={"token": token})  # 400 if already dead: fine


def _email_of(id_token: str) -> str:
    """The email claim. The token came straight from Google over TLS, so it isn't verified."""
    payload = id_token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    return claims["email"]


# ---- Gmail ------------------------------------------------------------------------


async def search(client: httpx.AsyncClient, token: str, query: str, n: int = 10) -> list[InboxItem]:
    """Messages matching a Gmail search (same syntax as the Gmail search box)."""
    auth = {"Authorization": f"Bearer {token}"}
    listed = await client.get(
        f"{GMAIL}/messages", headers=auth, params={"q": query, "maxResults": n}
    )
    listed.raise_for_status()
    ids = [m["id"] for m in listed.json().get("messages", [])]

    async def one(message_id: str) -> InboxItem:
        got = await client.get(
            f"{GMAIL}/messages/{message_id}",
            headers=auth,
            params={"format": "metadata", "metadataHeaders": ["From", "Subject"]},
        )
        got.raise_for_status()
        body = got.json()
        headers = {h["name"]: h["value"] for h in body["payload"].get("headers", [])}
        return InboxItem(
            id=message_id,
            sender=headers.get("From", ""),
            subject=headers.get("Subject", "(no subject)"),
            snippet=body.get("snippet", ""),
        )

    return list(await asyncio.gather(*(one(i) for i in ids)))


async def read(client: httpx.AsyncClient, token: str, message_id: str) -> str:
    """A message's plain-text body (first text/plain part), trimmed."""
    got = await client.get(
        f"{GMAIL}/messages/{message_id}",
        headers={"Authorization": f"Bearer {token}"},
        params={"format": "full"},
    )
    got.raise_for_status()
    body = got.json()
    return (_plain_text(body["payload"]) or body.get("snippet", ""))[:4000]


def _plain_text(part: dict[str, Any]) -> str:
    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(part["body"]["data"]).decode(errors="replace")
    for child in part.get("parts", []):
        if text := _plain_text(child):
            return text
    return ""


async def save_draft(
    client: httpx.AsyncClient,
    token: str,
    *,
    to: str,
    subject: str,
    body: str,
    draft_id: str | None = None,
) -> str:
    """A draft in their Gmail, new or replacing `draft_id`. Returns the draft id."""
    message = EmailMessage()
    message["To"], message["Subject"] = to, subject
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    url, payload = f"{GMAIL}/drafts", {"message": {"raw": raw}}
    auth = {"Authorization": f"Bearer {token}"}
    if draft_id:
        response = await client.put(f"{url}/{draft_id}", headers=auth, json=payload)
    else:
        response = await client.post(url, headers=auth, json=payload)
    response.raise_for_status()
    return response.json()["id"]


async def send_draft(client: httpx.AsyncClient, token: str, draft_id: str) -> None:
    response = await client.post(
        f"{GMAIL}/drafts/send", headers={"Authorization": f"Bearer {token}"}, json={"id": draft_id}
    )
    response.raise_for_status()


# ---- Calendar ---------------------------------------------------------------------


async def upcoming(client: httpx.AsyncClient, token: str, days: int) -> list[dict[str, str]]:
    """Their events from now to `days` ahead: id, title, start, end (ISO)."""
    now = datetime.now(UTC)
    response = await client.get(
        CALENDAR,
        headers={"Authorization": f"Bearer {token}"},
        params={
            "timeMin": now.isoformat(),
            "timeMax": (now + timedelta(days=days)).isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 25,
        },
    )
    response.raise_for_status()
    return [
        {
            "id": e["id"],
            "title": e.get("summary", "(no title)"),
            "start": e["start"].get("dateTime", e["start"].get("date", "")),
            "end": e["end"].get("dateTime", e["end"].get("date", "")),
        }
        for e in response.json().get("items", [])
    ]


async def calendar_timezone(client: httpx.AsyncClient, token: str) -> str:
    """The timezone their Google Calendar is set to (IANA)."""
    response = await client.get(
        "https://www.googleapis.com/calendar/v3/users/me/settings/timezone",
        headers={"Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    return response.json()["value"]


async def create_event(
    client: httpx.AsyncClient, token: str, *, title: str, start: str, end: str
) -> str:
    """An event on their primary calendar (start/end ISO with offset). Returns its link."""
    response = await client.post(
        CALENDAR,
        headers={"Authorization": f"Bearer {token}"},
        json={"summary": title, "start": {"dateTime": start}, "end": {"dateTime": end}},
    )
    response.raise_for_status()
    return response.json().get("htmlLink", "")


async def move_event(
    client: httpx.AsyncClient, token: str, event_id: str, *, start: datetime, minutes: int | None
) -> str:
    """Move an event to `start`, keeping its length unless `minutes` is given. Returns its title."""
    url, auth = f"{CALENDAR}/{event_id}", {"Authorization": f"Bearer {token}"}
    if minutes is None:
        got = await client.get(url, headers=auth)
        got.raise_for_status()
        was = got.json()
        length = _when(was["end"]) - _when(was["start"])
    else:
        length = timedelta(minutes=minutes)
    response = await client.patch(
        url,
        headers=auth,
        params={"sendUpdates": "all"},  # guests hear about it
        json={
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": (start + length).isoformat()},
        },
    )
    response.raise_for_status()
    return response.json().get("summary", "(no title)")


async def cancel_event(client: httpx.AsyncClient, token: str, event_id: str) -> None:
    response = await client.delete(
        f"{CALENDAR}/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
        params={"sendUpdates": "all"},
    )
    response.raise_for_status()


def _when(point: dict[str, str]) -> datetime:
    return datetime.fromisoformat(point.get("dateTime", point.get("date", "")))
