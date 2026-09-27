from __future__ import annotations

import asyncio
from datetime import timedelta

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agent.agent import agent
from app.agent.deps import Deps
from app.database import SessionFactory
from app.events.payload import Channel, Origin
from app.jobs.events import JobAsked, JobEnded, JobStarted, JobTold
from app.jobs.models import JobRow
from app.jobs.runner import Jobs, lines
from app.main import App
from app.pipeline import Pipeline
from app.text.events import UserMessage
from app.users.user import Medium
from app.voice.responder import VoiceResponder
from tests.conftest import PHONE, CapturingMessenger, FakeClock, FakeTimers, reply_hi, settle


def done(summary: str = "found it") -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart("final_result", {"ok": True, "summary": summary})])


def asks(question: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart("ask_user", {"question": question})])


def jobs_with(
    pipeline: Pipeline, db: SessionFactory, timers: FakeTimers, fn: FunctionModel
) -> Jobs:
    return Jobs(db, pipeline, model=fn, timers=timers, web_search=False)


def kinds(events: list[object]) -> list[str]:
    return [e.kind for e in events]  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType]


async def row(db: SessionFactory, job: str) -> JobRow:
    async with db() as s:
        found = await s.get(JobRow, job)
    assert found is not None
    return found


async def test_a_job_runs_and_reports_back(
    pipeline: Pipeline, db: SessionFactory, timers: FakeTimers
) -> None:
    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return done("Kin Khao has a 7pm table Saturday")

    jobs = jobs_with(pipeline, db, timers, FunctionModel(fn))
    job = await jobs.start(PHONE, "find a thai place for saturday")
    await settle(pipeline)

    events = await pipeline.history(PHONE)
    started = [e.payload for e in events if isinstance(e.payload, JobStarted)]
    ended = [e.payload for e in events if isinstance(e.payload, JobEnded)]
    assert started == [JobStarted(job=job, goal="find a thai place for saturday")]
    assert ended == [JobEnded(job=job, outcome="done", text="Kin Khao has a 7pm table Saturday")]
    assert ended[0].should_route()
    assert (await row(db, job)).status == "done"
    assert await jobs.open(PHONE) == []


async def test_a_question_waits_for_their_answer_and_the_job_resumes(
    pipeline: Pipeline, db: SessionFactory, timers: FakeTimers
) -> None:
    seen: list[str] = []

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        returns = [
            p.content
            for m in messages
            if isinstance(m, ModelRequest)
            for p in m.parts
            if isinstance(p, ToolReturnPart) and p.tool_name == "ask_user"
        ]
        if not returns:
            return asks("7pm or 8pm?")
        seen.append(str(returns[-1]))
        return done(f"booked for {returns[-1]}")

    jobs = jobs_with(pipeline, db, timers, FunctionModel(fn))
    job = await jobs.start(PHONE, "dinner saturday")
    await settle(pipeline)

    waiting = await row(db, job)
    assert (waiting.status, waiting.question) == ("waiting", "7pm or 8pm?")
    assert lines([waiting]) == [
        f"Background task {job} (dinner saturday) is waiting on their answer: 7pm or 8pm? "
        "Ask them when it fits, and pass the answer on (tell_job)."
    ]
    asked = [e.payload for e in await pipeline.history(PHONE) if isinstance(e.payload, JobAsked)]
    assert asked == [JobAsked(job=job, question="7pm or 8pm?")]

    assert await jobs.tell(PHONE, job, "8pm") == f"passed on to background task {job}"
    await settle(pipeline)

    assert seen == ["8pm"]
    assert (await row(db, job)).status == "done"
    events = await pipeline.history(PHONE)
    assert JobTold(job=job, text="8pm") in [e.payload for e in events]
    assert [e.payload for e in events if isinstance(e.payload, JobEnded)] == [
        JobEnded(job=job, outcome="done", text="booked for 8pm")
    ]


