"""The shared agent: one definition for text and voice. Instructions are markdown
fragments plus a dynamic state block; tools write facts through the user object."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.tools import ToolDefinition

from app.agent import prompts
from app.agent.deps import Deps
from app.agent.types import Graduated, ToolCall
from app.agent.views import state_block
from app.calls.types import CallEvent, CallTransition, Initiator
from app.gmail.types import GmailEvent, GmailPhase
from app.routing.types import Medium
from app.text.types import AgentMessage


class Reply(BaseModel):
    """The text channel's output: zero to four bubbles."""

    bubbles: list[str] = Field(default_factory=list, max_length=4)


agent: Agent[Deps, str] = Agent(
    deps_type=Deps,
    instructions=[prompts.PERSONA, prompts.STYLE, prompts.WORK],
    defer_model_check=True,
    name="onboarding",
)


@agent.instructions
def dynamic_instructions(ctx: RunContext[Deps]) -> str:
    tail = prompts.TEXT_TAIL if ctx.deps.medium is Medium.TEXT else ""
    return f"Current state:\n{state_block(ctx.deps.user.slots, ctx.deps.user.call)}\n\n{tail}"


# ---- helpers ----------------------------------------------------------------


def _record(ctx: RunContext[Deps], name: str, args: dict[str, Any], result: dict[str, Any]) -> None:
    d = ctx.deps
    d.submit(d.origin, d.event_channel, ToolCall(name=name, args=args, result=result))


async def say(deps: Deps, text: str) -> None:
    """Send a bubble: record it, then push it through the channel."""
    deps.submit(
        deps.origin,
        deps.event_channel,
        AgentMessage(text=text, from_call=deps.medium is Medium.VOICE),
    )
    await deps.channel.send(deps.user.phone, text)


async def _only(
    medium: Medium, ctx: RunContext[Deps], tool: ToolDefinition
) -> ToolDefinition | None:
    return tool if ctx.deps.medium is medium else None


async def only_text(ctx: RunContext[Deps], tool: ToolDefinition) -> ToolDefinition | None:
    return await _only(Medium.TEXT, ctx, tool)


async def only_voice(ctx: RunContext[Deps], tool: ToolDefinition) -> ToolDefinition | None:
    return await _only(Medium.VOICE, ctx, tool)


# ---- tools --------------------------------------------------------------------


@agent.tool
async def set_agent_name(ctx: RunContext[Deps], name: str) -> str:
    """Record the name the user chose for you. Call this the moment they pick one."""
    d = ctx.deps
    changed = await d.user.set_slot(
        "agent_name", name.strip(), origin=d.origin, channel=d.event_channel
    )
    _record(ctx, "set_agent_name", {"name": name}, {"changed": changed})
    return f"recorded: your name is {name.strip()}"


@agent.tool
async def set_user_name(ctx: RunContext[Deps], name: str) -> str:
    """Record the user's name (what they want to be called)."""
    d = ctx.deps
    changed = await d.user.set_slot(
        "user_name", name.strip(), origin=d.origin, channel=d.event_channel
    )
    _record(ctx, "set_user_name", {"name": name}, {"changed": changed})
    return f"recorded: user is called {name.strip()}"


@agent.tool
async def record_help_need(ctx: RunContext[Deps], need: str) -> str:
    """Record one concrete thing the user wants help with, in their words."""
    d = ctx.deps
    changed = await d.user.set_slot(
        "help_need", need.strip(), origin=d.origin, channel=d.event_channel
    )
    _record(ctx, "record_help_need", {"need": need}, {"changed": changed})
    return "recorded"


@agent.tool
async def send_gmail_link(ctx: RunContext[Deps]) -> str:
    """Text the user a link to connect their Gmail. Say in your own words that you sent it."""
    d = ctx.deps
    link = f"{d.app_base_url}/api/auth/google/start?phone={d.user.phone}"
    await say(d, link)
    await d.user.set_slot("gmail", GmailPhase.LINK_SENT, origin=d.origin, channel=d.event_channel)
    d.submit(d.origin, d.event_channel, GmailEvent(phase=GmailPhase.LINK_SENT))
    _record(ctx, "send_gmail_link", {}, {"link": link})
    return "link sent by text; the user will tap it when ready"


@agent.tool
async def skip_gmail(ctx: RunContext[Deps]) -> str:
    """The user declined to connect Gmail. Do not ask again."""
    d = ctx.deps
    await d.user.set_slot("gmail", GmailPhase.SKIPPED, origin=d.origin, channel=d.event_channel)
    d.submit(d.origin, d.event_channel, GmailEvent(phase=GmailPhase.SKIPPED))
    _record(ctx, "skip_gmail", {}, {})
    return "recorded: gmail skipped"


@agent.tool(prepare=only_text)
async def start_call(ctx: RunContext[Deps], reason: str) -> str:
    """Call the user now. Give the reason for the call in one line; you will have it on the call."""
    d = ctx.deps
    d.submit(
        d.origin,
        d.event_channel,
        CallEvent(transition=CallTransition.RINGING, reason=reason, initiated_by=Initiator.AGENT),
    )
    _record(ctx, "start_call", {"reason": reason}, {})
    return "calling now; the user's phone is ringing"


@agent.tool(prepare=only_voice)
async def end_call(ctx: RunContext[Deps]) -> str:
    """Hang up the call after saying goodbye."""
    d = ctx.deps
    d.submit(
        d.origin, d.event_channel, CallEvent(transition=CallTransition.ENDED, reason="agent_hangup")
    )
    _record(ctx, "end_call", {}, {})
    return "call ending"


@agent.tool
async def graduate(ctx: RunContext[Deps], first_action: str) -> str:
    """Move the user into the main experience. Say what you will do first, in one line."""
    d = ctx.deps
    await d.user.set_slot("graduated", True, origin=d.origin, channel=d.event_channel)
    d.submit(d.origin, d.event_channel, Graduated())
    _record(ctx, "graduate", {"first_action": first_action}, {})
    return "graduated"
