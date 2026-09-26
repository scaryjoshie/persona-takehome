"""When to reply to a run of texts. Pure functions of the log and the typing state.

People send several messages in a row and start typing and stop. Wait for a quiet window
after the last message, longer while the user is typing, but never past a hard cap from
the first unanswered message or once six messages are waiting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.events.event import Event
from app.text.events import Reaction, ReplyStarted, UserMessage, VoiceNote

MESSAGES = (UserMessage, VoiceNote, Reaction)  # things a person sends; answered after a pause


@dataclass(frozen=True)
class Timing:
    quiet: float = 1.5  # seconds after the last message
    typing_extend_max: float = 5.0  # while typing, wait at most this long past the last message
    hard_cap: float = 8.0  # from the first unanswered event
    max_messages: int = 6
    unfinished_extend: float = 4.0  # Jev may hold off this long past the last message


def waiting(recent: list[Event]) -> list[Event]:
    """Events since the last reply started that want a reply (messages, call outcomes, …)."""
    out: list[Event] = []
    for event in reversed(recent):
        if isinstance(event.payload, ReplyStarted):
            break
        if event.payload.should_route():
            out.append(event)
    out.reverse()
    return out


def delay(pending: list[Event], typing_since: datetime | None, now: datetime, t: Timing) -> float:
    """Seconds from now until a reply is due. 0 means reply now."""
    if not pending:
        return 0.0
    if any(not isinstance(e.payload, MESSAGES) for e in pending):
        return 0.0  # a call ended or Gmail connected: nobody is typing that, answer now
    messages = [e for e in pending if isinstance(e.payload, MESSAGES)]
    if len(messages) >= t.max_messages:
        return 0.0
    cap_left = (pending[0].ts - now).total_seconds() + t.hard_cap
    since_last = (now - messages[-1].ts).total_seconds()
    wait = t.typing_extend_max if typing_since is not None else t.quiet
    return max(0.0, min(wait - since_last, cap_left))
