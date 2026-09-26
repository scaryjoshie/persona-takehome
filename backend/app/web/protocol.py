"""The messages on the browser WebSocket, as types. `python -m app.web.schema` exports
these as JSON Schema for the frontend. See docs/proposed-design/14-frontend-contract.md."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from app.agent.slots import Slots
from app.calls.state import CallState
from app.routing.types import Medium

# ---- browser → server ---------------------------------------------------------


class CallAction(StrEnum):
    START = "start"  # the user taps "call"
    ACCEPT = "accept"  # the user answers the agent's call
    DECLINE = "decline"
    HANGUP = "hangup"
    FAILED = "failed"  # the browser could not bring audio up


class SendMessage(BaseModel):
    type: Literal["message"] = "message"
    text: str


class SetTyping(BaseModel):
    type: Literal["typing"] = "typing"
    active: bool


class CallCommand(BaseModel):
    type: Literal["call"] = "call"
    action: CallAction
    reason: str | None = None


class Reset(BaseModel):
    type: Literal["reset"] = "reset"


ClientMessage = Annotated[
    SendMessage | SetTyping | CallCommand | Reset, Field(discriminator="type")
]
CLIENT_MESSAGE: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


# ---- server → browser -----------------------------------------------------------


class Snapshot(BaseModel):
    type: Literal["snapshot"] = "snapshot"
    events: list[dict[str, Any]]  # Event, serialized; see the payload schema
    slots: Slots
    call: CallState
    floor: Medium


class EventMessage(BaseModel):
    type: Literal["event"] = "event"
    event: dict[str, Any]


class SlotsMessage(BaseModel):
    type: Literal["slots"] = "slots"
    slots: Slots


class CallMessage(BaseModel):
    type: Literal["call"] = "call"
    call: CallState


class TypingMessage(BaseModel):
    type: Literal["typing"] = "typing"
    active: bool


ServerMessage = Annotated[
    Snapshot | EventMessage | SlotsMessage | CallMessage | TypingMessage,
    Field(discriminator="type"),
]
