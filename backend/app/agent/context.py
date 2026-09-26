"""Render the log for a model. Rows are storage; this is the conversation."""

from __future__ import annotations

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart

from app.agent.slots import Slots
from app.events.event import Event
from app.events.payload import Role, Turn
from app.gmail.events import GmailPhase
from app.voice.call_state import CallPhase, CallState


def turns(events: tuple[Event, ...] | list[Event]) -> list[Turn]:
    """Each event renders itself; adjacent turns with the same role merge."""
    out: list[Turn] = []
    for event in events:
        turn = event.payload.turn(event.ts)
        if turn is None:
            continue
        if out and out[-1].role is turn.role:
            out[-1] = Turn(turn.role, f"{out[-1].text}\n{turn.text}")
        else:
            out.append(turn)
    return out


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


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def trim_history(
    messages: list[ModelMessage], *, max_messages: int, max_tokens: int
) -> list[ModelMessage]:
    """The most recent messages under both caps. GPT-Live seeding: 128 / 8192."""
    kept: list[ModelMessage] = []
    budget = max_tokens
    for m in reversed(messages):
        cost = estimate_tokens(_text_of(m))
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
            lines.append(f"Gmail is connected ({slots.gmail_email}).")
        case GmailPhase.SKIPPED:
            lines.append("They said no to Gmail. Don't bring it up again.")
        case GmailPhase.FAILED:
            lines.append("Connecting Gmail failed. Offer to try again once.")
    if slots.no_calls:
        lines.append("They'd rather not do a call. Don't offer one again unless they ask.")
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
