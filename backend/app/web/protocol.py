"""The messages on the browser WebSocket, as types. `python -m app.web.schema` exports
these as JSON Schema for the frontend. See docs/proposed-design/14-frontend-contract.md."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.agent.slots import Slots
from app.calls.state import CallState
from app.events.event import Event
from app.events.payload import Channel, Origin
from app.payloads import AnyPayload
from app.routing.types import Medium


class Message(BaseModel):
    """Base for every wire message: defaulted fields (the `type` tags) count as required in
    the exported schema, so TypeScript can narrow the unions."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class WireEvent(Message):
    """The event envelope as the browser sees it, with the payload typed as the full union."""

    seq: int
    ts: datetime
    origin: Origin
    channel: Channel
    payload: AnyPayload

    @classmethod
    def of(cls, event: Event) -> WireEvent:
        return cls.model_validate(event.model_dump())


# ---- browser → server ---------------------------------------------------------


class CallAction(StrEnum):
    START = "start"  # the user taps "call"
    ACCEPT = "accept"  # the user answers the agent's call
    DECLINE = "decline"
    HANGUP = "hangup"
    FAILED = "failed"  # the browser could not bring audio up


class SendMessage(Message):
    type: Literal["message"] = "message"
    text: str


class SetTyping(Message):
    type: Literal["typing"] = "typing"
    active: bool


class CallCommand(Message):
    type: Literal["call"] = "call"
    action: CallAction
    reason: str | None = None


class Reset(Message):
    type: Literal["reset"] = "reset"


ClientMessage = Annotated[
    SendMessage | SetTyping | CallCommand | Reset, Field(discriminator="type")
]
CLIENT_MESSAGE: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


# ---- server → browser -----------------------------------------------------------


class Snapshot(Message):
    type: Literal["snapshot"] = "snapshot"
    events: list[WireEvent]
    slots: Slots
    call: CallState
    floor: Medium


class EventMessage(Message):
    type: Literal["event"] = "event"
    event: WireEvent


class SlotsMessage(Message):
    type: Literal["slots"] = "slots"
    slots: Slots


class CallMessage(Message):
    type: Literal["call"] = "call"
    call: CallState


class TypingMessage(Message):
    type: Literal["typing"] = "typing"
    active: bool


ServerMessage = Annotated[
    Snapshot | EventMessage | SlotsMessage | CallMessage | TypingMessage,
    Field(discriminator="type"),
]
