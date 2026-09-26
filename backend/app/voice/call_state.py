"""What can happen to a call, where a call is, and how a call event moves it.

none → ringing → connecting → connected → ended
       └ declined ┘  └ failed ┘
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

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
            case CallTransition.ENDED if self.reason == "dropped":
                return Turn(Role.NOTE, f"the call dropped (lost connection, not a hang-up) {t}")
            case CallTransition.ENDED if self.reason == "silence":
                return Turn(Role.NOTE, f"you ended the call {t} after they went quiet")
            case CallTransition.ENDED:
                return Turn(Role.NOTE, f"call ended {t}, reason: {self.reason or 'unknown'}")
            case CallTransition.CONNECTING:
                return None


class CallPhase(StrEnum):
    NONE = "none"
    RINGING = "ringing"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ENDED = "ended"


class CallState(BaseModel):
    model_config = ConfigDict(frozen=True, json_schema_serialization_defaults_required=True)

    phase: CallPhase = CallPhase.NONE
    reason: str | None = None
    call_id: str | None = None
    initiated_by: Initiator | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


ALLOWED_FROM: dict[CallTransition, frozenset[CallPhase]] = {
    CallTransition.RINGING: frozenset({CallPhase.NONE, CallPhase.ENDED}),
    CallTransition.CONNECTING: frozenset({CallPhase.NONE, CallPhase.ENDED, CallPhase.RINGING}),
    CallTransition.CONNECTED: frozenset({CallPhase.CONNECTING}),
    CallTransition.DECLINED: frozenset({CallPhase.RINGING}),
    CallTransition.FAILED: frozenset({CallPhase.CONNECTING, CallPhase.RINGING}),
    CallTransition.ENDED: frozenset({CallPhase.CONNECTED, CallPhase.CONNECTING}),
}


def next_state(current: CallState, event: CallEvent, now: datetime) -> CallState | None:
    """The state after `event`, or None if the event is not valid from `current`."""
    if current.phase not in ALLOWED_FROM[event.transition]:
        return None
    if event.transition is CallTransition.FAILED and event.call_id not in (None, current.call_id):
        return None  # a missed-call timer for an attempt that was answered or replaced
    match event.transition:
        case CallTransition.RINGING:
            return CallState(
                phase=CallPhase.RINGING,
                initiated_by=event.initiated_by or Initiator.AGENT,
                call_id=event.call_id,
            )
        case CallTransition.CONNECTING:
            return CallState(
                phase=CallPhase.CONNECTING,
                initiated_by=event.initiated_by or current.initiated_by or Initiator.USER,
            )
        case CallTransition.CONNECTED:
            return CallState(
                phase=CallPhase.CONNECTED,
                initiated_by=current.initiated_by,
                call_id=event.call_id,
                started_at=now,
            )
        case CallTransition.DECLINED:
            return CallState(phase=CallPhase.NONE, reason=event.reason or "declined")
        case CallTransition.FAILED:
            return CallState(phase=CallPhase.NONE, reason=event.reason or "failed")
        case CallTransition.ENDED:
            return CallState(
                phase=CallPhase.ENDED,
                initiated_by=current.initiated_by,
                call_id=current.call_id,
                started_at=current.started_at,
                ended_at=now,
                reason=event.reason or "ended",
            )
