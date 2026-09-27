"""The shared agent: one definition for text and voice. Instructions are markdown
fragments plus a dynamic state block; tools write facts through pipeline."""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.tools import ToolDefinition

from app.agent import prompts
from app.agent.context import remembered, their_time, what_you_know
from app.agent.deps import Deps
from app.agent.events import (
    CallOptOut,
    ContactCard,
    Graduated,
    SlotChanged,
    TimezoneLearned,
    ToolCall,
)
from app.agent.objectives import guidance
from app.agent.slots import TzSource
from app.events.payload import Channel, Origin, Payload
from app.google import drafts
from app.google.accounts import Account
from app.google.events import GmailEvent, GmailPhase
from app.memory.events import Forgot, Remembered
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
    memory = remembered(await d.pipeline.memory(d.phone))
    tail = prompts.TEXT if d.medium is Medium.TEXT else ""
    events = await d.pipeline.history(d.phone, limit=RECENT)
    stage = guidance(user, events, d.medium, first_reply=d.first_reply)
    now = their_time(user.slots, d.pipeline.now())
    parts = (f"# What you know\n\n{now}\n{known}", memory, stage, tail)
    return "\n\n".join(p for p in parts if p)


# ---- helpers ----------------------------------------------------------------


async def _record(
    ctx: RunContext[Deps],
    name: str,
    args: dict[str, Any],
    result: dict[str, Any],
    *,
    shown: str | None = None,
    app: str | None = None,
) -> None:
    await _submit(ctx, ToolCall(name=name, args=args, result=result, shown=shown, app=app))


async def _submit(ctx: RunContext[Deps], payload: Payload) -> bool:
    """Submit an event from the agent. False if the pipeline dropped it (nothing changed)."""
    d = ctx.deps
    return await d.pipeline.submit(d.phone, d.origin, d.channel, payload) is not None


async def say(deps: Deps, text: str) -> None:
    """Send a bubble: record it, then push it through the messenger."""
    bubble = AgentMessage(text=text, from_call=deps.medium is Medium.VOICE)
    await deps.pipeline.submit(deps.phone, deps.origin, deps.channel, bubble)
    await deps.env.messenger.send(deps.phone, text)


# Who may use which tool. By text the agent does everything itself. On a call the voice only
# talks (and hangs up); the back office records what was said and sends what was promised.


Prepare = Callable[[RunContext[Deps], ToolDefinition], Awaitable[ToolDefinition | None]]


async def only_text(ctx: RunContext[Deps], tool: ToolDefinition) -> ToolDefinition | None:
    return tool if ctx.deps.medium is Medium.TEXT else None


async def not_the_voice(ctx: RunContext[Deps], tool: ToolDefinition) -> ToolDefinition | None:
    d = ctx.deps
    return tool if d.medium is Medium.TEXT or d.back_office else None


async def only_back_office(ctx: RunContext[Deps], tool: ToolDefinition) -> ToolDefinition | None:
    return tool if ctx.deps.back_office else None


async def only_on_call(ctx: RunContext[Deps], tool: ToolDefinition) -> ToolDefinition | None:
    return tool if ctx.deps.medium is Medium.VOICE else None


def acting(inner: Prepare) -> Prepare:
    """A tool that does something (sends, drafts, texts). On a call, the back office only gets
    it on a run after a voice turn: two keys, their ask or yes and then the voice saying it's
    on it, so nothing happens that the voice doesn't know about."""

    async def prepare(ctx: RunContext[Deps], tool: ToolDefinition) -> ToolDefinition | None:
        if ctx.deps.back_office and not ctx.deps.may_act:
            return None
        return await inner(ctx, tool)

    return prepare


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


def not_a_name(name: str) -> str | None:
    """Why this can't be a name (it shows on their phone), or None. Joke names are fine."""
    if not name:
        return "it's empty"
    if len(name) > 40:
        return "it's too long for a name"
    if any(c in name for c in "<>{}[]`\n\\"):
        return "it looks like code, not a name"
    return None


# ---- tools --------------------------------------------------------------------


