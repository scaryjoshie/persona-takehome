"""Background jobs: start one, pass it what they say, cancel it, and pick jobs back up after a
restart.

A job runs the job agent until it ends or asks them something. A question ends the run (a
deferred tool call); the job's messages are saved and it waits. Their answer, passed on with
`tell`, starts the next run from those messages. Something said while it runs goes straight
into the running conversation. Everything the chat agent should know is an event: started,
asked, told, ended. The questions and endings are routed, so they reach them by text or on a
call like anything else.

A question with no answer for ANSWER_WAIT is dropped quietly: the job is cancelled.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Sequence
from zoneinfo import ZoneInfo

from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.capabilities import WebSearch
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from pydantic_ai.models import Model
from pydantic_ai.run import AgentRun
from pydantic_ai.usage import UsageLimits
from sqlmodel import col, select

from app.database import SessionFactory
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
# Web search leaves citation markers in the text, in private-use characters: \ue200cite…\ue201
CITATION = re.compile("\ue200[^\ue201]*\ue201")
OPEN = ("running", "waiting")


class Jobs:
    def __init__(
        self,
        db: SessionFactory,
        pipeline: Pipeline,
        *,
        model: Model,
        timers: Timers,
        tz: ZoneInfo,
        google: Google | None = None,
        web_search: bool = True,
    ) -> None:
        self._db = db
        self._pipeline = pipeline
        self._model = model
        self._timers = timers
        self._tz = tz
        self._google = google
        self._web_search = web_search
        self._tasks: dict[str, asyncio.Future[object]] = {}
        self._runs: dict[str, AgentRun[JobDeps, Outcome | DeferredToolRequests]] = {}
        self._inbox: dict[str, list[str]] = {}  # said before its run was ready to take it

    async def start(self, phone: str, goal: str) -> str:
        job = uuid.uuid4().hex[:6]
        async with self._db() as s, s.begin():
            s.add(JobRow(id=job, phone=phone, goal=goal, created_at=self._pipeline.now()))
        await self._submit(phone, JobStarted(job=job, goal=goal))
        self._launch(job, phone, prompt=goal)
        return job

    async def tell(self, phone: str, job: str, text: str) -> str:
        """Their answer to its question, or anything else the job should know."""
        row = await self._row(job)
        if row is None or row.phone != phone:
            return f"there's no background task {job}"
        if row.status not in OPEN:
            return f"background task {job} already {row.status}; start a new one if needed"
        await self._submit(phone, JobTold(job=job, text=text))
        if row.status == "waiting":
            ids = (row.waiting_on or "").split(",")
            answers = {i: text if n == 0 else ONE_AT_A_TIME for n, i in enumerate(ids)}
            await self._set(job, status="running", waiting_on=None)
            self._launch(job, phone, answers=answers)
        elif job in self._runs:
            self._runs[job].enqueue(f"They said: {text}")
        else:
            self._inbox.setdefault(job, []).append(text)
        return f"passed on to background task {job}"

    async def cancel(self, phone: str, job: str, *, why: str = "") -> bool:
        row = await self._row(job)
        if row is None or row.phone != phone or row.status not in OPEN:
            return False
        task = self._tasks.pop(job, None)
        if task is not None:
            task.cancel()
        await self._end(phone, job, JobEnded(job=job, outcome="cancelled", text=why))
        return True

    async def open(self, phone: str) -> list[JobRow]:
        async with self._db() as s:
            query = select(JobRow).where(JobRow.phone == phone, col(JobRow.status).in_(OPEN))
            return list((await s.exec(query.order_by(col(JobRow.created_at)))).all())

    async def resume(self) -> None:
        """At startup: runs that were going when the process stopped go again from their
        last saved messages. Jobs waiting on an answer keep waiting."""
        async with self._db() as s:
            rows = (await s.exec(select(JobRow).where(JobRow.status == "running"))).all()
        for row in rows:
            fresh = row.messages == "[]"
            self._launch(row.id, row.phone, prompt=row.goal if fresh else "Carry on.")

    # ---- a run ------------------------------------------------------------------------

    def _launch(
        self,
        job: str,
        phone: str,
        *,
        prompt: str | None = None,
        answers: dict[str, str] | None = None,
    ) -> None:
        work = self._run(job, phone, prompt=prompt, answers=answers)
        task = self._pipeline.spawn(work)
        self._tasks[job] = task

        def forget(done: asyncio.Future[object]) -> None:
            if self._tasks.get(job) is done:
                del self._tasks[job]

        task.add_done_callback(forget)

    async def _run(
        self, job: str, phone: str, *, prompt: str | None, answers: dict[str, str] | None
    ) -> None:
        row = await self._row(job)
        assert row is not None
        history = ModelMessagesTypeAdapter.validate_json(row.messages)
        deps = JobDeps(self._pipeline, phone, job, self._tz, self._google)
        try:
            async with asyncio.timeout(RUN_SECONDS):
                async with job_agent.iter(
                    prompt,
                    message_history=history,
                    deferred_tool_results=DeferredToolResults(calls=answers) if answers else None,
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
            raise  # cancelled: cancel() records it
        except Exception as exc:
            log.exception("%s: background task %s failed", phone, job)
            failed = JobEnded(
                job=job, outcome="failed", text=f"it hit an error ({type(exc).__name__})"
            )
            await self._end(phone, job, failed)
            return
        finally:
            self._runs.pop(job, None)
        assert result is not None
        messages = result.all_messages()
        output = result.output
        if isinstance(output, DeferredToolRequests):
            await self._wait(phone, job, output, messages)
        else:
            outcome = "done" if output.ok else "failed"
            await self._end(
                phone,
                job,
                JobEnded(job=job, outcome=outcome, text=_clean(output.summary)),
                messages,
            )

    async def _wait(
        self, phone: str, job: str, asked: DeferredToolRequests, messages: list[ModelMessage]
    ) -> None:
        ids = [call.tool_call_id for call in asked.calls]
        question = str(asked.metadata.get(ids[0], {}).get("question", "")) or "(no question)"
        await self._set(
            job, status="waiting", waiting_on=",".join(ids), question=question, messages=messages
        )
        await self._submit(phone, JobAsked(job=job, question=question))

        def expire() -> None:
            self._pipeline.spawn(self._expire(phone, job, ",".join(ids)))

        self._timers.call_later(ANSWER_WAIT, expire)

    async def _expire(self, phone: str, job: str, waiting_on: str) -> None:
        row = await self._row(job)
        if row is not None and row.status == "waiting" and row.waiting_on == waiting_on:
            await self.cancel(phone, job, why="no answer for a day")

    async def _end(
        self, phone: str, job: str, ended: JobEnded, messages: Sequence[ModelMessage] = ()
    ) -> None:
        await self._set(job, status=ended.outcome, waiting_on=None, messages=messages or None)
        await self._submit(phone, ended)

    # ---- storage --------------------------------------------------------------------

    async def _row(self, job: str) -> JobRow | None:
        async with self._db() as s:
            return await s.get(JobRow, job)

    async def _set(
        self,
        job: str,
        *,
        status: str,
        waiting_on: str | None,
        question: str | None = None,
        messages: Sequence[ModelMessage] | None = None,
    ) -> None:
        async with self._db() as s, s.begin():
            row = await s.get(JobRow, job)
            assert row is not None
            row.status, row.waiting_on, row.question = status, waiting_on, question
            if messages:
                row.messages = ModelMessagesTypeAdapter.dump_json(list(messages)).decode()
            s.add(row)

    async def _submit(self, phone: str, payload: Payload) -> None:
        await self._pipeline.submit(phone, Origin.JOB, Channel.SYSTEM, payload)


def lines(rows: Sequence[JobRow]) -> list[str]:
    """Open jobs as facts for the chat agent: they stay in front of it until they end."""
    out: list[str] = []
    for row in rows:
        if row.status == "waiting":
            out.append(
                f"Background task {row.id} ({row.goal}) is waiting on their answer: {row.question} "
                "Ask them when it fits, and pass the answer on (tell_job)."
            )
        else:
            out.append(f"Background task {row.id} is working on: {row.goal}")
    return out


def _clean(text: str) -> str:
    """Without web search's citation markers, and the "Sources:" they leave dangling."""
    text = CITATION.sub("", text)
    text = re.sub(r"\s*\(\s*\)|\s+(?=\))", "", text)  # "(page )" and "()" they sat in
    return re.sub(r"\s*Sources:\s*(?=\(|$|\n)", " ", text).strip()
