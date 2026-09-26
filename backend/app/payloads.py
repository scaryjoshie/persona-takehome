"""Every event kind, in one union, so rows read back from SQLite become the right class.

This is the one module that imports every section's events. Adding an event kind means
adding it here.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, TypeAdapter

from app.agent.events import Graduated, SlotChanged, ToolCall
from app.calls.events import CallEvent
from app.events.payload import Payload
from app.gmail.events import GmailEvent
from app.routing.types import Decision
from app.text.events import AgentMessage, Typing, UserMessage
from app.voice.events import VoiceUtterance

AnyPayload = Annotated[
    UserMessage
    | AgentMessage
    | Typing
    | VoiceUtterance
    | ToolCall
    | SlotChanged
    | Graduated
    | CallEvent
    | GmailEvent
    | Decision,
    Field(discriminator="kind"),
]
PAYLOADS: TypeAdapter[Payload] = TypeAdapter(AnyPayload)  # pyright: ignore[reportArgumentType]
