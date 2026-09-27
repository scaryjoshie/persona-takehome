"""The rolling summary: what a model sees of the conversation before its latest messages.

It runs in the background after a reply or a call, and only in steps, with a buffer on
each side:
- nothing happens until the conversation after the summary passes HIGH_TOKENS;
- then everything but the latest KEEP_TOKENS is folded into the summary, so the next step
  is another HIGH_TOKENS - KEEP_TOKENS away, and a model always sees at least KEEP_TOKENS
  of the conversation word for word after the summary.
The cut never falls inside a call: a call is summarized whole or not at all.
"""

from __future__ import annotations

import logging
from datetime import tzinfo

from pydantic_ai import Agent
from pydantic_ai.models import Model

from app.agent.prompts import read_md
from app.events.event import Event
from app.memory.service import Summary
from app.pipeline import Pipeline
from app.voice.call_state import CallEvent, CallTransition

log = logging.getLogger(__name__)

HIGH_TOKENS = 40_000  # summarize once the conversation after the summary passes this
KEEP_TOKENS = 20_000  # and leave at least this much of the latest word for word

summarizer: Agent[None, str] = Agent(
    instructions=read_md("summary"), output_type=str, defer_model_check=True, name="summary"
)
_running: set[str] = set()


def tokens(event: Event) -> int:
    turn = event.payload.turn(event.ts)
    return 0 if turn is None else max(1, len(turn.text) // 4)  # about four characters a token


def cut(events: list[Event]) -> int:
    """How many of these events (the oldest) to fold into the summary now; 0 for none yet."""
    if sum(tokens(e) for e in events) < HIGH_TOKENS:
        return 0
    kept, at = 0, len(events)
    while at > 0 and kept < KEEP_TOKENS:
        at -= 1
        kept += tokens(events[at])
    call_from: int | None = None  # where the call open at the cut began
    for i, e in enumerate(events[:at]):
        if isinstance(e.payload, CallEvent):
            if e.payload.transition is CallTransition.CONNECTED:
                call_from = i
            elif e.payload.transition in (CallTransition.ENDED, CallTransition.FAILED):
                call_from = None
    return call_from if call_from is not None else at


def transcript(events: list[Event], tz: tzinfo) -> str:
    """The events as dated lines in their timezone, for the summarizer."""
    lines: list[str] = []
    day = None
    for e in events:
        at = e.ts.astimezone(tz)
        turn = e.payload.turn(at)
        if turn is None:
            continue
        if at.date() != day:
            day = at.date()
            lines.append(f"-- {day:%A %B %-d} --")
        lines.append(f"{turn.role.value} ({e.channel.value}): {turn.text}")
    return "\n".join(lines)


async def summarize_if_due(pipeline: Pipeline, model: Model, phone: str) -> bool:
    """Fold the oldest part of the conversation into the summary, if it's time. True if it
    did. One at a time per user; a second request while one runs is dropped (the next
    reply asks again)."""
    if phone in _running:
        return False
    _running.add(phone)
    try:
        memory, events = await pipeline.conversation(phone)
        n = cut(events)
        if n == 0:
            return False
        before = memory.summary.text if memory.summary else "(none yet)"
        tz = (await pipeline.user(phone)).slots.zone()
        prompt = (
            f"# Your notes so far\n\n{before}\n\n# What came next\n\n{transcript(events[:n], tz)}"
        )
        result = await summarizer.run(prompt, model=model)
        text = result.output.strip()
        if not text:
            return False
        await pipeline.save_summary(phone, Summary(through_seq=events[n - 1].seq, text=text))
        log.info("%s: summarized through event %d", phone, events[n - 1].seq)
        return True
    finally:
        _running.discard(phone)
