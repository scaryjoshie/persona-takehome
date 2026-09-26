"""The Google link the agent texts, and what happens when they tap it.

GET  /api/auth/google/start?phone=…   a small page: demo account, or connect real Google
POST /api/auth/google/demo?phone=…    connects the demo inbox and calendar
GET  /api/auth/google/real?phone=…    off to Google's consent screen
GET  /api/auth/google/callback        back from Google: keep the connection, peek at the inbox

Real Google works only for accounts on the project's test-user list (the app is in Testing),
so everyone else gets the demo account. Either way one "connected" event goes through the
pipeline with a look at the latest inbox, and the agent takes it from there.
"""

from __future__ import annotations

import html
import logging
import secrets

import httpx
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from app.events.payload import Channel, Origin
from app.google import api
from app.google.accounts import DEMO_EMAIL, DemoAccount
from app.google.events import GmailEvent, GmailPhase
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
button,a.btn{{display:block;width:100%;padding:12px;margin:10px 0;border-radius:10px;
border:1px solid #ccc;background:#fff;font:inherit;text-align:center;text-decoration:none;
color:#111;cursor:pointer}}.primary{{background:#0a84ff;color:#fff;border-color:#0a84ff}}
small{{color:#666}}</style></head><body><h1>{title}</h1>{body}</body></html>"""
    )


def _done(who: str) -> HTMLResponse:
    return _page("Connected ✓", f"<p>{html.escape(who)} is connected. Head back to your texts.</p>")


@router.get("/api/auth/google/start")
async def start(phone: str, svc: ServicesDep) -> HTMLResponse:
    phone = html.escape(normalize(phone))
    real = (
        f'<a class="btn" href="/api/auth/google/real?phone={phone}">Connect my Google account</a>'
        "<small>Real accounts only work for Google accounts added as testers of this demo.</small>"
        if svc.google.real
        else ""
    )
    return _page(
        "Connect Gmail & Calendar",
        "<p>Your Persona gets access to your Gmail and Google Calendar so it can find what "
        "needs you, draft replies, and add events. It never sends an email or books "
        "anything without asking you first, and it can't permanently delete mail. You can "
        "disconnect anytime by telling it to.</p>"
        f'<form method="post" action="/api/auth/google/demo?phone={phone}">'
        '<button class="primary">Use a demo account</button></form>' + real,
    )


@router.post("/api/auth/google/demo")
async def demo(phone: str, svc: ServicesDep) -> HTMLResponse:
    inbox = await DemoAccount(svc.google.tz).search(PEEK)
    connected = GmailEvent(phase=GmailPhase.CONNECTED, email=DEMO_EMAIL, demo=True, inbox=inbox)
    await svc.pipeline.submit(normalize(phone), Origin.GOOGLE, Channel.SYSTEM, connected)
    return _done("The demo inbox and calendar")


@router.get("/api/auth/google/real", response_model=None)
async def real(phone: str, svc: ServicesDep) -> RedirectResponse | HTMLResponse:
    if not svc.google.real or svc.google.creds is None:
        return _page("Not set up", "<p>Real Google isn't configured here. Use the demo.</p>")
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
    connected = GmailEvent(phase=GmailPhase.CONNECTED, email=email, inbox=inbox)
    await svc.pipeline.submit(phone, Origin.GOOGLE, Channel.SYSTEM, connected)
    return _done(email)


async def _failed(svc: ServicesDep, phone: str) -> None:
    failed = GmailEvent(phase=GmailPhase.FAILED)
    await svc.pipeline.submit(phone, Origin.GOOGLE, Channel.SYSTEM, failed)