@agent.tool(prepare=not_the_voice)
async def set_agent_name(ctx: RunContext[Deps], name: str) -> str:
    """Record the name the user chose for you. Call this the moment they pick one."""
    name = name.strip()
    if problem := not_a_name(name):
        return f"not recorded: {problem}; ask for another name"
    changed = await _submit(ctx, SlotChanged(slot="agent_name", new=name))
    if changed:
        await _submit(ctx, ContactCard(name=name))  # one tap for them to save it
    await _record(ctx, "set_agent_name", {"name": name}, {"changed": changed})
    if not changed:
        return f"{name} was already your name; nothing to do"
    return f"recorded: your name is {name}; your contact card went out, they can tap to save it"


@agent.tool(prepare=acting(not_the_voice))
async def send_contact_card(ctx: RunContext[Deps]) -> str:
    """Text your contact card again (they asked, or the last one got lost)."""
    name = (await ctx.deps.pipeline.user(ctx.deps.phone)).slots.agent_name
    if not name:
        return "you don't have a name yet; settle one first (set_agent_name sends the card)"
    await _submit(ctx, ContactCard(name=name))
    await _record(ctx, "send_contact_card", {}, {})
    return f"your {name} contact card went out"


@agent.tool(prepare=not_the_voice)
async def set_user_name(ctx: RunContext[Deps], name: str) -> str:
    """Record the user's name (what they want to be called)."""
    if problem := not_a_name(name.strip()):
        return f"not recorded: {problem}; ask what they'd like to be called"
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
async def remember(ctx: RunContext[Deps], fact: str, app: str = "") -> str:
    """Remember something about them worth knowing next week, in one short sentence: who
    someone in their life is, a preference, a routine, a constraint, a plan. Only what they
    said or agreed to, never your own guess about them. Not their name, your name, or what they
    want help with first: those have their own tools. `app`: the service it's about, if it's
    about one ("DoorDash" for their usual order), so work in that app can find it."""
    fact = fact.strip()
    if not fact:
        return "nothing to remember"
    if not await _submit(ctx, Remembered(fact=fact, app=app.strip() or None)):
        return "you already remember that"
    return "remembered"


@agent.tool(prepare=not_the_voice)
async def forget(ctx: RunContext[Deps], fact_id: int) -> str:
    """Forget something you remembered, by its number: it was wrong, it changed (then
    remember the new version), or they asked you to."""
    if not await _submit(ctx, Forgot(fact_id=fact_id)):
        return f"there's no fact [{fact_id}] to forget"
    return "forgotten"


@agent.tool(prepare=not_the_voice)
async def set_timezone(ctx: RunContext[Deps], tz: str) -> str:
    """They mentioned where they are or what time it is for them: set their timezone, in the
    same turn. `tz` is the IANA name, like America/Denver or Europe/London."""
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return f"{tz} isn't a timezone name; use one like America/Denver"
    if not await _submit(ctx, TimezoneLearned(tz=tz, source=TzSource.SAID)):
        return f"already set to {tz}"
    return f"their timezone is {tz}"


@agent.tool(prepare=acting(not_the_voice))
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
    await d.env.messenger.set_typing(d.phone, False)  # calling, not typing
    missed = CallEvent(transition=CallTransition.FAILED, reason="no_answer", call_id=call_id)
    d.pipeline.later(RING_SECONDS, d.phone, Origin.SYSTEM, Channel.SYSTEM, missed)
    await _record(ctx, "start_call", {"reason": reason}, {})
    return "calling now; the user's phone is ringing"


@agent.tool(prepare=acting(only_back_office))
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


# ---- their Google account (once connected) -------------------------------------------


async def google_connected(ctx: RunContext[Deps], tool: ToolDefinition) -> ToolDefinition | None:
    """Email and calendar tools: by text or for the back office, once Google is connected."""
    d = ctx.deps
    speaking = d.medium is Medium.VOICE and not d.back_office
    connected = d.user.slots.gmail is GmailPhase.CONNECTED and d.env.google is not None
    return tool if connected and not speaking else None


def _clip(text: str, n: int = 100) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


async def _account(ctx: RunContext[Deps]) -> Account:
    d = ctx.deps
    assert d.env.google is not None
    account = await d.env.google.account(d.phone, (await d.pipeline.user(d.phone)).slots)
    if account is None:
        raise ModelRetry("their Google account isn't connected")
    return account


