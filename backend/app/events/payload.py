"""What every event shares. Sections define their own payloads on top of this."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from app.routing.types import RoutingContext, Verb


class Origin(StrEnum):
    USER = "user"
    TEXT_AGENT = "text_agent"
    VOICE_AGENT = "voice_agent"
    CALL = "call"
    GOOGLE = "google"
    SYSTEM = "system"


class Channel(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    SYSTEM = "system"


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    NOTE = "note"


@dataclass(frozen=True)
class Turn:
    """How an event appears in a model's conversation."""

    role: Role
    text: str


class Payload(BaseModel):
    """Base for every event payload. Subclasses declare `kind: Literal["..."] = "..."`,
    which is the discriminator pydantic needs, and override the hooks they care about."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Subclasses declare `kind: Literal["..."] = "..."`; it is the union discriminator.
    routes: ClassVar[bool] = True  # does submitting this event trigger routing?
    persists: ClassVar[bool] = True  # does it go in the log?

    @property
    def kind_name(self) -> str:
        return str(type(self).model_fields["kind"].default)

    def should_route(self) -> bool:
        """Per-instance override of `routes` for kinds where it depends on the value."""
        return self.routes

    def turn(self, at: datetime) -> Turn | None:
        """Rendering for a model. None means the model never sees this event."""
        return None

    def describe(self) -> str:
        """One plain-English line saying what happened, for classifiers like the Jev decider."""
        turn = self.turn(datetime.now())
        return turn.text if turn else self.model_dump_json()

    def fixed_verb(self, ctx: RoutingContext) -> Verb | None:
        """A verb this event always gets while a run is active, or None to ask the decider."""
        return None
