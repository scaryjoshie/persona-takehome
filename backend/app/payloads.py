"""Every event kind, in one union, so rows read back from SQLite become the right class.

This is the one module that imports every section's events. Adding an event kind means
adding it here.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, TypeAdapter

from app.agent.events import (
    CallOptOut,
    ContactCard,
    ContactSaved,
    DeviceTimezone,
    Graduated,
    ObjectiveMoved,
    SlotChanged,
    StepSetAside,
    TimezoneLearned,
    ToolCall,
)
from app.events.decision import Decision
from app.events.payload import Payload
from app.google.events import EmailDraft, GmailEvent
from app.jobs.events import JobAsked, JobEnded, JobStarted, JobTold
from app.memory.events import Forgot, Remembered
from app.text.events import (
    AgentMessage,
    Reaction,
    ReplyDue,
    ReplyStarted,
    Typing,
    UserMessage,
    VoiceNote,
)
from app.voice.call_state import CallEvent
from app.voice.events import VoiceUtterance

AnyPayload = Annotated[
    UserMessage
    | AgentMessage
    | Typing
    | ReplyDue
    | ReplyStarted
    | VoiceNote
    | Reaction
    | ContactCard
    | ContactSaved
    | DeviceTimezone
    | TimezoneLearned
    | CallOptOut
    | VoiceUtterance
    | ToolCall
    | SlotChanged
    | ObjectiveMoved
    | StepSetAside
    | Graduated
    | CallEvent
    | GmailEvent
    | EmailDraft
    | Remembered
    | Forgot
    | JobStarted
    | JobAsked
    | JobTold
    | JobEnded
    | Decision,
    Field(discriminator="kind"),
]
PAYLOADS: TypeAdapter[Payload] = TypeAdapter(AnyPayload)  # pyright: ignore[reportArgumentType]
