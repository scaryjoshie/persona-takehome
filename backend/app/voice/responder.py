"""The voice medium: what to do with an event that arrives during a call.

When the agent is not speaking, the note goes in straight away. While it is speaking, pick
a verb: interrupt (the voice works it in now), absorb (a silent note), or defer (hold it
until the current sentence ends). A text from the user always interrupts; other events
ask Jev, with fixed defaults if Jev is unavailable.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from pydantic_ai.exceptions import UserError

from app.agent.context import last_lines
from app.events.decision import Decision
from app.events.event import Event
from app.events.payload import Origin
from app.google.events import GmailEvent, GmailPhase, inbox_lines
from app.jev import Jev
from app.jobs.events import JobAsked, JobEnded
from app.pipeline import Context
from app.text.events import Typing, UserMessage
from app.users.user import User


@dataclass(frozen=True)
class Note:
    text: str
    speak: bool


def call_note(event: Event, *, onboarding: bool = False) -> Note | None:
    """How a routed event is worded for the voice. None: the voice needn't hear of it.
    `onboarding`: the objective chain says what comes next, so a connection goes in silently
    (said twice, the voice lingered on the inbox instead of moving on)."""
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
        case GmailEvent(phase=GmailPhase.CONNECTED) as connected:
            return Note(
                "Their Gmail just connected."
                + ("" if onboarding else " Say so in a few words.")
                + " Go through it once they want you to; their latest messages, for then:\n"
                f"{inbox_lines(connected)}",
                not onboarding,
            )
        case GmailEvent(phase=GmailPhase.LINK_SENT):
            return Note("The Gmail link is in their texts now.", False)
        case GmailEvent(phase=GmailPhase.SKIPPED):
            return Note("They don't want to connect Gmail. Don't ask again.", False)
        case GmailEvent():
            return Note(
                "Tell the user the Gmail connection didn't go through; offer to retry.", True
            )
        case JobAsked(question=question):
            return Note(
                f"A background task you started needs their answer: {question} Ask them when "
                "there's a natural moment.",
                True,
            )
        case JobEnded(outcome="done", text=text):
            return Note(f"A background task you started finished: {text} Tell them briefly.", True)
        case JobEnded(outcome="failed", text=text):
            return Note(f"A background task you started didn't work out: {text}", True)
        case _:
            return None


ANSWER_WAIT = 6.0  # seconds a spoken note holds the next one while the voice starts answering
UNSAID = "You haven't told them this yet; work it into your reply:"


class Session(Protocol):
    async def send(self, content: str, /, *, respond: bool | None = None) -> None: ...


@dataclass
class LiveCall:
    """The in-process state of one call. Updated by the call's event loop (call.py)."""

    session: Session
    speaking: bool = False
    tool_running: bool = False
    asked_question: bool = False
    deferred: list[str] = field(default_factory=lambda: [])  # to say when its turn ends
    passing: list[str] = field(default_factory=lambda: [])  # the same, dropped once they speak
    held: list[str] = field(default_factory=lambda: [])  # background, for their next turn
    voice_owes_reply: bool = False  # they spoke last; the voice's next words answer them
    agent_lines: int = 0  # the voice's finished turns so far
    last_agent_line: str = ""
    hang_up_asked: asyncio.Event = field(default_factory=asyncio.Event)  # by end_call
    hang_up_after: int = 0  # agent_lines when end_call asked
    hang_up_reason: str = "agent_hangup"
    last_sound: float = field(default_factory=time.monotonic)  # anyone speaking, for silence
    heard_at: float = 0.0  # when they last spoke
    wake: Callable[[], None] = lambda: None  # run the call agent (a text arrived mid-call)
    check_ins: int = 0  # times the voice checked in on a silent line since they last spoke
    closed: bool = False  # the call ended; late sends (a call agent run finishing) are dropped
    steer_waiting: str | None = None  # the latest instructions, held while the voice speaks
    working: dict[str, str] = field(default_factory=lambda: {})  # job → goal, while it runs
    said_still_looking: bool = False  # since they last spoke
    spoke_at: float = 0.0  # when a spoken note last went in; the voice's answer is coming
    voice_at: float = 0.0  # when the voice's words were last heard
    delegations: int = 0  # times Live handed work to its own backend this call

    async def send(self, text: str, *, speak: bool) -> None:
        """GPT-Live takes at most 500 tokens per send (more ends the session), so long notes
        go in pieces; only the last one asks the voice to respond."""
        pieces = _pieces(text)
        for i, piece in enumerate(pieces):
            if self.closed:
                return
            try:
                await self.session.send(piece, respond=speak and i == len(pieces) - 1)
            except UserError:  # the session closed under us (a late call agent run)
                self.closed = True
                return

    async def steer(self, text: str) -> None:
        """Standing guidance as Live instructions (session.instructions.append): followed rather
        than paraphrased, and it never prompts speech, so it goes in straight away. They
        accumulate, so each one says what it replaces. pydantic-ai doesn't wrap this event, so
        it goes through the connection's raw sender; without one it falls back to a silent note."""
        send_event = getattr(getattr(self.session, "_connection", None), "_send_event", None)
        if send_event is None:
            return await self.send(text, speak=False)
        for piece in _pieces(text):
            if self.closed:
                return
            try:
                await send_event(
                    {"type": "session.instructions.append", "delegation_id": None, "content": piece}
                )
            except Exception:  # the session closed under us
                self.closed = True
                return

    async def steer_when_quiet(self, text: str) -> None:
        """Instructions go in only between turns: after the voice finishes, before they speak.
        Mid-sentence, Live rewrites the sentence as it speaks ("i just texted you my contact
        card, awesome, my contact card's..."); while they talk, it's already forming its reply.
        Meanwhile keep only the latest; it goes in when the voice's next turn ends."""
        if self.speaking or self.voice_owes_reply:
            self.steer_waiting = text
        else:
            await self.steer(text)

    async def _steer_waiting(self) -> None:
        text, self.steer_waiting = self.steer_waiting, None
        if text:
            await self.steer(text)

    def hang_up_when_done(self, reason: str = "agent_hangup") -> None:
        """Hang up once the voice's goodbye has played (call.py waits for it)."""
        if not self.hang_up_asked.is_set():
            self.hang_up_reason, self.hang_up_after = reason, self.agent_lines
            self.hang_up_asked.set()

    def said(self, line: str) -> None:
        self.agent_lines += 1
        self.last_agent_line = line

    async def whisper(self, text: str) -> None:
        """Background for the voice (where things stand, what the call agent did). If it's
        the voice's turn, it goes in now so the reply uses it. Otherwise it waits until they
        next start talking: sent to a voice that has finished its turn, even silent context
        tends to make it speak up unprompted."""
        if self.voice_owes_reply:
            await self.send(text, speak=False)
        else:
            self.held.append(text)

    async def tell(self, text: str, *, passing: bool = False) -> None:
        """Something to say (the email went out, a task's answer): the only way anything but
        their own text gets spoken. Said as soon as the voice is free; while it's talking, about
        to answer them, or still answering the last thing it was told, it waits for that turn
        to end, with anything else waiting, as one note. Two spoken notes in a row each drew a
        reply, and Live spliced them into one ("hey, you're connected hey! there we go").
        `passing`: only true for now (a check-in on a quiet line); dropped if they speak first."""
        if self.speaking or self.voice_owes_reply or self._answering():
            (self.passing if passing else self.deferred).append(text)
        else:
            await self._speak(text)

    def _answering(self) -> bool:
        return time.monotonic() - self.spoke_at < ANSWER_WAIT

    async def _speak(self, text: str) -> None:
        self.spoke_at = time.monotonic()
        await self.send(text, speak=True)

    async def user_started(self) -> None:
        """They started talking, so the voice stopped. Held background goes in now, and
        anything deferred to the end of its sentence goes in with its reply to them: it was
        still to be said, and a silent note alone reads as already dealt with (a failed task
        went unmentioned while the voice said it was still checking)."""
        self.last_sound = self.heard_at = time.monotonic()
        self.check_ins, self.said_still_looking = 0, False
        self.speaking, self.voice_owes_reply, self.spoke_at = False, True, 0.0
        owed = [f"{UNSAID}\n{text}" for text in self.deferred]
        waiting, self.held, self.deferred, self.passing = [*self.held, *owed], [], [], []
        if waiting:  # one append: several at once each drew their own reply
            await self.send("\n\n".join(waiting), speak=False)

    async def turn_complete(self, *, asked_question: bool) -> None:
        self.speaking, self.spoke_at = False, 0.0
        self.asked_question = asked_question
        await self._steer_waiting()
        deferred, self.deferred, self.passing = [*self.deferred, *self.passing], [], []
        if deferred:
            await self._speak("\n\n".join(deferred))


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
        if call is not None:
            call.last_sound = time.monotonic()  # a text or typing counts as them being there
            if isinstance(event.payload, UserMessage):
                call.wake()  # the call agent acts on texts too ("yes send it")
        note = call_note(event, onboarding=not user.slots.graduated)
        if note is None:
            return None
        if call is None:
            return Decision(trigger_kind=event.kind, verb="drop", note="no call in progress")
        if not call.speaking:
            await (call.tell(note.text) if note.speak else call.send(note.text, speak=False))
            return Decision(trigger_kind=event.kind, verb="send", note="agent not speaking")
        verb, by, confidence = await self._verb(event, call, ctx)
        if verb == "interrupt" and event.origin is not Origin.USER:
            verb, by = "defer", f"{by}; only what they do cuts in"  # the rest waits its turn
        elif call.tool_running and verb == "interrupt":
            verb, by = "defer", f"{by}; a tool is running"
        match verb:
            case "interrupt":
                await call.send(note.text, speak=True)
            case "absorb":
                await call.send(note.text, speak=False)
            case _:
                await call.tell(note.text)
        return Decision(trigger_kind=event.kind, verb=verb, by=by, confidence=confidence)

    def hang_up(self, phone: str) -> bool:
        """end_call during a call: the call hangs up once the voice's goodbye has played."""
        call = self.calls.get(phone)
        if call is None:
            return False
        call.hang_up_when_done()
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
        "conversation": last_lines(ctx.recent, ctx.tz),
        "new_event": event.payload.describe(),
    }


NOTE_CHARS = 1200  # ~300 tokens: well under Live's 500-token limit per send


def _pieces(text: str) -> list[str]:
    """Split by lines into pieces of at most NOTE_CHARS (a longer single line is cut)."""
    pieces: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > NOTE_CHARS:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:NOTE_CHARS])
            line = line[NOTE_CHARS:]
        if current and len(current) + 1 + len(line) > NOTE_CHARS:
            pieces.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current.strip():
        pieces.append(current)
    return pieces or [text]
