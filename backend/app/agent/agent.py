"""The shared agent: one definition for text and voice. Instructions are markdown
fragments plus a dynamic state block; tools write facts through pipeline."""

from __future__ import annotations

import re
import uuid
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.tools import ToolDefinition

from app.agent import prompts
from app.agent.context import what_you_know
from app.agent.deps import Deps
from app.agent.events import CallOptOut, ContactCard, Graduated, SlotChanged, ToolCall
from app.agent.objectives import guidance
from app.events.payload import Channel, Origin, Payload
from app.gmail.events import GmailEvent, GmailPhase
from app.pipeline import RECENT
from app.text.events import AgentMessage
from app.users.user import Medium
from app.voice.call_state import CallEvent, CallTransition, Initiator


class Bubbles(BaseModel):
    """The text channel's output: zero to four bubbles, and optionally a tapback."""

    bubbles: list[str] = Field(default_factory=list, max_length=4)
    react: str | None = Field(
        default=None, description="An emoji tapback on their latest message, e.g. ❤️ 👍 😂"
    )


agent: Agent[Deps, str] = Agent(
    deps_type=Deps,
    instructions=[prompts.PERSONA, prompts.ONBOARDING],
    defer_model_check=True,
    name="onboarding",
)


@agent.instructions
async def dynamic_instructions(ctx: RunContext[Deps]) -> str:
    d = ctx.deps
    if d.medium is Medium.VOICE and not d.back_office:
        return ""  # the Live backend: its snapshot would go stale; state reaches it as notes
    # Read fresh: tools earlier in this same run may have just saved a name.
    user = await d.pipeline.user(d.phone)
    known = what_you_know(user.slots, user.call)
    tail = prompts.TEXT if d.medium is Medium.TEXT else ""
    events = await d.pipeline.history(d.phone, limit=RECENT)
    stage = guidance(user, events, d.medium, first_reply=d.first_reply)
    return f"# What you know\n\n{known}\n\n{stage}\n\n{tail}"


# ---- helpers ----------------------------------------------------------------


async def _record(
    ctx: RunContext[Deps], name: str, args: dict[str, Any], result: dict[str, Any]
) -> None:
    await _submit(ctx, ToolCall(name=name, args=args, result=result))


async def _submit(ctx: RunContext[Deps], payload: Payload) -> bool:
    """Submit an event from the agent. False if the pipeline dropped it (nothing changed)."""
    d = ctx.deps
    return await d.pipeline.submit(d.phone, d.origin, d.channel, payload) is not None


async def say(deps: Deps, text: str) -> None:
    """Send a bubble: record it, then push it through the messenger."""
    bubble = AgentMessage(text=text, from_call=deps.medium is Medium.VOICE)
    await deps.pipeline.submit(deps.phone, deps.origin, deps.channel, bubble)
    await deps.messenger.send(deps.phone, text)


# Who may use which tool. By text the agent does everything itself. On a call the voice only
# talks (and hangs up); the back office records what was said and sends what was promised.


async def only_text(ctx: RunContext[Deps], tool: ToolDefinition) -> ToolDefinition | None:
    return tool if ctx.deps.medium is Medium.TEXT else None


async def not_the_voice(ctx: RunContext[Deps], tool: ToolDefinition) -> ToolDefinition | None:
    d = ctx.deps
    return tool if d.medium is Medium.TEXT or d.back_office else None


async def only_back_office(ctx: RunContext[Deps], tool: ToolDefinition) -> ToolDefinition | None:
    return tool if ctx.deps.back_office else None


async def only_on_call(ctx: RunContext[Deps], tool: ToolDefinition) -> ToolDefinition | None:
    return tool if ctx.deps.medium is Medium.VOICE else None


ASKS_FOR_CALL = re.compile(r"\b(call|ring|phone)\b", re.IGNORECASE)


async def may_call(ctx: RunContext[Deps], tool: ToolDefinition) -> ToolDefinition | None:
    """After a no, the agent can't call unless their latest message mentions a call."""
    if ctx.deps.medium is not Medium.TEXT:
        return None
    if not ctx.deps.user.slots.no_calls:
        return tool
    return tool if ASKS_FOR_CALL.search(_latest_user_text(ctx)) else None


def _latest_user_text(ctx: RunContext[Deps]) -> str:
    for message in reversed(ctx.messages):
        if isinstance(message, ModelRequest):
            return "\n".join(
                p.content
                for p in message.parts
                if isinstance(p, UserPromptPart)
                and isinstance(p.content, str)
                and not p.content.startswith("[note:")
            )
    return ""


# ---- tools --------------------------------------------------------------------


