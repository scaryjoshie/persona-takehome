"""GET /api/preview?url=… → a link card's data. See fetch.py for the guards."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException

from app.previews.fetch import FetchFailed, Preview, PreviewCache, Refused, fetch_preview


def make_router() -> APIRouter:
    router = APIRouter()
    cache = PreviewCache()
    client = httpx.AsyncClient(timeout=httpx.Timeout(4.0))

    @router.get("/api/preview")
    async def preview(url: str) -> Preview:
        cached = cache.get(url)
        if cached is not None:
            return cached
        try:
            result = await fetch_preview(url, client)
        except Refused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (FetchFailed, httpx.HTTPError) as exc:
            raise HTTPException(status_code=502, detail="could not fetch the page") from exc
        cache.put(url, result)
        return result

    return router
