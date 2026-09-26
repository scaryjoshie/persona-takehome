"""What can happen to a call."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Literal

from app.events.payload import Payload, Role, Turn


class CallTransition(StrEnum):
    RINGING = "ringing"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DECLINED = "declined"
    FAILED = "failed"
    ENDED = "ended"


class Initiator(StrEnum):
    AGENT = "agent"
    USER = "user"


class CallEvent(Payload):
    kind: Literal["call"] = "call"

    transition: CallTransition
    reason: str | None = None
    call_id: str | None = None
    initiated_by: Initiator | None = None

    OUTCOMES: ClassVar[frozenset[CallTransition]] = frozenset(
        {CallTransition.DECLINED, CallTransition.FAILED, CallTransition.ENDED}
    )

    def should_route(self) -> bool:
        """Only outcomes need a reply. State transitions are recorded, not answered."""
        return self.transition in self.OUTCOMES

    def turn(self, at: datetime) -> Turn | None:
        t = at.strftime("%H:%M")
        match self.transition:
            case CallTransition.RINGING:
                return Turn(Role.NOTE, f"you started calling the user {t}")
            case CallTransition.CONNECTED:
                return Turn(Role.NOTE, f"call connected {t}")
            case CallTransition.DECLINED:
                return Turn(Role.NOTE, f"user declined the call {t}")
            case CallTransition.FAILED if self.reason == "no_answer":
                return Turn(Role.NOTE, f"they didn't pick up your call {t}")
            case CallTransition.FAILED:
                return Turn(Role.NOTE, f"call failed {t}, reason: {self.reason or 'unknown'}")
            case CallTransition.ENDED:
                return Turn(Role.NOTE, f"call ended {t}, reason: {self.reason or 'unknown'}")
            case CallTransition.CONNECTING:
                return None
