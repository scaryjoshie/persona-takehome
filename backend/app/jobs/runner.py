"""Background jobs: start one, pass it what they say, cancel it, and pick jobs back up after a
restart.

A job runs the job agent until it ends or asks them something. A question ends the run (a
deferred tool call); the job's messages are saved and it waits. Their answer, passed on with
`tell`, starts the next run from those messages. Something said while it runs goes straight
into the running conversation. Everything the chat agent should know is an event: started,
asked, told, ended. The questions and endings are routed, so they reach them by text or on a
call like anything else.

Every status change is one conditional update (`_move`: only from the status it expects), so
two answers at once resume a job once, a job ends once, and a cancelled job stays cancelled,
without locks. Whoever's update took records the event.

A question with no answer for ANSWER_WAIT is dropped quietly: the job is cancelled.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.capabilities import WebSearch
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from pydantic_ai.models import Model
from pydantic_ai.run import AgentRun
from pydantic_ai.tools import DeferredToolApprovalResult
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.usage import UsageLimits
from sqlalchemy import update
from sqlmodel import col, select

from app.database import SessionFactory, aware
from app.events.payload import Channel, Origin, Payload
from app.google.accounts import Google
from app.jobs.agent import JobDeps, Outcome, job_agent
from app.jobs.events import JobAsked, JobEnded, JobStarted, JobTold
from app.jobs.models import JobRow
from app.pipeline import Pipeline
from app.timers import Timers

log = logging.getLogger(__name__)

STEPS = 30  # model requests per run
RUN_SECONDS = 300.0
ANSWER_WAIT = 24 * 3600.0
ONE_AT_A_TIME = "one question at a time: ask this again after they answer the first"
# Web search leaves citation markers in the text, in private-use characters: cite…
CITATION = re.compile("[^]*")
OPEN = ("running", "waiting")
ONE_THING = "Ask them one thing at a time; ask this again after they've answered."
# Waits on a link it texted (the secure form, an app's sign-in page).
LINKS = {"secret": "secure link", "connect": "sign-in link"}


@dataclass(frozen=True)
class Extras:
    """More for one run, from outside the jobs package (integrations): tools and what to know."""

    toolsets: Sequence[AbstractToolset[JobDeps]] = ()
    instructions: str = ""


ExtrasFor = Callable[[JobDeps], Awaitable[Extras]]
# What resumes a paused job, as JSON: {"calls": {id: text}, "approvals": {id: true | "why not"}}
Reply = dict[str, dict[str, Any]]


@dataclass(frozen=True)
class Pending:
    """What a paused job is waiting on: the one thing it asked (`ask`), what kind of wait it
    is, and every open call, so the reply answers them all (the rest get "one at a time")."""

    ask: str
    kind: str  # "answer", "approval" (their yes or no), or a link: "secret", "connect"
    calls: tuple[str, ...] = ()
    approvals: tuple[str, ...] = ()

    @classmethod
    def of(cls, asked: DeferredToolRequests) -> tuple[Pending, str]:
        calls = tuple(c.tool_call_id for c in asked.calls)
        approvals = tuple(c.tool_call_id for c in asked.approvals)
        first = (asked.approvals or asked.calls)[0]
        meta = asked.metadata.get(first.tool_call_id, {})
        if approvals:
            kind = "approval"
        else:
            kind = str(meta["kind"]) if meta.get("kind") in LINKS else "answer"
        question = str(meta.get("question", "")) or f"ok to go ahead with {first.tool_name}?"
        return cls(first.tool_call_id, kind, calls, approvals), question

    @classmethod
    def parse(cls, stored: str | None) -> Pending:
        if stored and stored.startswith("{"):
            data = json.loads(stored)
            return cls(data["ask"], data["kind"], tuple(data["calls"]), tuple(data["approvals"]))
        ids = tuple((stored or "").split(","))  # written before approvals existed
        return cls(ids[0], "answer", ids)

    def dump(self) -> str:
        return json.dumps(
            {"ask": self.ask, "kind": self.kind, "calls": self.calls, "approvals": self.approvals}
        )

    def reply(self, text: str, approve: bool | None) -> Reply:
        calls: dict[str, Any] = {i: ONE_AT_A_TIME for i in self.calls}
        approvals: dict[str, Any] = {i: ONE_THING for i in self.approvals}
        if self.kind == "approval":
            approvals[self.ask] = True if approve else f"They said no: {text}"
        else:
            calls[self.ask] = text
        return {"calls": calls, "approvals": approvals}


def results(reply: Reply) -> DeferredToolResults:
    if "calls" not in reply and "approvals" not in reply:  # written before approvals existed
        return DeferredToolResults(calls=dict(reply))
    approvals: dict[str, bool | DeferredToolApprovalResult] = {
        i: True if v is True else ToolDenied(str(v)) for i, v in reply.get("approvals", {}).items()
    }
    return DeferredToolResults(calls=dict(reply.get("calls", {})), approvals=approvals)


class Jobs:
    def __init__(
        self,
        db: SessionFactory,
        pipeline: Pipeline,
        *,
        model: Model,
        timers: Timers,
        google: Google | None = None,
        web_search: bool = True,
        extras: ExtrasFor | None = None,  # more tools and instructions per run (integrations)
    ) -> None:
        self._db = db
        self._pipeline = pipeline
        self._model = model
        self._timers = timers
        self._google = google
        self._web_search = web_search
        self._extras = extras
        self._tasks: dict[str, asyncio.Future[object]] = {}
        self._runs: dict[str, AgentRun[JobDeps, Outcome | DeferredToolRequests]] = {}
        self._inbox: dict[str, list[str]] = {}  # said while no run could take it

    async def start(self, phone: str, goal: str) -> str:
        job = uuid.uuid4().hex[:6]
        async with self._db() as s, s.begin():
            s.add(JobRow(id=job, phone=phone, goal=goal, created_at=self._pipeline.now()))
        await self._submit(phone, JobStarted(job=job, goal=goal))
        self._launch(job, phone, prompt=goal)
        return job

    async def tell(self, phone: str, job: str, text: str, *, approve: bool | None = None) -> str:
        """Their answer to its question, their yes or no to something it wants to do, or
        anything else the job should know."""
        row = await self._row(job)
        if row is None or row.phone != phone:
            return f"there's no background task {job}"
        if row.status not in OPEN:
            return f"background task {job} already {row.status}; start a new one if needed"
        pending = Pending.parse(row.waiting_on) if row.status == "waiting" else None
        if pending is not None and pending.kind == "approval" and approve is None:
            return (
                f"background task {job} asked for a yes or no ({row.question}); pass what "
                "they said with approve set"
            )
        await self._submit(phone, JobTold(job=job, text=text))
        # Only the form answers a secure link (saying they pasted it proves nothing); a sign-in
        # can be checked, so their words resume it ("i missed it", "done").
        if pending is not None and pending.kind != "secret":
            reply = pending.reply(text, approve)
            # Saved with the move, so a restart before the next run saves its messages resumes
            # with the reply rather than with a question nobody answered.
            if await self._move(
                job, ("waiting",), status="running", waiting_on=None, answer=json.dumps(reply)
            ):
                self._launch(job, phone, reply=reply)
                return f"passed on to background task {job}"
        if not self._slip_in(job, text):
            # Between runs, waiting on the secure form, or the run just ended: the next run
            # picks it up, or the ending one runs once more.
            self._inbox.setdefault(job, []).append(text)
        return f"passed on to background task {job}"

    async def resolve(self, job: str, question: str, result: str) -> bool:
        """Something other than their words answered it (they saved a secret in the form, or
        came back from signing in)."""
        row = await self._row(job)
        if row is None or row.status != "waiting":
            return False
        pending = Pending.parse(row.waiting_on)
        if pending.ask != question:
            return False
        reply = pending.reply(result, None)
        if not await self._move(
            job, ("waiting",), status="running", waiting_on=None, answer=json.dumps(reply)
        ):
            return False
        self._launch(job, row.phone, reply=reply)
        return True

    def _slip_in(self, job: str, text: str) -> bool:
        """Into the running conversation, if there is one still taking messages."""
        run = self._runs.get(job)
        if run is None:
            return False
        try:
            run.enqueue(f"They said: {text}")
        except UserError:  # it ended a moment ago
            return False
        return True

    async def cancel(self, phone: str, job: str, *, why: str = "") -> bool:
        row = await self._row(job)
        if row is None or row.phone != phone:
            return False
        ended = JobEnded(job=job, outcome="cancelled", text=why)
        if not await self._end(phone, job, ended):
            return False  # already over
        task = self._tasks.pop(job, None)
        if task is not None:
            task.cancel()
        return True

    async def open(self, phone: str) -> list[JobRow]:
        async with self._db() as s:
            query = select(JobRow).where(JobRow.phone == phone, col(JobRow.status).in_(OPEN))
            return list((await s.exec(query.order_by(col(JobRow.created_at)))).all())

    async def resume(self) -> None:
        """At startup: runs that were going when the process stopped go again from their last
        saved messages (with the answer that resumed them, if one did). Jobs waiting on an
        answer keep waiting, with what's left of their day."""
        async with self._db() as s:
            rows = (await s.exec(select(JobRow).where(col(JobRow.status).in_(OPEN)))).all()
        for row in rows:
            if row.status == "waiting":
                asked = aware(row.asked_at) if row.asked_at else self._pipeline.now()
                left = ANSWER_WAIT - (self._pipeline.now() - asked).total_seconds()
                self._expire_later(row.phone, row.id, row.waiting_on or "", max(left, 0.0))
            elif row.answer:
                self._launch(row.id, row.phone, reply=json.loads(row.answer))
            else:
                fresh = row.messages == "[]"
                self._launch(row.id, row.phone, prompt=row.goal if fresh else "Carry on.")

    # ---- a run ------------------------------------------------------------------------

    def _launch(
        self,
        job: str,
        phone: str,
        *,
        prompt: str | None = None,
        reply: Reply | None = None,
    ) -> None:
        task = self._pipeline.spawn(self._run(job, phone, prompt=prompt, reply=reply))
        self._tasks[job] = task

        def forget(done: asyncio.Future[object]) -> None:
            if self._tasks.get(job) is done:
                del self._tasks[job]

        task.add_done_callback(forget)

    async def _run(self, job: str, phone: str, *, prompt: str | None, reply: Reply | None) -> None:
        row = await self._row(job)
        if row is None:
            return  # forgotten (a reset)
        history = ModelMessagesTypeAdapter.validate_json(row.messages)
        tz = (await self._pipeline.user(phone)).slots.zone()  # theirs, as the chat agent has it
        deps = JobDeps(self._pipeline, phone, job, tz, self._google)
        extras = await self._extras(deps) if self._extras else Extras()
        try:
            async with asyncio.timeout(RUN_SECONDS):
                async with job_agent.iter(
                    prompt,
                    message_history=history,
                    deferred_tool_results=results(reply) if reply else None,
                    toolsets=list(extras.toolsets) or None,
                    instructions=extras.instructions or None,
                    deps=deps,
                    model=self._model,
                    usage_limits=UsageLimits(request_limit=STEPS),
                    capabilities=[WebSearch()] if self._web_search else None,
                ) as run:
                    self._runs[job] = run
                    for text in self._inbox.pop(job, []):
                        run.enqueue(f"They said: {text}")
                    async for _ in run:
                        pass
                    result = run.result
        except asyncio.CancelledError:
            raise  # cancelled: cancel() recorded it
        except Exception as exc:
            log.exception("%s: background task %s failed", phone, job)
            error = f"it hit an error ({type(exc).__name__})"
            await self._end(phone, job, JobEnded(job=job, outcome="failed", text=error))
            return
        finally:
            self._runs.pop(job, None)
        assert result is not None
        messages = result.all_messages()
        output = result.output
        if isinstance(output, DeferredToolRequests):
            await self._wait(phone, job, output, messages)
            return
        late = self._inbox.pop(job, [])
        if late:  # they said something as it finished: take it into account first
            await self._move(job, ("running",), messages=messages, answer=None)
            self._launch(job, phone, prompt="\n".join(f"They said: {t}" for t in late))
            return
        outcome = "done" if output.ok else "failed"
        ended = JobEnded(job=job, outcome=outcome, text=_clean(output.summary))
        await self._end(phone, job, ended, messages)

    async def _wait(
        self, phone: str, job: str, asked: DeferredToolRequests, messages: list[ModelMessage]
    ) -> None:
        pending, question = Pending.of(asked)
        ids = pending.dump()
        waiting = await self._move(
            job,
            ("running",),
            status="waiting",
            waiting_on=ids,
            question=question,
            asked_at=self._pipeline.now(),
            messages=messages,
            answer=None,
        )
        if not waiting:
            return  # cancelled while it ran
        await self._submit(phone, JobAsked(job=job, question=question))
        self._expire_later(phone, job, ids, ANSWER_WAIT)

    def _expire_later(self, phone: str, job: str, waiting_on: str, seconds: float) -> None:
        def expire() -> None:
            self._pipeline.spawn(self._expire(phone, job, waiting_on))

        self._timers.call_later(seconds, expire)

    async def _expire(self, phone: str, job: str, waiting_on: str) -> None:
        row = await self._row(job)
        if row is not None and row.status == "waiting" and row.waiting_on == waiting_on:
            await self.cancel(phone, job, why="no answer for a day")

    async def _end(
        self, phone: str, job: str, ended: JobEnded, messages: Sequence[ModelMessage] = ()
    ) -> bool:
        """End it once: only the caller whose update took records the ending."""
        values: dict[str, Any] = {"status": ended.outcome, "waiting_on": None, "answer": None}
        if messages:
            values["messages"] = messages
        if not await self._move(job, OPEN, **values):
            return False
        await self._submit(phone, ended)
        return True

    # ---- storage --------------------------------------------------------------------

    async def _row(self, job: str) -> JobRow | None:
        async with self._db() as s:
            return await s.get(JobRow, job)

    async def _move(self, job: str, from_: tuple[str, ...], **values: Any) -> bool:
        """Update the job only if its status is one of `from_`. True if it did."""
        if "messages" in values:
            values["messages"] = ModelMessagesTypeAdapter.dump_json(
                list(values["messages"])
            ).decode()
        statement = (
            update(JobRow)
            .where(col(JobRow.id) == job, col(JobRow.status).in_(from_))
            .values(**values)
        )
        async with self._db() as s, s.begin():
            result = await s.exec(statement)  # pyright: ignore[reportArgumentType, reportCallIssue]
        return result.rowcount == 1  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]

    async def _submit(self, phone: str, payload: Payload) -> None:
        await self._pipeline.submit(phone, Origin.JOB, Channel.SYSTEM, payload)


