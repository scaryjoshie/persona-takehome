"""Render the log for a model. Rows are storage; this is the conversation."""

from __future__ import annotations

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart

from app.agent.slots import Slots
from app.events.event import Event
from app.events.payload import Role, Turn
from app.google.events import GmailPhase
from app.voice.call_state import CallEvent, CallPhase, CallState, CallTransition
from app.voice.events import VoiceUtterance


def spoken_order(events: tuple[Event, ...] | list[Event]) -> list[Event]:
    """A call's lines in the order they were said. Live finalizes a line when its speaker
    finishes, so a long agent line is logged after the "yeah" said halfway through it; its
    turn ids ("agent-3", "user-4") number both speakers in spoken order, per call. Anything
    else (a saved name, a text) stays after every line that was logged before it."""
    out: list[Event] = []
    segment: list[Event] = []

    def settle() -> None:
        lines = sorted((e for e in segment if _turn_index(e) >= 0), key=_turn_index)
        rank = {id(e): r for r, e in enumerate(lines)}
        anchored: dict[int, list[Event]] = {}
        latest = -1  # the highest-ranked line logged so far
        for e in segment:
            if id(e) in rank:
                latest = max(latest, rank[id(e)])
            else:
                anchored.setdefault(latest, []).append(e)
        out.extend(anchored.get(-1, []))
        for r, line in enumerate(lines):
            out.append(line)
            out.extend(anchored.get(r, []))
        segment.clear()

    for event in events:
        p = event.payload
        if isinstance(p, CallEvent) and p.transition is CallTransition.CONNECTED:
            settle()  # numbering starts over with each call
        segment.append(event)
    settle()
    return out


def _turn_index(event: Event) -> int:
    """A call line's place in the call, or -1 (not a line, or no number: no captions came)."""
    if not isinstance(event.payload, VoiceUtterance):
        return -1
    number = (event.payload.turn_id or "").rpartition("-")[2]
    return int(number) if number.isdigit() else -1


def turns(events: tuple[Event, ...] | list[Event]) -> list[Turn]:
    """Each event renders itself; adjacent turns with the same role merge."""
    out: list[Turn] = []
    for event in spoken_order(events):
        turn = event.payload.turn(event.ts)
        if turn is None:
            continue
        if out and out[-1].role is turn.role:
            out[-1] = Turn(turn.role, f"{out[-1].text}\n{turn.text}")
        else:
            out.append(turn)
    return out


def last_lines(events: list[Event], n: int = 12) -> list[str]:
    """The last few events as "role: text" lines, for Jev's view of the conversation."""
    lines: list[str] = []
    for event in spoken_order(events)[-n:]:
        turn = event.payload.turn(event.ts)
        if turn is not None:
            lines.append(f"{turn.role.value}: {turn.text}")
    return lines


def to_model_messages(events: tuple[Event, ...] | list[Event]) -> list[ModelMessage]:
    """Notes ride as bracketed user-role parts. Adjacent request-side turns share one
    ModelRequest so requests and responses alternate."""
    messages: list[ModelMessage] = []
    for turn in turns(events):
        if turn.role is Role.ASSISTANT:
            messages.append(ModelResponse(parts=[TextPart(turn.text)]))
            continue
        text = turn.text if turn.role is Role.USER else bracket(turn.text)
        part = UserPromptPart(text)
        if messages and isinstance(messages[-1], ModelRequest):
            messages[-1] = ModelRequest(parts=[*messages[-1].parts, part])
        else:
            messages.append(ModelRequest(parts=[part]))
    return messages


def bracket(lines: str) -> str:
    return "\n".join(f"[note: {line}]" for line in lines.split("\n"))


def trim_history(
    messages: list[ModelMessage], *, max_messages: int, max_tokens: int
) -> list[ModelMessage]:
    """The most recent messages under both caps. GPT-Live seeding: 128 / 8192."""
    kept: list[ModelMessage] = []
    budget = max_tokens
    for m in reversed(messages):
        cost = max(1, len(_text_of(m)) // 4)  # about four characters a token
        if len(kept) >= max_messages or cost > budget:
            break
        kept.append(m)
        budget -= cost
    kept.reverse()
    return kept


def _text_of(m: ModelMessage) -> str:
    return "\n".join(
        p.content
        for p in m.parts
        if isinstance(p, UserPromptPart | TextPart) and isinstance(p.content, str)
    )


SET_ASIDE = {
    "agent_name": "naming you",
    "user_name": "giving their name",
    "help_need": "saying what they need",
}


def what_you_know(slots: Slots, call: CallState) -> str:
    """The facts, in plain sentences, rendered into every prompt."""
    lines = [
        f"Your name is {slots.agent_name}."
        if slots.agent_name
        else "You don't have a name yet; they haven't picked one.",
        f"They go by {slots.user_name}." if slots.user_name else "You don't know their name yet.",
        f"They want help with: {slots.help_need}."
        if slots.help_need
        else "You don't know what they want help with yet.",
    ]
    match slots.gmail:
        case None:
            lines.append("Gmail isn't connected and you haven't sent the link.")
        case GmailPhase.LINK_SENT:
            lines.append(
                "You've texted the Gmail link; it isn't connected yet. Don't send it again."
            )
        case GmailPhase.CONNECTED:
            lines.append(
                f"Their Google account is connected ({slots.gmail_email}): "
                "your email and calendar tools work on their real Gmail and Calendar."
            )
        case GmailPhase.SKIPPED:
            lines.append("They said no to Gmail. Don't bring it up again.")
        case GmailPhase.FAILED:
            lines.append("Connecting Gmail failed. Offer to try again once.")
        case GmailPhase.DISCONNECTED:
            lines.append("They disconnected Google. Don't bring it up unless they ask.")
    if slots.no_calls:
        lines.append("They'd rather not do a call. Don't offer one again unless they ask.")
    if slots.set_aside:
        skipped = ", ".join(SET_ASIDE[step] for step in slots.set_aside)
        lines.append(
            f"They'd rather not do this for now: {skipped}. Leave it unless they bring it up."
        )
    if slots.graduated:
        lines.append("They've graduated: onboarding is done.")
    if call.phase is CallPhase.CONNECTED:
        lines.append("You're on a call with them right now.")
    missing = slots.missing()
    names_sorted = slots.agent_name is not None and slots.user_name is not None
    if not names_sorted and "help_need" in missing:
        missing = tuple(m for m in missing if m != "help_need")  # the ask comes after names
    lines.append(
        "Still missing: " + ", ".join(m.replace("_", " ") for m in missing) + "."
        if missing
        else "You have everything onboarding needs."
    )
    if names_sorted and slots.help_need is None:
        lines.append("Names are sorted, so when it fits naturally, it's time for the ask.")
    return "\n".join(lines)
