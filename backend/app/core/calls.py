"""The call state machine. See docs/proposed-design/09-protocol.md.

none → ringing → connecting → connected → ended
       └ declined ┘  └ failed ┘
The floor flips here, in the same step the event is appended (docs 03, the hang-up race).
"""

from __future__ import annotations

from datetime import datetime

from app.core.store import Store
from app.core.types import CallEvent, CallState

_ALLOWED: dict[str, frozenset[str]] = {
    "ringing": frozenset({"none", "ended"}),
    "connecting": frozenset({"none", "ended", "ringing"}),
    "connected": frozenset({"connecting"}),
    "declined": frozenset({"ringing"}),
    "failed": frozenset({"connecting", "ringing"}),
    "ended": frozenset({"connected", "connecting"}),
}


def transition(current: CallState, event: CallEvent, now: datetime) -> CallState | None:
    """Next state, or None if the event is not valid from the current phase."""
    if current.phase not in _ALLOWED[event.phase]:
        return None
    match event.phase:
        case "ringing":
            return CallState(phase="ringing", initiated_by=event.initiated_by or "agent")
        case "connecting":
            return CallState(
                phase="connecting",
                initiated_by=event.initiated_by or current.initiated_by or "user",
            )
        case "connected":
            return CallState(
                phase="connected",
                initiated_by=current.initiated_by,
                call_id=event.call_id,
                started_at=now,
            )
        case "declined":
            return CallState(phase="none", reason=event.reason or "declined")
        case "failed":
            return CallState(phase="none", reason=event.reason or "failed")
        case "ended":
            return CallState(
                phase="ended",
                initiated_by=current.initiated_by,
                call_id=current.call_id,
                started_at=current.started_at,
                ended_at=now,
                reason=event.reason or "ended",
            )


async def apply_call_event(store: Store, event: CallEvent) -> bool:
    """Update call state and floor. Returns False if the transition was invalid."""
    nxt = transition(store.call, event, store.now())
    if nxt is None:
        return False
    await store.set_call(nxt)
    await store.set_floor("voice" if nxt.phase == "connected" else "text")
    return True