async def test_something_said_while_it_runs_reaches_the_running_job(
    pipeline: Pipeline, db: SessionFactory, timers: FakeTimers
) -> None:
    go = asyncio.Event()
    prompts: list[str] = []

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompts.extend(
            str(p.content)
            for m in messages
            if isinstance(m, ModelRequest)
            for p in m.parts
            if isinstance(p, UserPromptPart)
        )
        if len(messages) == 1:
            await go.wait()  # still thinking when they change the plan
            return ModelResponse(parts=[TextPart("looking")])
        return done("ok")

    jobs = jobs_with(pipeline, db, timers, FunctionModel(fn))
    job = await jobs.start(PHONE, "dinner saturday at 7")
    for _ in range(20):
        await asyncio.sleep(0)
    await jobs.tell(PHONE, job, "make it 8 instead")
    go.set()
    await settle(pipeline)

    assert "They said: make it 8 instead" in prompts
    assert (await row(db, job)).status == "done"


async def test_cancel_stops_it_quietly(
    pipeline: Pipeline, db: SessionFactory, timers: FakeTimers
) -> None:
    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return asks("which dentist?")

    jobs = jobs_with(pipeline, db, timers, FunctionModel(fn))
    job = await jobs.start(PHONE, "book a cleaning")
    await settle(pipeline)
    assert await jobs.cancel(PHONE, job)
    assert not await jobs.cancel(PHONE, job)  # already over

    ended = [e.payload for e in await pipeline.history(PHONE) if isinstance(e.payload, JobEnded)]
    assert ended == [JobEnded(job=job, outcome="cancelled")]
    assert not ended[0].should_route()
    assert await jobs.tell(PHONE, job, "the one downtown") == (
        f"background task {job} already cancelled; start a new one if needed"
    )


async def test_an_unanswered_question_is_dropped_after_a_day(
    pipeline: Pipeline, db: SessionFactory, timers: FakeTimers
) -> None:
    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return asks("which dentist?")

    jobs = jobs_with(pipeline, db, timers, FunctionModel(fn))
    job = await jobs.start(PHONE, "book a cleaning")
    await settle(pipeline)
    while timers.pending:  # replies to the question first, then the day runs out
        timers.fire_next()
        await settle(pipeline)
    assert (await row(db, job)).status == "cancelled"


async def test_a_failing_job_says_so(
    pipeline: Pipeline, db: SessionFactory, timers: FakeTimers
) -> None:
    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("boom")

    jobs = jobs_with(pipeline, db, timers, FunctionModel(fn))
    job = await jobs.start(PHONE, "anything")
    await settle(pipeline)
    ended = [e.payload for e in await pipeline.history(PHONE) if isinstance(e.payload, JobEnded)]
    assert [(e.job, e.outcome) for e in ended] == [(job, "failed")]


async def test_a_restart_picks_running_jobs_back_up(
    pipeline: Pipeline, db: SessionFactory, timers: FakeTimers
) -> None:
    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return done("found it")

    async with db() as s, s.begin():
        s.add(JobRow(id="abc123", phone=PHONE, goal="find a plumber", created_at=pipeline.now()))
    await pipeline.user(PHONE)
    jobs = jobs_with(pipeline, db, timers, FunctionModel(fn))
    await jobs.resume()
    await settle(pipeline)
    assert (await row(db, "abc123")).status == "done"


async def test_job_tools_are_there_by_text_and_for_the_back_office_but_not_the_voice(
    app: App,
) -> None:
    pipeline = app.pipeline

    async def tools_for(deps: Deps) -> list[str]:
        seen: list[str] = []

        async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            seen.extend(t.name for t in info.function_tools)
            return ModelResponse(parts=[TextPart("ok")])

        with agent.override(model=FunctionModel(fn)):
            await agent.run("go", deps=deps, output_type=str)
        return seen

    user = await pipeline.user(PHONE)  # mid-onboarding: nothing collected yet
    assert {"start_job", "tell_job", "cancel_job"} <= set(
        await tools_for(app.env.deps(user, Medium.TEXT))
    )
    back_office = await tools_for(app.env.deps(user, Medium.VOICE, back_office=True))
    assert {"start_job", "tell_job"} <= set(back_office)
    voice = await tools_for(app.env.deps(user, Medium.VOICE))
    assert not {"start_job", "tell_job"} & set(voice)


