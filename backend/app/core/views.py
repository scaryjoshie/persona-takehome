"""Context views: render the store into what a model sees. See docs 04.

Nothing is stored as "context". Rows are the storage; these functions build the
conversation at prompt time. Merging adjacent same-speaker rows happens here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, assert_never

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart

from app.core.types import (
    AgentMessage,
    CallEvent,
    CallState,
    Decision,
    Event,
    GmailEvent,
    Graduated,
    RoutingContext,
    SlotChanged,
    Slots,
    ToolCall,
    UserMessage,
    VoiceUtterance,
)

Role = Literal["user", "assistant", "note"]


@dataclass(frozen=True)
class Turn:
    role: Role
    text: str
    seq: int


def _hm(event: Event) -> str:
    return event.ts.strftime("%H:%M")


def turn_for(event: Event) -> Turn | None:
    """One event → one turn, or None if the model should not see it."""
    p = event.payload
    match p:
        case UserMessage(text=text):
            return Turn("user", text, event.seq)
        case AgentMessage(text=text, via=via):
            if via == "voice":
                return Turn("note", f"you texted (from the call): {text}", event.seq)
            return Turn("assistant", text, event.seq)
        case VoiceUtterance(speaker=speaker, text=text):
            if not text:
                return None
            who = "user" if speaker == "user" else "you"
            return Turn("note", f"on call, {who} said: {text}", event.seq)
        case ToolCall(name=name, args=args, result=result):
            arg_s = ", ".join(f"{k}={v!r}" for k, v in args.items())
            res_s = "" if result is None else f" → {result}"
            return Turn("note", f"you called {name}({arg_s}){res_s}", event.seq)
        case CallEvent(phase=phase, reason=reason):
            t = _hm(event)
            if phase == "connected":
                return Turn("note", f"call connected {t}", event.seq)
            if phase == "ended":
                return Turn("note", f"call ended {t}, reason: {reason or 'unknown'}", event.seq)
            if phase == "declined":
                return Turn("note", f"user declined the call {t}", event.seq)
            if phase == "failed":
                return Turn("note", f"call failed {t}, reason: {reason or 'unknown'}", event.seq)
            if phase == "ringing":
                return Turn("note", f"you started calling the user {t}", event.seq)
            return None  # connecting is noise
        case GmailEvent(phase=phase, email=email):
            if phase == "connected":
                return Turn("note", f"Gmail connected as {email}", event.seq)
            if phase == "link_sent":
                return Turn("note", "Gmail link sent by text", event.seq)
            if phase == "skipped":
                return Turn("note", "user skipped Gmail; do not ask again", event.seq)
            return Turn("note", "Gmail connection failed", event.seq)
        case Graduated():
            return Turn("note", "user graduated to the main experience", event.seq)
        case SlotChanged() | Decision():
            return None  # state and routing internals; slots are rendered separately
        case _:
            assert_never(p)


def merge_turns(events: tuple[Event, ...] | list[Event]) -> list[Turn]:
    """Consecutive turns with the same role merge into one (line-joined)."""
    out: list[Turn] = []
    for event in events:
        turn = turn_for(event)
        if turn is None:
            continue
        if out and out[-1].role == turn.role:
            prev = out[-1]
            out[-1] = Turn(prev.role, f"{prev.text}\n{turn.text}", prev.seq)
        else:
            out.append(turn)
    return out


def to_model_messages(events: tuple[Event, ...] | list[Event]) -> list[ModelMessage]:
    """pydantic-ai message history. Notes ride as bracketed user-role parts; adjacent
    request-side turns share one ModelRequest so requests and responses alternate."""
    messages: list[ModelMessage] = []
    for turn in merge_turns(events):
        if turn.role == "assistant":
            messages.append(ModelResponse(parts=[TextPart(turn.text)]))
            continue
        text = turn.text if turn.role == "user" else _bracket(turn.text)
        part = UserPromptPart(text)
        if messages and isinstance(messages[-1], ModelRequest):
            messages[-1] = ModelRequest(parts=[*messages[-1].parts, part])
        else:
            messages.append(ModelRequest(parts=[part]))
    return messages


def _bracket(note_lines: str) -> str:
    return "\n".join(f"[note: {line}]" for line in note_lines.split("\n"))


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _message_text(m: ModelMessage) -> str:
    parts: list[str] = []
    for part in m.parts:
        content = getattr(part, "content", "")
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


def trim_history(
    messages: list[ModelMessage], *, max_messages: int, max_tokens: int
) -> list[ModelMessage]:
    """Keep the most recent messages under both caps. For Live seeding: 128 / 8192."""
    kept: list[ModelMessage] = []
    budget = max_tokens
    for m in reversed(messages):
        cost = estimate_tokens(_message_text(m))
        if len(kept) >= max_messages or cost > budget:
            break
        kept.append(m)
        budget -= cost
    kept.reverse()
    return kept


def state_block(slots: Slots, call: CallState) -> str:
    """The facts rendered into every prompt. This is the entire steering mechanism."""
    lines = [
        f"agent_name: {slots.agent_name or '(not chosen)'}",
        f"user_name: {slots.user_name or '(unknown)'}",
        f"help_need: {slots.help_need or '(unknown)'}",
        f"gmail: {slots.gmail}" + (f" ({slots.gmail_email})" if slots.gmail_email else ""),
        f"graduated: {'yes' if slots.graduated else 'no'}",
        f"call: {call.phase}" + (f", reason: {call.reason}" if call.reason else ""),
    ]
    missing = slots.missing()
    lines.append("still need: " + (", ".join(missing) if missing else "nothing"))
    return "\n".join(lines)


def decider_view(ctx: RoutingContext, *, max_chars: int = 1200) -> str:
    """Compact rendering for a small classifier. The Jev wrapper budgets on this."""
    head = [
        f"floor: {ctx.floor}; call: {ctx.call.phase}",
        "run: "
        + (
            "none"
            if ctx.run is None
            else (
                f"{ctx.run.medium}, side_effect={ctx.run.side_effect_in_flight}, "
                f"last_turn_question={ctx.run.last_agent_turn_was_question}, "
                f"inferred={ctx.run.inferred}"
            )
        ),
        f"trigger: {ctx.trigger.model_dump_json()}",
        "still need: " + (", ".join(ctx.slots.missing()) or "nothing"),
        "recent:",
    ]
    body = "\n".join(f"  {t.role}: {t.text}" for t in merge_turns(list(ctx.recent)))
    text = "\n".join(head) + "\n" + body
    return text if len(text) <= max_chars else text[-max_chars:]
