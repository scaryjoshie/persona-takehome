"""The voice medium: what to do with an event that arrives during a call.

When the agent is not speaking, the note goes in straight away. While it is speaking, pick
a verb: interrupt (the voice works it in now), absorb (a silent note), or defer (hold it
until the current sentence ends). A text from the user always interrupts; other events
ask Jev, with fixed defaults if Jev is unavailable.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol

from app.agent.context import last_lines
from app.events.decision import Decision
from app.events.event import Event
from app.gmail.events import GmailEvent, GmailPhase
from app.jev import Jev
from app.pipeline import Context
from app.text.events import Typing, UserMessage
from app.users.user import User


@dataclass(frozen=True)
class Note:
    text: str
    speak: bool


def call_note(event: Event) -> Note | None:
    """How a routed event is worded for the voice. None: the voice needn't hear of it."""
    match event.payload:
        case UserMessage(text=text):
            return Note(f"They just texted you: {text!r}. Work it in naturally.", False)
        case Typing(active=False):
            return None
        case Typing(seconds=seconds):
            return Note(
                f"They've been typing a reply for {seconds:.0f}s. "
                "If you asked them something, invite them to finish typing and wait.",
                False,
            )
        case GmailEvent(phase=GmailPhase.CONNECTED, email=email):
            return Note(f"Tell the user their Gmail ({email}) is now connected.", True)
        case GmailEvent(phase=GmailPhase.LINK_SENT):
            return Note("The Gmail link is in their texts now.", False)
        case GmailEvent(phase=GmailPhase.SKIPPED):
            return Note("They don't want to connect Gmail. Don't ask again.", False)
        case GmailEvent():
            return Note(
                "Tell the user the Gmail connection didn't go through; offer to retry.", True
            )
        case _:
            return None


class Session(Protocol):
    async def send(self, content: str, /, *, respond: bool | None = None) -> None: ...


@dataclass
class LiveCall:
    """The in-process state of one call. Updated by the call's event loop (call.py)."""

    session: Session
    speaking: bool = False
    tool_running: bool = False
    asked_question: bool = False
    deferred: list[str] = field(default_factory=lambda: [])
    held: list[str] = field(default_factory=lambda: [])  # background, for their next turn
    voice_owes_reply: bool = False  # they spoke last; the voice's next words answer them
    agent_lines: int = 0  # the voice's finished turns so far
    last_agent_line: str = ""
    hang_up_after: int | None = None  # set by end_call: agent_lines when it was asked
    hang_up_asked: asyncio.Event = field(default_factory=asyncio.Event)
    closed: bool = False  # the call ended; late sends (a back-office run finishing) are dropped

    async def send(self, text: str, *, speak: bool) -> None:
        if not self.closed:
            await self.session.send(text, respond=speak)

    def said(self, line: str) -> None:
        self.agent_lines += 1
        self.last_agent_line = line

    async def whisper(self, text: str) -> None:
        """Background for the voice (where things stand, what the back office did). If it's
        the voice's turn, it goes in now so the reply uses it. Otherwise it waits until they
        next start talking: sent to a voice that has finished its turn, even silent context
        tends to make it speak up unprompted."""
        if self.voice_owes_reply:
            await self.send(text, speak=False)
        else:
            self.held.append(text)

    async def user_started(self) -> None:
        """They started talking, so the voice stopped. Held background goes in now, and
        anything deferred to the end of its sentence goes in silently rather than being lost
        with the cut-off turn."""
        self.speaking, self.voice_owes_reply = False, True
        waiting, self.held, self.deferred = [*self.held, *self.deferred], [], []
        if waiting:  # one append: several at once each drew their own reply
            await self.send("\n\n".join(waiting), speak=False)

    async def turn_complete(self, *, asked_question: bool) -> None:
        self.speaking = False
        self.asked_question = asked_question
        deferred, self.deferred = self.deferred, []
        for text in deferred:
            await self.send(text, speak=True)


QUESTION = (
    "A new event arrived while the assistant is responding on a phone call. "
    "How should the assistant handle it?"
)
# Measured 2026-09-26: this wording separates "user typing after the agent asked a question"
# (interrupt) from "user typing while the agent explains" (defer); looser variants did not.
CRITERIA = {
    "interrupt": (
        "The event is a reply to something the assistant is waiting on, or needs a response "
        "right now. Address it immediately."
    ),
    "absorb": "The event is background information the assistant should know but not remark on.",
    "defer": (
        "The event deserves a response, but the assistant is mid-response on something else "
        "and should finish first."
    ),
}
DEFAULTS = {"typing": "absorb", "gmail": "defer"}


class VoiceResponder:
    def __init__(self, *, jev: Jev | None = None) -> None:
        self.calls: dict[str, LiveCall] = {}  # phone → the call in progress
        self._jev = jev

    async def handle(self, event: Event, user: User, ctx: Context) -> Decision | None:
        call = self.calls.get(ctx.phone)
        note = call_note(event)
        if note is None:
            return None
        if call is None:
            return Decision(trigger_kind=event.kind, verb="drop", note="no call in progress")
        if not call.speaking:
            await call.send(note.text, speak=note.speak)
            return Decision(trigger_kind=event.kind, verb="send", note="agent not speaking")
        verb, by, confidence = await self._verb(event, call, ctx)
        if call.tool_running and verb == "interrupt":
            verb, by = "defer", f"{by}; a tool is running"
        match verb:
            case "interrupt":
                await call.send(note.text, speak=True)
            case "absorb":
                await call.send(note.text, speak=False)
            case _:
                call.deferred.append(note.text)
        return Decision(trigger_kind=event.kind, verb=verb, by=by, confidence=confidence)

    def hang_up(self, phone: str) -> bool:
        """end_call during a call: the call hangs up once the voice's goodbye has played."""
        call = self.calls.get(phone)
        if call is None:
            return False
        if call.hang_up_after is None:
            call.hang_up_after = call.agent_lines
            call.hang_up_asked.set()
        return True

    async def _verb(
        self, event: Event, call: LiveCall, ctx: Context
    ) -> tuple[str, str, float | None]:
        if isinstance(event.payload, UserMessage):
            return "interrupt", "rule", None  # the user texted during the call
        if self._jev is not None:
            answer = await self._jev.choice(QUESTION, CRITERIA, _state(event, call, ctx))
            if answer is not None:
                return answer.choice, "jev", answer.probabilities.get(answer.choice)
        return DEFAULTS.get(event.kind, "absorb"), "fallback", None


def _state(event: Event, call: LiveCall, ctx: Context) -> dict[str, object]:
    return {
        "channel": "voice call",
        "assistant_currently_responding": call.speaking,
        "assistant_last_turn_asked_a_question": call.asked_question,
        "conversation": last_lines(ctx.recent),
        "new_event": event.payload.describe(),
    }
