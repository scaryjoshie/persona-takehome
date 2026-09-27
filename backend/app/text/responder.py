"""The text medium. Stateless: every decision is made from the log and the user's state.

- A message, a typing change, or a call/Gmail outcome: schedule a ReplyDue check.
- ReplyDue: if nothing is waiting, drop it. If it is too early (the user is still going),
  check again later. If Jev thinks the user is mid-thought and there is time, check again
  shortly. Otherwise record ReplyStarted and start the reply in the background.

Duplicate checks are harmless: once ReplyStarted is recorded, nothing is waiting.
"""

from __future__ import annotations

from app.agent.context import last_lines
from app.events.decision import Decision
from app.events.event import Event
from app.events.payload import Channel, Origin
from app.jev import Jev
from app.pipeline import Context
from app.text.events import ReplyDue, ReplyStarted, UserMessage
from app.text.reply import Replier
from app.text.timing import delay, waiting
from app.users.user import User

FINISHED_QUESTION = (
    "Has the user finished what they wanted to say, so that now is a good moment to reply?"
)
# Hold only when Jev is confident the user is mid-thought. Measured 2026-09-26: fragments
# ("so basically", "i was thinking and", "hold on") score 0.10–0.15; "hey" and "ok thanks!"
# 0.35–0.50; complete asks ("call me Sam", "what can you do?") 0.80–0.91.
MID_THOUGHT_BELOW = 0.25
FINISHED_ABOVE = 0.8  # clearly complete: answer without waiting out the quiet window
EARLY_LOOK = 0.4  # with Jev: first check this soon, to answer finished texts early
UNFINISHED_EXTEND = 4.0  # Jev may hold off this long past the last message


class TextResponder:
    def __init__(self, replier: Replier, *, jev: Jev | None = None):
        self._replier = replier
        self._jev = jev

    async def handle(self, event: Event, user: User, ctx: Context) -> Decision | None:
        if not isinstance(event.payload, ReplyDue):
            return self._schedule(event, user, ctx)
        return await self._reply_due(event, user, ctx)

    def _schedule(self, event: Event, user: User, ctx: Context) -> Decision:
        pending = waiting(ctx.recent)
        if not pending:
            return Decision(trigger_kind=event.kind, verb="ignore", note="nothing waiting")
        seconds = delay(pending, user.typing_since, ctx.pipeline.now())
        if self._may_answer_early(pending, user):
            seconds = min(seconds, EARLY_LOOK)
        ctx.later(seconds, Origin.SYSTEM, Channel.TEXT, ReplyDue())
        return Decision(trigger_kind=event.kind, verb="schedule", note=f"check in {seconds:.1f}s")

    async def _reply_due(self, event: Event, user: User, ctx: Context) -> Decision | None:
        pending = waiting(ctx.recent)
        if not pending:
            return None  # an earlier check already started the reply
        now = ctx.pipeline.now()
        seconds = delay(pending, user.typing_since, now)
        last = pending[-1]
        since_last = (now - last.ts).total_seconds()
        if seconds > 0.05 and self._jev is not None and self._may_answer_early(pending, user):
            # Inside the quiet window: answer now only if Jev is sure they've finished.
            finished = await self._jev.yes_probability(FINISHED_QUESTION, _state(ctx))
            if finished is None or finished < FINISHED_ABOVE:
                ctx.later(seconds, Origin.SYSTEM, Channel.TEXT, ReplyDue())
                return Decision(
                    trigger_kind=event.kind, verb="wait", by="jev", note=f"{seconds:.1f}s more"
                )
            return await self._start(event, ctx, last, why=f"finished ({finished:.2f})")
        if seconds > 0.05:
            ctx.later(seconds, Origin.SYSTEM, Channel.TEXT, ReplyDue())
            return Decision(trigger_kind=event.kind, verb="wait", note=f"{seconds:.1f}s more")
        if (
            self._jev is not None
            and isinstance(last.payload, UserMessage)
            and since_last < UNFINISHED_EXTEND
        ):
            finished = await self._jev.yes_probability(FINISHED_QUESTION, _state(ctx))
            if finished is not None and finished < MID_THOUGHT_BELOW:
                ctx.later(1.0, Origin.SYSTEM, Channel.TEXT, ReplyDue())
                return Decision(
                    trigger_kind=event.kind,
                    verb="wait",
                    by="jev",
                    confidence=1 - finished,
                    note="user seems mid-thought",
                )
        return await self._start(event, ctx, last, why=f"through event {last.seq}")

    async def _start(self, event: Event, ctx: Context, last: Event, *, why: str) -> Decision:
        await ctx.record(Origin.TEXT_AGENT, Channel.TEXT, ReplyStarted(through_seq=last.seq))
        ctx.pipeline.spawn(self._replier.reply(ctx.phone, last.seq))
        return Decision(trigger_kind=event.kind, verb="reply", note=why)

    def _may_answer_early(self, pending: list[Event], user: User) -> bool:
        """A single typed message, nobody typing, and Jev to ask."""
        return (
            self._jev is not None
            and user.typing_since is None
            and isinstance(pending[-1].payload, UserMessage)
        )


def _state(ctx: Context) -> dict[str, object]:
    return {"channel": "text messages", "conversation": last_lines(ctx.recent, ctx.tz)}
