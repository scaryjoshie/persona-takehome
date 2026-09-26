"""Render the log for a model. Rows are storage; this is the conversation."""

from __future__ import annotations

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart

from app.agent.slots import Slots
from app.calls.state import CallState
from app.events.event import Event
from app.events.payload import Role, Turn
from app.routing.types import RoutingContext


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


def state_block(slots: Slots, call: CallState) -> str:
    """The facts rendered into every prompt. This is the whole steering mechanism."""
    gmail = "not asked" if slots.gmail is None else slots.gmail.value
    if slots.gmail_email:
        gmail += f" ({slots.gmail_email})"
    lines = [
        f"agent_name: {slots.agent_name or '(not chosen)'}",
        f"user_name: {slots.user_name or '(unknown)'}",
        f"help_need: {slots.help_need or '(unknown)'}",
        f"gmail: {gmail}",
        f"graduated: {'yes' if slots.graduated else 'no'}",
        f"call: {call.phase.value}" + (f", reason: {call.reason}" if call.reason else ""),
        "still need: " + (", ".join(slots.missing()) or "nothing"),
    ]
    return "\n".join(lines)


def decider_view(ctx: RoutingContext, *, max_chars: int = 1200) -> str:
    """Compact rendering for a small classifier; the Jev wrapper budgets on this."""
    run = "none"
    if ctx.run:
        run = (
            f"{ctx.run.medium.value}, side_effect={ctx.run.side_effect_in_flight}, "
            f"last_turn_question={ctx.run.last_agent_turn_was_question}, "
            f"inferred={ctx.run.inferred}"
        )
    header = [
        f"floor: {ctx.floor.value}; call: {ctx.call.phase.value}",
        f"run: {run}",
        f"trigger: {ctx.trigger.payload.model_dump_json()}",
        "still need: " + (", ".join(ctx.still_missing) or "nothing"),
        "recent:",
    ]
    body = "\n".join(f"  {t.role.value}: {t.text}" for t in turns(list(ctx.recent)))
    text = "\n".join(header) + "\n" + body
    return text if len(text) <= max_chars else text[-max_chars:]