async def test_a_job_ending_gets_a_text_reply(
    app: App, db: SessionFactory, timers: FakeTimers, messenger: CapturingMessenger
) -> None:
    pipeline = app.pipeline

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return done("Dr. Lee has Tuesday 10am")

    jobs = jobs_with(pipeline, db, timers, FunctionModel(fn))
    await jobs.start(PHONE, "find a dentist")
    await settle(pipeline)
    timers.fire_next()  # no one is typing a job's result, so the reply is due straight away
    await settle(pipeline)
    assert messenger.sent == ["hi"]


async def test_a_job_question_reaches_the_voice_on_a_call(
    app: App, db: SessionFactory, timers: FakeTimers
) -> None:
    from tests.test_voice import on_a_call

    pipeline, _, session = await on_a_call(app, VoiceResponder())

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return asks("Tuesday 10am or Thursday 3pm?")

    jobs = jobs_with(pipeline, db, timers, FunctionModel(fn))
    await jobs.start(PHONE, "book a dentist cleaning")
    await settle(pipeline)
    spoken = [text for text, respond in session.sent if respond]
    assert len(spoken) == 1 and "Tuesday 10am or Thursday 3pm?" in spoken[0]


def test_citation_markers_are_stripped() -> None:
    from app.jobs.runner import _clean  # pyright: ignore[reportPrivateUsage]

    raw = (
        "(1) Sway Thai, 4.6/5. Sources: citeturn0search0turn0search1 "
        "(2) Thai Fresh. Sources: citeturn1search2"
    )
    assert _clean(raw) == "(1) Sway Thai, 4.6/5. (2) Thai Fresh."
    assert _clean(
        "from $16 (ordering page \ue200cite\ue202x\ue201). $17 (\ue200cite\ue202y\ue201)."
    ) == ("from $16 (ordering page). $17.")


def answers_in(messages: list[ModelMessage]) -> list[str]:
    """What ask_user's calls returned: the answers the job got."""
    return [
        str(p.content)
        for m in messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart) and p.tool_name == "ask_user"
    ]


async def endings(pipeline: Pipeline) -> list[JobEnded]:
    return [e.payload for e in await pipeline.history(PHONE) if isinstance(e.payload, JobEnded)]


