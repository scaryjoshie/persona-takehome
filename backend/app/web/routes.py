"""HTTP and WebSocket endpoints for the browser. Thin: every change goes through the pipeline."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from app.calls.events import CallEvent, CallTransition, Initiator
from app.calls.state import CallPhase
from app.events.event import Event
from app.events.payload import Channel, Origin
from app.pipeline import Pipeline
from app.text.events import Typing, UserMessage
from app.web.protocol import (
    CLIENT_MESSAGE,
    CallAction,
    CallCommand,
    CallMessage,
    EventMessage,
    Reset,
    SendMessage,
    SetTyping,
    SlotsMessage,
    Snapshot,
    WireEvent,
)
from app.web.sockets import Sockets

log = logging.getLogger(__name__)

STATE_KINDS = {"slot_changed", "call"}  # events after which the browser needs fresh state


class SessionRequest(BaseModel):
    phone: str


CallRunner = Callable[[WebSocket, str], Awaitable[None]]

AUDIO_BUSY = 4409  # a call is already running for this user (another tab)
AUDIO_NO_CALL = 4400  # no call is being connected; send accept or start first


def make_router(pipeline: Pipeline, sockets: Sockets, run_call: CallRunner) -> APIRouter:
    router = APIRouter()
    calls_running: set[str] = set()

    async def snapshot(phone: str) -> Snapshot:
        user = await pipeline.user(phone)
        history = await pipeline.history(phone)
        return Snapshot(
            events=[WireEvent.of(e) for e in history],
            slots=user.slots,
            call=user.call,
            floor=user.floor,
        )

    @router.post("/api/session")
    async def session(body: SessionRequest) -> Snapshot:
        return await snapshot(normalize(body.phone))

    @router.websocket("/ws")
    async def ws(websocket: WebSocket, phone: str) -> None:
        phone = normalize(phone)
        await websocket.accept()
        sockets.add(phone, websocket)
        live = pipeline.live_users.get(phone)

        async def on_event(event: Event) -> None:
            await sockets.push(phone, EventMessage(event=WireEvent.of(event)))
            if event.kind in STATE_KINDS:
                user = await pipeline.user(phone)
                await sockets.push(phone, SlotsMessage(slots=user.slots))
                await sockets.push(phone, CallMessage(call=user.call))

        unsubscribe = live.subscribe(on_event)
        typing_since: float | None = None
        try:
            await websocket.send_text((await snapshot(phone)).model_dump_json())
            while True:
                raw = await websocket.receive_text()
                try:
                    message = CLIENT_MESSAGE.validate_json(raw)
                except ValidationError as exc:
                    log.info("%s: bad message %s", phone, exc.errors()[:1])
                    continue
                match message:
                    case SendMessage(text=text):
                        typing_since = None
                        await pipeline.submit(
                            phone, Origin.USER, Channel.TEXT, UserMessage(text=text)
                        )
                    case SetTyping(active=active):
                        now = time.monotonic()
                        typing_since = (typing_since or now) if active else None
                        seconds = now - typing_since if typing_since else 0.0
                        typing = Typing(active=active, seconds=seconds)
                        await pipeline.submit(phone, Origin.USER, Channel.TEXT, typing)
                    case CallCommand():
                        await pipeline.submit(
                            phone, Origin.USER, Channel.SYSTEM, call_event(message)
                        )
                    case Reset():
                        await pipeline.reset(phone)
                        await websocket.send_text((await snapshot(phone)).model_dump_json())
        except WebSocketDisconnect:
            pass
        finally:
            unsubscribe()
            sockets.remove(phone, websocket)

    @router.websocket("/ws/audio")
    async def audio(websocket: WebSocket, phone: str) -> None:
        """The call itself: PCM16 mono 24 kHz both ways. Closing this socket hangs up."""
        phone = normalize(phone)
        user = await pipeline.user(phone)
        if phone in calls_running:
            await websocket.close(code=AUDIO_BUSY)
            return
        if user.call.phase is not CallPhase.CONNECTING:
            await websocket.close(code=AUDIO_NO_CALL)
            return
        calls_running.add(phone)
        try:
            await websocket.accept()
            await run_call(websocket, phone)
        finally:
            calls_running.discard(phone)

    return router


def call_event(command: CallCommand) -> CallEvent:
    match command.action:
        case CallAction.START:
            return CallEvent(transition=CallTransition.CONNECTING, initiated_by=Initiator.USER)
        case CallAction.ACCEPT:
            return CallEvent(transition=CallTransition.CONNECTING)
        case CallAction.DECLINE:
            return CallEvent(transition=CallTransition.DECLINED)
        case CallAction.HANGUP:
            return CallEvent(transition=CallTransition.ENDED, reason="user_hangup")
        case CallAction.FAILED:
            return CallEvent(transition=CallTransition.FAILED, reason=command.reason or "failed")


def normalize(phone: str) -> str:
    """Users are identified by the digits alone. A '+' in a query string arrives as a space,
    so keeping it would make '+1555…' over HTTP and over the socket two different users."""
    return "".join(c for c in phone if c.isdigit())
