from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.events.payload import Payload, Role, Turn


def _quoted(text: str | None) -> str:
    """How a quoted bubble reads in the model's context."""
    if not text:
        return ""
    short = text if len(text) <= 80 else text[:77] + "..."
    return f'(replying to "{short}") '


class UserMessage(Payload):
    kind: Literal["user_message"] = "user_message"
    text: str
    reply_to: int | None = None  # seq of the bubble this replies to (an iMessage inline reply)
    reply_to_text: str | None = None  # that bubble's text, kept so the model sees the quote

    def turn(self, at: datetime) -> Turn | None:
        return Turn(Role.USER, _quoted(self.reply_to_text) + self.text)


class AgentMessage(Payload):
    """A bubble that was sent. Recorded, never routed."""

    kind: Literal["agent_message"] = "agent_message"
    routes = False

    text: str
    from_call: bool = False  # sent by the voice side via a tool
    reply_to: int | None = None

    def turn(self, at: datetime) -> Turn | None:
        if self.from_call:
            return Turn(Role.NOTE, f"you texted (from the call): {self.text}")
        return Turn(Role.ASSISTANT, self.text)


class Typing(Payload):
    """Coalesced client-side. Routed, never stored."""

    kind: Literal["typing"] = "typing"
    persists = False

    active: bool
    seconds: float = 0.0

    def describe(self) -> str:
        return f"the user has been typing a text message for {self.seconds:.0f} seconds"


class ReplyDue(Payload):
    """Time to check whether to reply. Submitted by the text medium after a delay; never stored."""

    kind: Literal["reply_due"] = "reply_due"
    persists = False


class ReplyStarted(Payload):
    """The agent started replying to everything up to and including event `through_seq`.
    Stored, so "is anything waiting for a reply?" is a question about the log."""

    kind: Literal["reply_started"] = "reply_started"
    routes = False

    through_seq: int


class VoiceNote(Payload):
    """A voice message the user sent. Handled like a text: `transcript` is what they said."""

    kind: Literal["voice_note"] = "voice_note"

    audio_id: str
    duration_ms: int | None = None
    transcript: str | None = None

    def turn(self, at: datetime) -> Turn | None:
        return Turn(Role.USER, f"(voice message) {self.transcript or '[could not transcribe]'}")


class Reaction(Payload):
    """A tapback (❤️ 👍 😂 …) on a bubble, by either side. The user's route: a 👍 on
    "want me to call?" is an answer. The agent's are recorded only."""

    kind: Literal["reaction"] = "reaction"

    target_seq: int
    target_text: str | None = None
    emoji: str
    by: Literal["user", "agent"]
    removed: bool = False

    def should_route(self) -> bool:
        return self.by == "user" and not self.removed

    def turn(self, at: datetime) -> Turn | None:
        who = "user" if self.by == "user" else "you"
        verb = "took back their" if self.removed else "reacted"
        target = f' to "{self.target_text}"' if self.target_text else ""
        return Turn(Role.NOTE, f"{who} {verb} {self.emoji}{target}")
