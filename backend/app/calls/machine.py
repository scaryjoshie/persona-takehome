"""none → ringing → connecting → connected → ended, with declined and failed exits.
See docs/proposed-design/09-protocol.md."""

from __future__ import annotations

from datetime import datetime

from app.calls.types import CallEvent, CallPhase, CallState, CallTransition, Initiator

_FROM: dict[CallTransition, frozenset[CallPhase]] = {
    CallTransition.RINGING: frozenset({CallPhase.NONE, CallPhase.ENDED}),
    CallTransition.CONNECTING: frozenset({CallPhase.NONE, CallPhase.ENDED, CallPhase.RINGING}),
    CallTransition.CONNECTED: frozenset({CallPhase.CONNECTING}),
    CallTransition.DECLINED: frozenset({CallPhase.RINGING}),
    CallTransition.FAILED: frozenset({CallPhase.CONNECTING, CallPhase.RINGING}),
    CallTransition.ENDED: frozenset({CallPhase.CONNECTED, CallPhase.CONNECTING}),
}


def transition(current: CallState, event: CallEvent, now: datetime) -> CallState | None:
    """The next state, or None if the transition is not valid from `current`."""
    if current.phase not in _FROM[event.transition]:
        return None
    match event.transition:
        case CallTransition.RINGING:
            return CallState(
                phase=CallPhase.RINGING, initiated_by=event.initiated_by or Initiator.AGENT
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