def lines(rows: Sequence[JobRow], *, speaking: bool = False) -> list[str]:
    """Open jobs as facts: they stay in front of the agent until they end. `speaking`: for the
    voice, which never sees ids or tool names (its answers reach the job on their own)."""
    out: list[str] = []
    for row in rows:
        name = f"a background task ({row.goal})" if speaking else f"Background task {row.id}"
        kind = Pending.parse(row.waiting_on).kind if row.status == "waiting" else None
        if kind in LINKS:
            out.append(f"{name} is waiting for them to use the {LINKS[kind]} it texted.")
        elif kind is not None and speaking:
            wants = "their yes or no" if kind == "approval" else "their answer"
            out.append(f"{name} needs {wants}: {row.question} Ask them when it fits.")
        elif kind == "approval":
            out.append(
                f"{name} ({row.goal}) is waiting on their yes or no: {row.question} Ask them "
                "when it fits, and pass on what they said (tell_job, with approve set)."
            )
        elif kind == "answer":
            out.append(
                f"{name} ({row.goal}) is waiting on their answer: {row.question} "
                "Ask them when it fits, and pass the answer on (tell_job)."
            )
        else:
            out.append(
                f"{name} is working on: {row.goal}" if not speaking else f"{name} is underway."
            )
    return out


def _clean(text: str) -> str:
    """Without web search's citation markers, and the "Sources:" they leave dangling."""
    text = CITATION.sub("", text)
    text = re.sub(r"\s*\(\s*\)|\s+(?=\))", "", text)  # "(page )" and "()" they sat in
    return re.sub(r"\s*Sources:\s*(?=\(|$|\n)", " ", text).strip()
