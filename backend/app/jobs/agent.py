"""The job agent: works on one goal in the background, asks them through the chat agent when
it must, and ends with what it found. It never talks to them directly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from pydantic_ai import Agent, CallDeferred, DeferredToolRequests, ModelRetry, RunContext
from pydantic_ai.tools import ToolDefinition

from app.agent import prompts
from app.google.accounts import Account, Google
from app.pipeline import Pipeline


@dataclass(frozen=True)
class JobDeps:
    pipeline: Pipeline
    phone: str
    job: str
    tz: ZoneInfo
    google: Google | None = None


class Outcome(BaseModel):
    """How the job ended. `summary` is for the chat agent: facts, names, times, links."""

    ok: bool
    summary: str


# A run ends with an Outcome, or with the questions it asked (DeferredToolRequests): the job
# then waits, and its next run starts from its saved messages with their answer.
job_agent: Agent[JobDeps, Outcome | DeferredToolRequests] = Agent(
    deps_type=JobDeps,
    output_type=[Outcome, DeferredToolRequests],
    instructions=prompts.JOB,
    defer_model_check=True,
    name="job",
)


@job_agent.instructions
def now(ctx: RunContext[JobDeps]) -> str:
    return f"It's {datetime.now(ctx.deps.tz):%A %B %-d, %-I:%M %p} where they are."


@job_agent.tool_plain
def ask_user(question: str) -> str:
    """Ask them something only they can answer, or for a yes before acting. One short question;
    offer the options when there are some. You pause until they answer."""
    raise CallDeferred(metadata={"question": question})


async def _connected(ctx: RunContext[JobDeps], tool: ToolDefinition) -> ToolDefinition | None:
    return tool if await _account(ctx) is not None else None


async def _account(ctx: RunContext[JobDeps]) -> Account | None:
    d = ctx.deps
    if d.google is None:
        return None
    return await d.google.account(d.phone, (await d.pipeline.user(d.phone)).slots)


async def _need_account(ctx: RunContext[JobDeps]) -> Account:
    account = await _account(ctx)
    if account is None:
        raise ModelRetry("their Google account isn't connected")
    return account


@job_agent.tool(prepare=_connected)
async def search_email(ctx: RunContext[JobDeps], query: str) -> str:
    """Search their Gmail (Gmail search syntax works, e.g. from:landlord newer_than:7d)."""
    found = await (await _need_account(ctx)).search(query)
    return "\n".join(f"[{m.id}] {m.sender}: {m.subject} ({m.snippet})" for m in found) or "none"


@job_agent.tool(prepare=_connected)
async def read_email(ctx: RunContext[JobDeps], message_id: str) -> str:
    """Open one email by the id from search_email."""
    return await (await _need_account(ctx)).read(message_id)


@job_agent.tool(prepare=_connected)
async def upcoming_events(ctx: RunContext[JobDeps], days: int = 7) -> str:
    """Their calendar for the next few days."""
    events = await (await _need_account(ctx)).upcoming(days)
    return "\n".join(f"{e['start']} to {e['end']}: {e['title']}" for e in events) or "nothing"