@agent.tool(prepare=google_connected)
async def search_email(ctx: RunContext[Deps], query: str) -> str:
    """Search their Gmail (Gmail search syntax works, e.g. from:landlord newer_than:7d)."""
    found = await (await _account(ctx)).search(query)
    lines = "\n".join(f"[{m.id}] {m.sender}: {m.subject} ({m.snippet})" for m in found)
    kept = "\n".join(f"[{m.id}] {m.sender}: {m.subject} ({_clip(m.snippet)})" for m in found)
    await _record(
        ctx, "search_email", {"query": query}, {"found": len(found)}, shown=kept, app="google"
    )
    return lines or "none"


@agent.tool(prepare=google_connected)
async def read_email(ctx: RunContext[Deps], message_id: str) -> str:
    """Open one email by the id from search_email."""
    await _record(ctx, "read_email", {"message_id": message_id}, {}, app="google")
    return await (await _account(ctx)).read(message_id)


@agent.tool(prepare=acting(google_connected))
async def draft_email(
    ctx: RunContext[Deps], to: str = "", subject: str = "", body: str = "", ref: str = ""
) -> str:
    """Draft an email in their Gmail; they get a picture of it (exactly what's saved, gaps
    included; nothing is sent). To change a draft, call again with its ref and only the fields
    that change; the rest stay as they were."""
    d = ctx.deps
    try:
        draft = await drafts.save(
            d.pipeline,
            d.phone,
            await _account(ctx),
            ref=ref or None,
            to=to,
            subject=subject,
            body=body,
        )
    except ValueError as exc:
        return str(exc)
    await _record(ctx, "draft_email", {"ref": draft.ref, "subject": subject}, {}, app="google")
    gaps = f"; it's missing {', '.join(draft.missing)}" if draft.missing else ""
    return (
        f"draft {draft.ref} was texted to them as an image{gaps}. don't retype it; ask whether "
        f"to send it or what to change (then call draft_email again with ref={draft.ref})"
    )


@agent.tool(prepare=acting(google_connected))
async def send_draft(ctx: RunContext[Deps], ref: str) -> str:
    """Send a draft you showed them, by its ref, once they've said yes to it."""
    d = ctx.deps
    try:
        sent = await drafts.send(d.pipeline, d.phone, await _account(ctx), ref)
    except ValueError as exc:
        return f"not sent: {exc}"
    await _record(ctx, "send_draft", {"ref": ref}, {}, app="google")
    return f"sent to {sent.to}"


@agent.tool(prepare=google_connected)
async def upcoming_events(ctx: RunContext[Deps], days: int = 7) -> str:
    """Their calendar for the next few days."""
    events = await (await _account(ctx)).upcoming(days)
    lines = "\n".join(f"{e['start']} to {e['end']}: {e['title']}" for e in events)
    await _record(
        ctx, "upcoming_events", {"days": days}, {"found": len(events)}, shown=lines, app="google"
    )
    return lines or "nothing"


@agent.tool(prepare=acting(google_connected))
async def create_event(ctx: RunContext[Deps], title: str, start: str, minutes: int = 60) -> str:
    """Add an event to their calendar. `start` is their local time, like 2026-10-02 15:00.
    Only after they said yes to this exact event. If you don't know their timezone yet, check
    it with them first, in passing, once."""
    d = ctx.deps
    assert d.env.google is not None
    tz = (await d.pipeline.user(d.phone)).slots.zone()
    begins = datetime.fromisoformat(start).replace(tzinfo=tz)
    await (await _account(ctx)).create_event(
        title=title, start=begins, end=begins + timedelta(minutes=minutes)
    )
    await _record(ctx, "create_event", {"title": title, "start": start}, {}, app="google")
    return f"added {title} at {begins:%a %b %-d %-I:%M %p}"


@agent.tool(prepare=acting(google_connected))
async def disconnect_google(ctx: RunContext[Deps]) -> str:
    """They asked to disconnect their Google account. Revokes access for good."""
    d = ctx.deps
    assert d.env.google is not None
    await d.env.google.disconnect(d.phone)
    await _submit(ctx, GmailEvent(phase=GmailPhase.DISCONNECTED))
    await _record(ctx, "disconnect_google", {}, {}, app="google")
    return "disconnected"
