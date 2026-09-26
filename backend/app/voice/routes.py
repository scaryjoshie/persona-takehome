"""The call's audio socket: PCM16 mono 24 kHz both ways. Closing it hangs up."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket

from app.services import ServicesDep
from app.voice.call_state import CallPhase
from app.web.routes import normalize

router = APIRouter()

BUSY = 4409  # a call is already running for this user (another tab)
NO_CALL = 4400  # no call is being connected; send accept or start first


@router.websocket("/ws/audio")
async def audio(websocket: WebSocket, phone: str, svc: ServicesDep) -> None:
    phone = normalize(phone)
    await websocket.accept()  # accept first: a socket closed before accepting reads as HTTP 403,
    # and the browser would never see the close code saying why
    if phone in svc.voice.calls:
        await websocket.close(code=BUSY)
        return
    if (await svc.pipeline.user(phone)).call.phase is not CallPhase.CONNECTING:
        await websocket.close(code=NO_CALL)
        return
    await svc.run_call(websocket, phone)
