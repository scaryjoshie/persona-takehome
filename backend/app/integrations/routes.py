"""The secure link a job texts when it needs a secret (an API key, a token, a webhook URL).

GET  /api/secret/{token}   a small form: what to paste and where to get it
POST /api/secret/{token}   stores it encrypted, then the job that asked carries on

The value goes from their browser to the vault; no agent sees it. A link works once.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse

from app.services import ServicesDep

router = APIRouter()


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>body{{font:16px/1.5 -apple-system,system-ui,sans-serif;max-width:420px;margin:48px auto;
padding:0 16px;color:#111;background:#fafafa}}*{{box-sizing:border-box}}h1{{font-size:22px}}
input,button{{display:block;width:100%;padding:12px;margin:10px 0;border-radius:10px;
border:1px solid #ccc;font:inherit}}button{{background:#0a84ff;color:#fff;border-color:#0a84ff}}
small{{color:#666}}</style></head><body><h1>{title}</h1>{body}</body></html>"""
    )


GONE = "<p>This link was already used or has expired. Head back to your texts.</p>"


@router.get("/api/secret/{token}")
async def form(token: str, svc: ServicesDep) -> HTMLResponse:
    request = svc.integrations.request(token) if svc.integrations else None
    if request is None:
        return _page("Link expired", GONE)
    app = html.escape(request.app.title())
    return _page(
        f"Connect {app}",
        f"<p>Paste your {html.escape(request.about)} below.</p>"
        f'<form method="post"><input name="value" type="password" autocomplete="off" required '
        f'placeholder="Paste it here"><button>Save</button></form>'
        "<small>It's stored encrypted and only used for what you ask. Your assistant never "
        "sees it.</small>",
    )


@router.post("/api/secret/{token}")
async def save(token: str, svc: ServicesDep, value: str = Form(...)) -> HTMLResponse:
    done = await svc.integrations.fulfil(token, value.strip()) if svc.integrations else None
    if done is None:
        return _page("Link expired", GONE)
    return _page(
        "Saved ✓",
        f"<p>Got it. Your {html.escape(done.app.title())} is being set up; "
        "head back to your texts.</p>",
    )
