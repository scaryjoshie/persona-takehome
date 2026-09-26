from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from app.events.payload import Payload, Role, Turn


class Speaker(StrEnum):
    USER = "user"
    AGENT = "agent"


class VoiceUtterance(Payload):
    """One turn of speech. Recorded, never routed. On GPT-Live the boundary is inferred."""

    kind: Literal["voice_utterance"] = "voice_utterance"
    routes = False

    speaker: Speaker
    text: str | None
    turn_id: str
    inferred: bool = True

    def turn(self, at: datetime) -> Turn | None:
        if not self.text:
            return None
        who = "user" if self.speaker is Speaker.USER else "you"
        return Turn(Role.NOTE, f"on call, {who} said: {self.text}")
