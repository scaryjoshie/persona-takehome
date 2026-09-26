"""The call's audio socket: PCM16 mono 24 kHz both ways. Closing it hangs up."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import APIRouter, WebSocket

from app.events.payload import Channel, Origin
from app.services import ServicesDep
from app.voice.call_state import CallEvent, CallPhase, CallTransition
from app.web.routes import normalize

router = APIRouter()

BUSY = 4409  # a call is already running for this user (another tab)
NO_CALL = 4400  # no call is being connected; send accept or start first
LIMITED = 4429  # this IP has had its calls for the day
DAY = 24 * 3600.0

_calls_by_ip: defaultdict[str, deque[float]] = defaultdict(deque)  # start times, last DAY


def over_call_limit(ip: str, limit: int) -> bool:
    now, starts = time.monotonic(), _calls_by_ip[ip]
    while starts and now - starts[0] > DAY:
        starts.popleft()
    if len(starts) >= limit:
        return True
    starts.append(now)
    return False


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
    ip = websocket.client.host if websocket.client else "unknown"
    if over_call_limit(ip, svc.calls_per_ip_per_day):
        failed = CallEvent(transition=CallTransition.FAILED, reason="call_limit")
        await svc.pipeline.submit(phone, Origin.CALL, Channel.SYSTEM, failed)
        await websocket.close(code=LIMITED)
        return
    await svc.run_call(websocket, phone)
