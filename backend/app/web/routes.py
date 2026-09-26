"""The browser's endpoints: start a session, and the main socket. Every change goes
through the pipeline; this file only turns requests into events."""

from __future__ import annotations

import contextlib
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from app.agent.events import ContactSaved
from app.events.event import Event
from app.events.payload import Channel, Origin
from app.pipeline import Pipeline
from app.services import ServicesDep
from app.text.events import Reaction, Typing, UserMessage
from app.voice.call_events import CallEvent, CallTransition, Initiator
from app.web.protocol import (
    CLIENT_MESSAGE,
    CallAction,
    CallCommand,
    CallMessage,
    EventMessage,
    ReactCommand,
    Reset,
    SaveContact,
    SendMessage,
    SetTyping,
    SlotsMessage,
    Snapshot,
    WireEvent,
)

log = logging.getLogger(__name__)
router = APIRouter()

STATE_KINDS = {
    "slot_changed",
    "call",
    "gmail",
    "graduated",
    "contact_saved",
}  # the browser needs fresh state


class SessionRequest(BaseModel):
    phone: str


@router.post("/api/session")
async def session(body: SessionRequest, svc: ServicesDep) -> Snapshot:
    return await snapshot(svc.pipeline, normalize(body.phone))


@router.websocket("/ws")
async def ws(websocket: WebSocket, phone: str, svc: ServicesDep) -> None:
    phone, pipeline = normalize(phone), svc.pipeline
    await websocket.accept()
    svc.sockets.add(phone, websocket)

    async def send(message: BaseModel) -> None:
        with contextlib.suppress(Exception):  # this socket closed; its finally cleans up
            await websocket.send_text(message.model_dump_json())

    async def on_event(event: Event) -> None:
        # Each socket has its own subscription and sends only to itself.
        await send(EventMessage(event=WireEvent.of(event)))
        if event.kind in STATE_KINDS:
            user = await pipeline.user(phone)
            await send(SlotsMessage(slots=user.slots))
            await send(CallMessage(call=user.call))

    unsubscribe = pipeline.subscribe(phone, on_event)
    typing_since: float | None = None
    try:
        await send(await snapshot(pipeline, phone))
        while True:
            try:
                message = CLIENT_MESSAGE.validate_json(await websocket.receive_text())
            except ValidationError as exc:
                log.info("%s: bad message %s", phone, exc.errors()[:1])
                continue
            match message:
                case SendMessage(text=text, reply_to=reply_to):
                    typing_since = None
                    quoted = await text_of(pipeline, phone, reply_to)
                    said = UserMessage(text=text, reply_to=reply_to, reply_to_text=quoted)
                    await pipeline.submit(phone, Origin.USER, Channel.TEXT, said)
                case ReactCommand(target_seq=seq, emoji=emoji, remove=remove):
                    tapback = Reaction(
                        target_seq=seq,
                        target_text=await text_of(pipeline, phone, seq),
                        emoji=emoji,
                        by="user",
                        removed=remove,
                    )
                    await pipeline.submit(phone, Origin.USER, Channel.TEXT, tapback)
                case SaveContact():
                    name = (await pipeline.user(phone)).slots.agent_name
                    if name:
                        await pipeline.submit(
                            phone, Origin.USER, Channel.TEXT, ContactSaved(name=name)
                        )
                case SetTyping(active=active):
                    now = time.monotonic()
                    typing_since = (typing_since or now) if active else None
                    typing = Typing(
                        active=active, seconds=now - typing_since if typing_since else 0
                    )
                    await pipeline.submit(phone, Origin.USER, Channel.TEXT, typing)
                case CallCommand():
                    await pipeline.submit(phone, Origin.USER, Channel.SYSTEM, call_event(message))
                case Reset():
                    await pipeline.reset(phone)
                    await send(await snapshot(pipeline, phone))
    except WebSocketDisconnect:
        pass
    finally:
        unsubscribe()
        svc.sockets.remove(phone, websocket)


async def text_of(pipeline: Pipeline, phone: str, seq: int | None) -> str | None:
    """The text of bubble `seq`, if it has any, so a reply or tapback can quote it."""
    if seq is None:
        return None
    for event in await pipeline.history(phone):
        if event.seq == seq:
            return getattr(event.payload, "text", None) or getattr(
                event.payload, "transcript", None
            )
    return None


async def snapshot(pipeline: Pipeline, phone: str) -> Snapshot:
    user = await pipeline.user(phone)
    return Snapshot(
        events=[WireEvent.of(e) for e in await pipeline.history(phone)],
        slots=user.slots,
        call=user.call,
        floor=user.floor,
    )


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
