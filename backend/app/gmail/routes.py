"""The Gmail link the agent texts, and what happens when they tap it.

GET  /api/auth/google/start?phone=…   a small page: use a demo inbox, or connect real Gmail
POST /api/auth/google/demo?phone=…    connects the demo inbox
GET  /api/auth/google/real?phone=…    off to Google's consent screen
GET  /api/auth/google/callback        back from Google: read the inbox once, connect

Real Gmail works only for accounts on the Google project's test-user list (the app is in
Testing), so everyone else gets the demo inbox. Either way one "connected" event goes
through the pipeline with a snapshot of the inbox, and the agent takes it from there.
"""

from __future__ import annotations

import html
import logging
import secrets

import httpx
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from app.events.payload import Channel, Origin
from app.gmail import google
from app.gmail.events import GmailEvent, GmailPhase, InboxItem
from app.services import ServicesDep
from app.web.routes import normalize

log = logging.getLogger(__name__)
router = APIRouter()
CALLBACK = "/api/auth/google/callback"

_pending: dict[str, tuple[str, str]] = {}  # state → (phone, PKCE verifier); one process
_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

DEMO_INBOX = [
    InboxItem(sender=sender, subject=subject, snippet=snippet)
    for sender, subject, snippet in [
        (
            "Prof. Alvarez",
            "Problem set 4 due Friday",
            "PS4 is due Friday at 5pm.",
        ),
        ("ConEd", "Your bill is ready: $142.18 due Oct 3", "Your September statement is ready."),
        ("Bright Smiles Dental", "Time for your cleaning", "It's been 7 months since your visit."),
        ("Maria (landlord)", "Re: heater", "I'll send someone next week, sorry for the delay."),
        ("Netflix", "Your membership renews on Oct 1", "Your plan renews for $15.49 on Oct 1."),
        ("Jordan", "dinner saturday?", "still on for saturday? thinking 7 at that thai place"),
        ("UPS", "Your package was delivered", "Delivered to front door at 2:14 PM."),
    ]
]


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>body{{font:16px/1.5 -apple-system,system-ui,sans-serif;max-width:420px;margin:48px auto;
padding:0 16px;color:#111;background:#fafafa}}*{{box-sizing:border-box}}h1{{font-size:22px}}
button,a.btn{{display:block;
width:100%;padding:12px;margin:10px 0;border-radius:10px;border:1px solid #ccc;background:#fff;
font:inherit;text-align:center;text-decoration:none;color:#111;cursor:pointer}}
.primary{{background:#0a84ff;color:#fff;border-color:#0a84ff}}small{{color:#666}}</style>
</head><body><h1>{title}</h1>{body}</body></html>"""
    )


def _done(email: str) -> HTMLResponse:
    return _page(
        "Connected ✓", f"<p>{html.escape(email)} is connected. You can go back to your texts.</p>"
    )


@router.get("/api/auth/google/start")
async def start(phone: str, svc: ServicesDep) -> HTMLResponse:
    phone = html.escape(normalize(phone))
    real = (
        f'<a class="btn" href="/api/auth/google/real?phone={phone}">Connect my real Gmail</a>'
        "<small>Real Gmail only works for Google accounts added as testers of this demo.</small>"
        if svc.google
        else ""
    )
    return _page(
        "Connect Gmail",
        "<p>Your Persona will read your latest inbox headers once, to see what needs you. "
        "It never sends or deletes anything.</p>"
        f'<form method="post" action="/api/auth/google/demo?phone={phone}">'
        '<button class="primary">Use a demo inbox</button></form>' + real,
    )


@router.post("/api/auth/google/demo")
async def demo(phone: str, svc: ServicesDep) -> HTMLResponse:
    connected = GmailEvent(
        phase=GmailPhase.CONNECTED, email="demo inbox", demo=True, inbox=DEMO_INBOX
    )
    await svc.pipeline.submit(normalize(phone), Origin.GOOGLE, Channel.SYSTEM, connected)
    return _done("The demo inbox")


@router.get("/api/auth/google/real", response_model=None)
async def real(phone: str, svc: ServicesDep) -> RedirectResponse | HTMLResponse:
    if not svc.google:
        return _page("Not set up", "<p>Real Gmail isn't configured here. Use the demo inbox.</p>")
    state = secrets.token_urlsafe(24)
    verifier, challenge = google.pkce()
    _pending[state] = (normalize(phone), verifier)
    client_id, _ = svc.google
    return RedirectResponse(
        google.consent_url(client_id, svc.app_base_url + CALLBACK, state, challenge)
    )


@router.get(CALLBACK)
async def callback(
    svc: ServicesDep, state: str = "", code: str | None = None, error: str | None = None
) -> HTMLResponse:
    pending = _pending.pop(state, None)
    if pending is None or svc.google is None:
        return _page("Link expired", "<p>Go back to your texts and ask for a new link.</p>")
    phone, verifier = pending
    if error or not code:  # they said no, or closed the consent screen
        await _failed(svc, phone)
        return _page("Not connected", "<p>No worries, nothing was connected.</p>")
    client_id, client_secret = svc.google
    try:
        token, email = await google.exchange(
            _client,
            code=code,
            verifier=verifier,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=svc.app_base_url + CALLBACK,
        )
        inbox = await google.peek(_client, token)
    except (httpx.HTTPError, KeyError, ValueError):
        log.exception("%s: Gmail connect failed", phone)
        await _failed(svc, phone)
        return _page("Something went wrong", "<p>That didn't go through. Try the link again.</p>")
    connected = GmailEvent(phase=GmailPhase.CONNECTED, email=email, inbox=inbox)
    await svc.pipeline.submit(phone, Origin.GOOGLE, Channel.SYSTEM, connected)
    return _done(email)


async def _failed(svc: ServicesDep, phone: str) -> None:
    await svc.pipeline.submit(
        phone, Origin.GOOGLE, Channel.SYSTEM, GmailEvent(phase=GmailPhase.FAILED)
    )