@agent.tool(prepare=not_the_voice)
async def set_agent_name(ctx: RunContext[Deps], name: str) -> str:
    """Record the name the user chose for you. Call this the moment they pick one."""
    name = name.strip()
    changed = await _submit(ctx, SlotChanged(slot="agent_name", new=name))
    if changed:
        await _submit(ctx, ContactCard(name=name))  # one tap for them to save it
    await _record(ctx, "set_agent_name", {"name": name}, {"changed": changed})
    if not changed:
        return f"{name} was already your name; nothing to do"
    return f"recorded: your name is {name}; your contact card went out, they can tap to save it"


@agent.tool(prepare=not_the_voice)
async def set_user_name(ctx: RunContext[Deps], name: str) -> str:
    """Record the user's name (what they want to be called)."""
    changed = await _submit(ctx, SlotChanged(slot="user_name", new=name.strip()))
    await _record(ctx, "set_user_name", {"name": name}, {"changed": changed})
    if not changed:
        return f"they were already called {name.strip()}; nothing to do"
    return f"recorded: user is called {name.strip()}"


@agent.tool(prepare=not_the_voice)
async def record_help_need(ctx: RunContext[Deps], need: str) -> str:
    """Record one concrete thing the user wants help with, in their words."""
    changed = await _submit(ctx, SlotChanged(slot="help_need", new=need.strip()))
    await _record(ctx, "record_help_need", {"need": need}, {"changed": changed})
    return "recorded" if changed else "already recorded; nothing to do"


@agent.tool(prepare=not_the_voice)
async def send_gmail_link(ctx: RunContext[Deps]) -> str:
    """Text the user a link to connect their Gmail. Say in your own words that you sent it."""
    d = ctx.deps
    if not await _submit(ctx, GmailEvent(phase=GmailPhase.LINK_SENT)):
        return "the link was already sent (or Gmail is connected); it is in their texts"
    link = f"{d.env.app_base_url}/api/auth/google/start?phone={d.phone}"
    if d.medium is Medium.TEXT:
        d.after_reply.append(link)  # sent after this reply's bubbles, so they introduce it
    else:
        await say(d, link)
    await _record(ctx, "send_gmail_link", {}, {"link": link})
    return "link sent by text; the user will tap it when ready"


@agent.tool(prepare=not_the_voice)
async def skip_gmail(ctx: RunContext[Deps]) -> str:
    """The user declined to connect Gmail. Do not ask again."""
    await _submit(ctx, GmailEvent(phase=GmailPhase.SKIPPED))
    await _record(ctx, "skip_gmail", {}, {})
    return "recorded: gmail skipped"


@agent.tool(prepare=only_text)
async def no_call(ctx: RunContext[Deps]) -> str:
    """They'd rather not talk on the phone. Stops you offering a call again."""
    await _submit(ctx, CallOptOut())
    await _record(ctx, "no_call", {}, {})
    return "recorded: no calls unless they ask"


RING_SECONDS = 30.0  # an unanswered call becomes a missed call


@agent.tool(prepare=may_call)
async def start_call(ctx: RunContext[Deps], reason: str) -> str:
    """Call the user now. `reason`: a few words of background for the call, not a script."""
    d = ctx.deps
    call_id = uuid.uuid4().hex
    ringing = CallEvent(
        transition=CallTransition.RINGING,
        reason=reason,
        initiated_by=Initiator.AGENT,
        call_id=call_id,
    )
    if not await _submit(ctx, ringing):
        return "can't call right now; a call is already ringing or in progress"
    d.placed_call = True  # the call is this reply; its bubbles are dropped
    missed = CallEvent(transition=CallTransition.FAILED, reason="no_answer", call_id=call_id)
    d.pipeline.later(RING_SECONDS, d.phone, Origin.SYSTEM, Channel.SYSTEM, missed)
    await _record(ctx, "start_call", {"reason": reason}, {})
    return "calling now; the user's phone is ringing"


@agent.tool(prepare=only_back_office)
async def send_text(ctx: RunContext[Deps], text: str) -> str:
    """Text the user during the call. Only what the voice said out loud it would text."""
    await say(ctx.deps, text)
    await _record(ctx, "send_text", {"text": text}, {})
    return "texted"


HANG_UP_AFTER = 3.0  # seconds, so the goodbye finishes playing before the line drops


@agent.tool(prepare=only_on_call)
async def end_call(ctx: RunContext[Deps]) -> str:
    """Hang up the call. Only after the voice has said goodbye."""
    d = ctx.deps
    if d.env.hang_up is None or not d.env.hang_up(d.phone):
        ended = CallEvent(transition=CallTransition.ENDED, reason="agent_hangup")
        d.pipeline.later(HANG_UP_AFTER, d.phone, d.origin, d.channel, ended)
    await _record(ctx, "end_call", {}, {})
    return "call ending"


@agent.tool(prepare=only_text)  # never mid-call: onboarding wraps up by text afterwards
async def graduate(ctx: RunContext[Deps], first_action: str) -> str:
    """Move the user into the main experience. Say what you will do first, in one line."""
    await _submit(ctx, Graduated())
    await _record(ctx, "graduate", {"first_action": first_action}, {})
    return "graduated"