async def test_a_restart_after_an_answer_resumes_with_that_answer(
    pipeline: Pipeline, db: SessionFactory, timers: FakeTimers
) -> None:
    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        got = answers_in(messages)
        return done(f"booked for {got[-1]}") if got else asks("7 or 8?")

    jobs = jobs_with(pipeline, db, timers, FunctionModel(fn))
    job = await jobs.start(PHONE, "dinner")
    await settle(pipeline)
    blocked, started = asyncio.Event(), asyncio.Event()

    async def hang(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        started.set()
        await blocked.wait()  # the process dies during the resumed run
        raise AssertionError("never reached")

    dying = jobs_with(pipeline, db, timers, FunctionModel(hang))
    await dying.tell(PHONE, job, "8")
    await started.wait()  # the resumed run is under way
    for task in list(dying._tasks.values()):  # pyright: ignore[reportPrivateUsage]
        task.cancel()
    await settle(pipeline)

    restarted = jobs_with(pipeline, db, timers, FunctionModel(fn))
    await restarted.resume()
    await settle(pipeline)
    assert await endings(pipeline) == [JobEnded(job=job, outcome="done", text="booked for 8")]


async def test_two_answers_at_once_resume_the_job_once(
    pipeline: Pipeline, db: SessionFactory, timers: FakeTimers
) -> None:
    runs: list[list[str]] = []

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        got = answers_in(messages)
        if not got:
            return asks("7 or 8?")
        runs.append(got)
        return done("ok")

    jobs = jobs_with(pipeline, db, timers, FunctionModel(fn))
    job = await jobs.start(PHONE, "dinner")
    await settle(pipeline)
    await asyncio.gather(jobs.tell(PHONE, job, "8"), jobs.tell(PHONE, job, "8pm please"))
    await settle(pipeline)

    assert all(len(got) == 1 for got in runs)  # one answer to the question, never two runs of it
    assert [e.outcome for e in await endings(pipeline)] == ["done"]


async def test_a_cancel_mid_run_is_the_only_ending(
    pipeline: Pipeline, db: SessionFactory, timers: FakeTimers
) -> None:
    release = asyncio.Event()

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await release.wait()
        return done("found it")

    jobs = jobs_with(pipeline, db, timers, FunctionModel(fn))
    job = await jobs.start(PHONE, "anything")
    await asyncio.sleep(0)
    assert await jobs.cancel(PHONE, job)
    release.set()
    await settle(pipeline)
    assert await endings(pipeline) == [JobEnded(job=job, outcome="cancelled")]
    assert not await jobs.cancel(PHONE, job)


async def test_something_said_as_it_finishes_gets_one_more_run(
    pipeline: Pipeline, db: SessionFactory, timers: FakeTimers
) -> None:
    prompts: list[str] = []
    jobs: Jobs

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        said = [
            str(p.content)
            for m in messages
            if isinstance(m, ModelRequest)
            for p in m.parts
            if isinstance(p, UserPromptPart)
        ]
        prompts.extend(said)
        if not any("They said" in s for s in said):
            jobs._inbox.setdefault(job, []).append("actually 8")  # pyright: ignore[reportPrivateUsage]
            return done("booked 7")
        return done("booked 8")

    jobs = jobs_with(pipeline, db, timers, FunctionModel(fn))
    job = await jobs.start(PHONE, "dinner at 7")
    await settle(pipeline)
    assert "They said: actually 8" in prompts
    assert await endings(pipeline) == [JobEnded(job=job, outcome="done", text="booked 8")]


async def test_a_reset_mid_run_ends_quietly(
    pipeline: Pipeline, db: SessionFactory, timers: FakeTimers
) -> None:
    release = asyncio.Event()

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await release.wait()
        return done("found it")

    jobs = jobs_with(pipeline, db, timers, FunctionModel(fn))
    await jobs.start(PHONE, "anything")
    await asyncio.sleep(0)
    await pipeline.reset(PHONE)
    release.set()
    await settle(pipeline)
    assert await pipeline.history(PHONE) == []


async def test_a_restart_keeps_the_rest_of_the_day_for_an_open_question(
    pipeline: Pipeline, db: SessionFactory, timers: FakeTimers, clock: FakeClock
) -> None:
    async with db() as s, s.begin():
        s.add(
            JobRow(
                id="abc123",
                phone=PHONE,
                goal="dinner",
                status="waiting",
                question="7 or 8?",
                waiting_on="call1",
                asked_at=clock() - timedelta(hours=23),
                created_at=clock(),
            )
        )
    jobs = jobs_with(pipeline, db, timers, FunctionModel(reply_hi))
    await jobs.resume()
    assert timers.pending == [3600.0]


async def test_a_job_that_hangs_never_holds_up_their_texts(
    app: App, db: SessionFactory, timers: FakeTimers, messenger: CapturingMessenger
) -> None:
    pipeline = app.pipeline
    never = asyncio.Event()

    async def hang(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await never.wait()
        raise AssertionError("never reached")

    jobs = jobs_with(pipeline, db, timers, FunctionModel(hang))
    await jobs.start(PHONE, "something slow")
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, UserMessage(text="hey"))
    timers.fire_next()
    await asyncio.wait_for(settle_except(pipeline, jobs), timeout=2)
    assert messenger.sent == ["hi"]
    await jobs.cancel(PHONE, (await jobs.open(PHONE))[0].id)


async def settle_except(pipeline: Pipeline, jobs: Jobs) -> None:
    """Wait for background work other than the jobs' runs (settle() would wait on the hang)."""
    runs = set(jobs._tasks.values())  # pyright: ignore[reportPrivateUsage]
    for _ in range(10):
        others = [t for t in pipeline._background if t not in runs and not t.done()]  # pyright: ignore[reportPrivateUsage]
        await asyncio.gather(*others)
        await asyncio.sleep(0)
