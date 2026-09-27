"""The Google link the agent texts, and what happens when they tap it.

GET /api/auth/google/start?phone=…   straight to Google's consent screen
GET /api/auth/google/callback        back from Google: keep the connection, peek at the inbox
GET /api/drafts/{phone}/{ref}.svg     a draft email as a picture, texted to them as an image

The Google project is in Testing mode, so only accounts on its test-user list can connect.
A successful connect sends one "connected" event through the pipeline with a look at the
latest inbox, and the agent takes it from there.
"""

from __future__ import annotations

import html
import logging
import secrets
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.agent.events import TimezoneLearned
from app.agent.slots import TzSource
from app.events.payload import Channel, Origin
from app.google import api, preview
from app.google.events import EmailDraft, GmailEvent, GmailPhase
from app.services import ServicesDep
from app.web.routes import normalize

log = logging.getLogger(__name__)
router = APIRouter()
CALLBACK = "/api/auth/google/callback"
PEEK = "in:inbox -category:promotions -category:social"

_pending: dict[str, tuple[str, str]] = {}  # state → (phone, PKCE verifier); one process


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>body{{font:16px/1.5 -apple-system,system-ui,sans-serif;max-width:420px;margin:48px auto;
padding:0 16px;color:#111;background:#fafafa}}*{{box-sizing:border-box}}h1{{font-size:22px}}
</style></head><body><h1>{title}</h1>{body}</body></html>"""
    )


def _done(who: str) -> HTMLResponse:
    return _page("Connected ✓", f"<p>{html.escape(who)} is connected. Head back to your texts.</p>")


@router.get("/api/auth/google/start", response_model=None)
async def start(phone: str, svc: ServicesDep) -> RedirectResponse | HTMLResponse:
    if not svc.google.configured or svc.google.creds is None:
        return _page("Not set up", "<p>Google sign-in isn't configured on this server.</p>")
    state = secrets.token_urlsafe(24)
    verifier, challenge = api.pkce()
    _pending[state] = (normalize(phone), verifier)
    client_id, _ = svc.google.creds
    return RedirectResponse(
        api.consent_url(client_id, svc.app_base_url + CALLBACK, state, challenge)
    )


@router.get(CALLBACK)
async def callback(
    svc: ServicesDep, state: str = "", code: str | None = None, error: str | None = None
) -> HTMLResponse:
    pending, creds = _pending.pop(state, None), svc.google.creds
    if pending is None or creds is None:
        return _page("Link expired", "<p>Go back to your texts and ask for a new link.</p>")
    phone, verifier = pending
    if error or not code:  # they said no, or closed the consent screen
        await _failed(svc, phone)
        return _page("Not connected", "<p>No worries, nothing was connected.</p>")
    google = svc.google
    try:
        token, refresh_token, email = await api.exchange(
            google.client,
            code=code,
            verifier=verifier,
            creds=creds,
            redirect_uri=svc.app_base_url + CALLBACK,
        )
        await google.save(phone, email, refresh_token)
        inbox = await api.search(google.client, token, PEEK, n=15)
    except (httpx.HTTPError, KeyError, ValueError):
        log.exception("%s: Google connect failed", phone)
        await _failed(svc, phone)
        return _page("Something went wrong", "<p>That didn't go through. Try the link again.</p>")
    await _learn_timezone(svc, phone, token)  # before "connected", so the reply knows it
    connected = GmailEvent(phase=GmailPhase.CONNECTED, email=email, inbox=inbox)
    await svc.pipeline.submit(phone, Origin.GOOGLE, Channel.SYSTEM, connected)
    return _done(email)


async def _learn_timezone(svc: ServicesDep, phone: str, token: str) -> None:
    """Their calendar's timezone, if Google says. Without it, the default guess stands."""
    try:
        tz = await api.calendar_timezone(svc.google.client, token)
        ZoneInfo(tz)
    except (httpx.HTTPError, KeyError, ValueError, ZoneInfoNotFoundError):
        log.warning("%s: couldn't read their calendar's timezone", phone)
        return
    learned = TimezoneLearned(tz=tz, source=TzSource.CALENDAR)
    await svc.pipeline.submit(phone, Origin.GOOGLE, Channel.SYSTEM, learned)


async def _failed(svc: ServicesDep, phone: str) -> None:
    failed = GmailEvent(phase=GmailPhase.FAILED)
    await svc.pipeline.submit(phone, Origin.GOOGLE, Channel.SYSTEM, failed)


@router.get("/api/drafts/{phone}/{ref}.svg")
async def draft_image(phone: str, ref: str, svc: ServicesDep, v: int | None = None) -> Response:
    """The picture of one version of a draft (the latest if no version is given)."""
    versions = [
        e
        for e in await svc.pipeline.history(normalize(phone))
        if isinstance(e.payload, EmailDraft) and e.payload.ref == ref and (v is None or e.seq <= v)
    ]
    if not versions:
        return Response(status_code=404)
    draft = versions[-1].payload
    assert isinstance(draft, EmailDraft)
    return Response(preview.render(draft), media_type="image/svg+xml")
