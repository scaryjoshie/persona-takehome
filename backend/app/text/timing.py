"""When to reply to a run of texts. Pure functions of the log and the typing state.

People send several messages in a row and start typing and stop. Wait for a quiet window
after the last message, longer while the user is typing, but never past a hard cap from
the first unanswered message or once six messages are waiting.
"""

from __future__ import annotations

from datetime import datetime

from app.events.event import Event
from app.text.events import Reaction, ReplyStarted, UserMessage, VoiceNote

MESSAGES = (UserMessage, VoiceNote, Reaction)  # things a person sends; answered after a pause


QUIET = 1.5  # seconds after the last message
TYPING_WAIT = 5.0  # while typing, wait at most this long past the last message
HARD_CAP = 8.0  # from the first unanswered event
MAX_MESSAGES = 6


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


def delay(pending: list[Event], typing_since: datetime | None, now: datetime) -> float:
    """Seconds from now until a reply is due. 0 means reply now."""
    if not pending:
        return 0.0
    if any(not isinstance(e.payload, MESSAGES) for e in pending):
        return 0.0  # a call ended or Gmail connected: nobody is typing that, answer now
    messages = [e for e in pending if isinstance(e.payload, MESSAGES)]
    if len(messages) >= MAX_MESSAGES:
        return 0.0
    cap_left = (pending[0].ts - now).total_seconds() + HARD_CAP
    since_last = (now - messages[-1].ts).total_seconds()
    wait = TYPING_WAIT if typing_since is not None else QUIET
    return max(0.0, min(wait - since_last, cap_left))
