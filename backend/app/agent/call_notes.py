"""How routed events are worded for the speaking model during a call."""

from __future__ import annotations

from app.calls.events import CallEvent
from app.events.event import Event
from app.gmail.events import GmailEvent, GmailPhase
from app.text.events import Typing, UserMessage
from app.voice.responder import Note


def call_note(event: Event) -> Note | None:
    match event.payload:
        case UserMessage(text=text):
            return Note(
                f"Internal note: the user just texted: {text!r}. Work it in naturally.", False
            )
        case Typing(active=False):
            return None
        case Typing(seconds=seconds):
            return Note(
                f"Internal note: the user has been typing a reply for {seconds:.0f}s. "
                "If you asked them something, invite them to finish typing and wait.",
                False,
            )
        case GmailEvent(phase=GmailPhase.CONNECTED, email=email):
            return Note(f"Tell the user their Gmail ({email}) is now connected.", True)
        case GmailEvent(phase=GmailPhase.LINK_SENT):
            return Note("Internal note: the Gmail link was just texted to the user.", False)
        case GmailEvent(phase=GmailPhase.SKIPPED):
            return Note("Internal note: the user skipped Gmail. Do not ask again.", False)
        case GmailEvent():
            return Note(
                "Tell the user the Gmail connection didn't go through; offer to retry.", True
            )
        case CallEvent():
            return None  # the call manager handles call state; nothing to say
        case _:
            return None
