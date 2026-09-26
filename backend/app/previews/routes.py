"""GET /api/preview?url=… → a link card's data. See fetch.py for the guards."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException

from app.previews.fetch import FetchFailed, Preview, PreviewCache, Refused, fetch_preview
from app.services import ServicesDep

GMAIL_LINK = "/api/auth/google/start"


router = APIRouter()
_cache = PreviewCache()
_client = httpx.AsyncClient(timeout=httpx.Timeout(4.0))


@router.get("/api/preview")
async def preview(url: str, svc: ServicesDep) -> Preview:
    if url.startswith(svc.app_base_url + GMAIL_LINK):  # our own link: no fetch needed
        return Preview(
            url=url,
            title="Connect your Google account",
            description="Let your Persona read your Gmail so it can see what needs you.",
            site_name="Persona",
        )
    cached = _cache.get(url)
    if cached is not None:
        return cached
    try:
        result = await fetch_preview(url, _client)
    except Refused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (FetchFailed, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail="could not fetch the page") from exc
    _cache.put(url, result)
    return result
