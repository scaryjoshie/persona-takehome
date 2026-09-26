"""Google sign-in and one look at the inbox, over plain httpx.

We ask for gmail.readonly, read the latest inbox headers once, and keep only that snapshot.
Tokens are used for that one read and then dropped: onboarding never reads the inbox again.
See docs/proposed-design/research/google-oauth-gmail.md.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from urllib.parse import urlencode

import httpx

from app.gmail.events import InboxItem

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
SCOPES = "openid email https://www.googleapis.com/auth/gmail.readonly"
PEEK = 15  # inbox messages to look at


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
            "prompt": "consent select_account",
        }
    )


async def exchange(
    client: httpx.AsyncClient,
    *,
    code: str,
    verifier: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> tuple[str, str]:
    """The authorization code → (access token, email)."""
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
    return body["access_token"], _email_of(body["id_token"])


def _email_of(id_token: str) -> str:
    """The email claim. The token came straight from Google over TLS, so it isn't verified."""
    payload = id_token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    return claims["email"]


async def peek(client: httpx.AsyncClient, token: str) -> list[InboxItem]:
    """The latest inbox messages: sender, subject, snippet."""
    auth = {"Authorization": f"Bearer {token}"}
    listed = await client.get(
        GMAIL,
        headers=auth,
        params={
            "maxResults": PEEK,
            "labelIds": "INBOX",
            "q": "-category:promotions -category:social",
        },
    )
    listed.raise_for_status()
    ids = [m["id"] for m in listed.json().get("messages", [])]

    async def one(message_id: str) -> InboxItem:
        got = await client.get(
            f"{GMAIL}/{message_id}",
            headers=auth,
            params={"format": "metadata", "metadataHeaders": ["From", "Subject"]},
        )
        got.raise_for_status()
        body = got.json()
        headers = {h["name"]: h["value"] for h in body["payload"].get("headers", [])}
        return InboxItem(
            sender=headers.get("From", ""),
            subject=headers.get("Subject", "(no subject)"),
            snippet=body.get("snippet", ""),
        )

    return list(await asyncio.gather(*(one(i) for i in ids)))
