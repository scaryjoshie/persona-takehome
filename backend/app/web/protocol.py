"""The messages on the browser WebSocket, as types. `python -m app.web.schema` exports
these as JSON Schema for the frontend. See docs/proposed-design/14-frontend-contract.md."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.agent.slots import Slots
from app.events.event import Event
from app.events.payload import Channel, Origin
from app.payloads import AnyPayload
from app.users.user import Medium
from app.voice.call_state import CallState
from app.voice.events import Speaker


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
    reply_to: int | None = None  # seq of the bubble being replied to


class SendDraft(Message):
    """The Send button on an email draft card."""

    type: Literal["draft"] = "draft"
    action: Literal["send"] = "send"
    ref: str


class ReactCommand(Message):
    """A tapback on a bubble; `remove` takes it back."""

    type: Literal["react"] = "react"
    target_seq: int
    emoji: str
    remove: bool = False


class SaveContact(Message):
    """The user tapped the agent's contact card to save it."""

    type: Literal["contact"] = "contact"
    action: Literal["save"] = "save"


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
    SendMessage | SetTyping | CallCommand | ReactCommand | SaveContact | SendDraft | Reset,
    Field(discriminator="type"),
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


class TranscriptPartial(Message):
    """A live caption: `text` is the turn's full transcript so far (replace, don't append).
    `final` closes the turn; the VoiceUtterance event with the same turn_id follows."""

    type: Literal["partial"] = "partial"
    speaker: Speaker
    turn_id: str
    text: str
    final: bool


ServerMessage = Annotated[
    Snapshot | EventMessage | SlotsMessage | CallMessage | TypingMessage | TranscriptPartial,
    Field(discriminator="type"),
]
