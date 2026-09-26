"""The voice medium: what to do with an event that arrives during a call.

When the agent is not speaking, the note goes in straight away. While it is speaking, pick
a verb: interrupt (the voice works it in now), absorb (a silent note), or defer (hold it
until the current sentence ends). A text from the user always interrupts; other events
ask Jev, with fixed defaults if Jev is unavailable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.events.decision import Decision
from app.events.event import Event
from app.jev import Jev
from app.pipeline import Context
from app.text.events import UserMessage
from app.users.user import User


@dataclass(frozen=True)
class Note:
    text: str
    speak: bool


NoteFor = Callable[[Event], Note | None]


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
    closed: bool = False  # the call ended; late sends (a back-office run finishing) are dropped

    async def send(self, text: str, *, speak: bool) -> None:
        if not self.closed:
            await self.session.send(text, respond=speak)

    def hold(self, text: str) -> None:
        """Background for the voice (where things stand, what the back office did). Handed in
        when they next start talking: sent while the voice is idle, even silent context
        tends to make it speak up unprompted, and it only matters for its next reply."""
        self.held.append(text)

    async def user_started(self) -> None:
        """They started talking, so the voice stopped. Held background goes in now, and
        anything deferred to the end of its sentence goes in silently rather than being lost
        with the cut-off turn."""
        self.speaking = False
        waiting, self.held, self.deferred = [*self.held, *self.deferred], [], []
        for text in waiting:
            await self.send(text, speak=False)

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
    def __init__(self, *, notes: NoteFor, jev: Jev | None = None) -> None:
        self.calls: dict[str, LiveCall] = {}  # phone → the call in progress
        self._notes = notes
        self._jev = jev

    async def handle(self, event: Event, user: User, ctx: Context) -> Decision | None:
        call = self.calls.get(ctx.phone)
        note = self._notes(event)
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
    conversation: list[str] = []
    for e in ctx.recent[-12:]:
        turn = e.payload.turn(e.ts)
        if turn is not None:
            conversation.append(f"{turn.role.value}: {turn.text}")
    return {
        "channel": "voice call",
        "assistant_currently_responding": call.speaking,
        "assistant_last_turn_asked_a_question": call.asked_question,
        "conversation": conversation,
        "new_event": event.payload.describe(),
    }
