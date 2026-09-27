"""Integrations: the HTTP executor's guards, storage and secrets, and a job that sets one up
through the secure link and then posts through it only after their yes."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.database import SessionFactory
from app.integrations import http, templates
from app.integrations.integration import ApiKey, Endpoint, HttpApi, Integration, Param
from app.integrations.models import IntegrationRow
from app.jobs.events import JobAsked, JobEnded
from app.main import App, assemble
from app.pipeline import Pipeline
from tests.conftest import (
    PHONE,
    CapturingMessenger,
    FakeClock,
    FakeTimers,
    reply_hi,
    settle,
)

WEBHOOK = "https://hooks.slack.com/services/T000/B000/XXXXsecretXXXX"
SLACK = {
    "app": "slack",
    "auth": [{"kind": "api_key", "secret": "webhook_url", "about": "Slack incoming webhook URL"}],
    "api": {
        "base_url": "{secret:webhook_url}",
        "allowed_hosts": ["hooks.slack.com"],
        "endpoints": [
            {
                "name": "post_message",
                "about": "Post a message to their channel",
                "method": "POST",
                "params": [{"name": "text", "about": "the message"}],
                "effect": "act",
            }
        ],
    },
}


def slack_api() -> HttpApi:
    return Integration.model_validate(SLACK).api  # pyright: ignore[reportReturnType]


class Recorder:
    """A fake internet: records requests, answers 200 with a body echoing a secret back."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"ok": True, "you_sent_to": str(request.url)})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


# ---- the executor ----------------------------------------------------------------------


async def test_a_call_fills_in_secrets_and_never_returns_them() -> None:
    net = Recorder()
    api, endpoint = slack_api(), slack_api().endpoints[0]
    out = await http.call(net.client(), api, endpoint, {"text": "hi"}, {"webhook_url": WEBHOOK})
    assert str(net.requests[0].url) == WEBHOOK
    assert json.loads(net.requests[0].content) == {"text": "hi"}
    assert out.ok and "XXXXsecretXXXX" not in out.text and "[secret]" in out.text


@pytest.mark.parametrize(
    ("secret", "args", "why"),
    [
        ("https://evil.example.com/steal", {"text": "hi"}, "allowed hosts"),
        ("http://hooks.slack.com/services/x", {"text": "hi"}, "only https"),
        (WEBHOOK, {"text": "hi", "channel": "#all"}, "unknown arguments"),
        (WEBHOOK, {}, "missing arguments"),
    ],
)
async def test_a_call_is_refused_before_anything_is_sent(
    secret: str, args: dict[str, Any], why: str
) -> None:
    net = Recorder()
    with pytest.raises(http.Refused, match=why):
        await http.call(
            net.client(), slack_api(), slack_api().endpoints[0], args, {"webhook_url": secret}
        )
    assert net.requests == []


async def test_a_missing_secret_is_refused() -> None:
    with pytest.raises(http.Refused, match="isn't saved"):
        await http.call(
            Recorder().client(), slack_api(), slack_api().endpoints[0], {"text": "x"}, {}
        )


def test_an_agent_written_read_must_be_a_get_but_a_template_s_can_be_a_post() -> None:
    get = Endpoint(name="list", about="x", method="GET", effect="read")
    post = Endpoint(name="search", about="x", method="POST", effect="read")
    written = Integration(app="x")
    assert written.reads(get) and not written.reads(post)
    assert templates.build("notion").reads(post)


def test_a_template_needs_a_real_host_when_it_takes_one() -> None:
    canvas = templates.build("canvas", host="Canvas.School.edu")
    assert isinstance(canvas.api, HttpApi)
    assert canvas.api.base_url == "https://canvas.school.edu/api/v1"
    assert canvas.api.allowed_hosts == ["canvas.school.edu"]
    for bad in ("", "evil.com/steal", "https://x.edu", "localhost"):
        with pytest.raises(ValueError, match="needs their host"):
            templates.build("canvas", host=bad)


async def test_params_can_go_by_another_name_as_a_form_with_a_fixed_body_and_pages_come_back() -> (
    None
):
    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body: dict[str, Any] = {"items": [{"id": 1, "note": None, "tags": []}], "extra": ""}
        return httpx.Response(200, json=body, headers={"Link": '<https://a.io/p2>; rel="next"'})

    seen: list[httpx.Request] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    api = HttpApi(base_url="https://a.io", allowed_hosts=["a.io"])
    listing = Endpoint(
        name="list",
        about="x",
        params=[Param(name="codes", key="context_codes[]", where="query")],
        effect="read",
    )
    out = await http.call(client, api, listing, {"codes": "course_1"}, {})
    assert seen[0].url.params["context_codes[]"] == "course_1"
    assert '{"items": [{"id": 1}]}' in out.text and 'rel="next"' in out.text
    form = Endpoint(name="send", about="x", method="POST", params=[Param(name="to", where="form")])
    await http.call(client, api, form, {"to": "+1555"}, {})
    assert seen[1].content == b"to=%2B1555"
    graphql = Endpoint(
        name="issues",
        about="x",
        method="POST",
        body={"query": "{ issues { id } }"},
        params=[Param(name="variables")],
    )
    await http.call(client, api, graphql, {"variables": {"n": 5}}, {})
    assert json.loads(seen[2].content) == {"query": "{ issues { id } }", "variables": {"n": 5}}


# ---- storage --------------------------------------------------------------------------


def with_integrations(db: SessionFactory, clock: FakeClock, timers: FakeTimers, **kw: Any) -> App:
    return assemble(
        db=db,
        messenger=kw.pop("messenger", CapturingMessenger()),
        model=kw.pop("model", FunctionModel(reply_hi)),
        app_base_url="http://x",
        timers=timers,
        clock=clock,
        web_search=False,
        credentials_key=Fernet.generate_key().decode(),
        **kw,
    )


async def test_secrets_are_stored_encrypted_and_hosts_cannot_grow_once_ready(
    db: SessionFactory, clock: FakeClock, timers: FakeTimers
) -> None:
    store = with_integrations(db, clock, timers).env.integrations
    assert store is not None
    saved = await store.save(PHONE, Integration.model_validate(SLACK))
    assert await store.missing(PHONE, saved) == ["webhook_url"]
    await store.set_secret(PHONE, saved.id, "webhook_url", WEBHOOK)
    async with db() as s:
        row = await s.get(IntegrationRow, saved.id)
    assert row is not None and "XXXXsecretXXXX" not in row.secrets + json.dumps(row.data)
    assert await store.secrets(PHONE, saved.id) == {"webhook_url": WEBHOOK}

    ready = await store.save(PHONE, saved.model_copy(update={"status": "ready"}))
    assert ready.api is not None
    wider = ready.api.model_copy(update={"allowed_hosts": ["hooks.slack.com", "evil.example.com"]})
    with pytest.raises(ValueError, match="can't grow"):
        await store.save(PHONE, ready.model_copy(update={"api": wider}))


# ---- a job sets one up, then uses it ------------------------------------------------------


def returns(messages: list[ModelMessage], tool: str) -> list[str]:
    return [
        str(p.content)
        for m in messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart) and p.tool_name == tool
    ]


def call(tool: str, **args: Any) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool, args)])


def done(summary: str) -> ModelResponse:
    return call("final_result", ok=True, summary=summary)


async def ended(pipeline: Pipeline) -> list[JobEnded]:
    return [e.payload for e in await pipeline.history(PHONE) if isinstance(e.payload, JobEnded)]


async def asked(pipeline: Pipeline) -> list[str]:
    return [
        e.payload.question for e in await pipeline.history(PHONE) if isinstance(e.payload, JobAsked)
    ]


async def test_a_job_connects_slack_through_the_secure_link_then_posts_only_after_a_yes(
    db: SessionFactory, clock: FakeClock, timers: FakeTimers
) -> None:
    net = Recorder()
    messenger = CapturingMessenger()
    integration_id: list[str] = []

    async def setup(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        saved = returns(messages, "save_integration")
        if not saved:
            return call("save_integration", integration=SLACK)
        integration_id[:] = [saved[0].split()[2].rstrip(";")]
        if not returns(messages, "request_secret"):
            return call("request_secret", integration_id=integration_id[0], secret="webhook_url")
        if not returns(messages, "mark_ready"):
            return call("mark_ready", integration_id=integration_id[0], notes="posts to #friends")
        return done("Slack is connected: posts go to #friends")

    built = with_integrations(
        db, clock, timers, messenger=messenger, model=FunctionModel(setup), http=net.client()
    )
    jobs, store = built.env.jobs, built.env.integrations
    assert jobs is not None and store is not None
    job = await jobs.start(PHONE, "connect their Slack so you can post to #friends")
    await settle(built.pipeline)

    # They got a secure link by text; the job waits for the form, not for their words.
    link = next(t for t in messenger.sent if "/api/secret/" in t)
    token = link.rsplit("/", 1)[1]
    assert "secure link" in (await asked(built.pipeline))[-1]
    assert await jobs.tell(PHONE, job, "ok i did it") == f"passed on to background task {job}"
    await settle(built.pipeline)
    assert await ended(built.pipeline) == []  # still waiting on the form

    assert await store.fulfil(token, WEBHOOK) is not None
    await settle(built.pipeline)
    assert [(e.outcome, e.text) for e in await ended(built.pipeline)] == [
        ("done", "Slack is connected: posts go to #friends")
    ]
    assert (await store.all(PHONE))[0].status == "ready"
    assert (
        "slack: connected; can post a message to their channel"
        in (await store.all(PHONE))[0].describe()
    )

    # A second job posts through it: the post waits for their yes.
    async def post(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        result = returns(messages, "slack_post_message")
        if not result:
            return call(
                "slack_post_message",
                args={"text": "dinner sat 7pm at Kin Khao"},
                ask_them="Post 'dinner sat 7pm at Kin Khao' in #friends?",
            )
        return done(f"result: {result[-1]}")

    with jobs_model(jobs, FunctionModel(post)):
        job2 = await jobs.start(PHONE, "post the dinner plan to Slack #friends")
        await settle(built.pipeline)
        assert (await asked(built.pipeline))[-1] == "Post 'dinner sat 7pm at Kin Khao' in #friends?"
        assert net.requests == []
        assert "yes or no" in await jobs.tell(PHONE, job2, "yep")  # approve must be set
        await jobs.tell(PHONE, job2, "yep", approve=True)
        await settle(built.pipeline)
    assert len(net.requests) == 1 and str(net.requests[0].url) == WEBHOOK
    assert json.loads(net.requests[0].content) == {"text": "dinner sat 7pm at Kin Khao"}
    final = (await ended(built.pipeline))[-1]
    assert (
        final.outcome == "done" and "HTTP 200" in final.text and "XXXXsecretXXXX" not in final.text
    )


async def test_a_no_means_nothing_is_sent_and_the_job_hears_why(
    db: SessionFactory, clock: FakeClock, timers: FakeTimers
) -> None:
    net = Recorder()
    heard: list[str] = []

    async def post(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        result = returns(messages, "slack_post_message")
        if not result:
            return call("slack_post_message", args={"text": "hi"}, ask_them="Post 'hi'?")
        heard.append(result[-1])
        return done("didn't post")

    built = with_integrations(db, clock, timers, model=FunctionModel(post), http=net.client())
    store, jobs = built.env.integrations, built.env.jobs
    assert store is not None and jobs is not None
    saved = await store.save(PHONE, Integration.model_validate({**SLACK, "status": "ready"}))
    await store.set_secret(PHONE, saved.id, "webhook_url", WEBHOOK)
    job = await jobs.start(PHONE, "post hi")
    await settle(built.pipeline)
    await jobs.tell(PHONE, job, "no, say hello instead", approve=False)
    await settle(built.pipeline)
    assert net.requests == []
    assert heard == ["They said no: no, say hello instead"]


class jobs_model:  # noqa: N801
    """Swap the jobs runner's model for a block (the second job in a test)."""

    def __init__(self, jobs: Any, model: FunctionModel) -> None:
        self.jobs, self.model = jobs, model

    def __enter__(self) -> None:
        self.saved, self.jobs._model = self.jobs._model, self.model

    def __exit__(self, *exc: object) -> None:
        self.jobs._model = self.saved


def test_parts_validate_what_the_agent_writes() -> None:
    with pytest.raises(ValueError):
        Integration.model_validate({"app": "Slack Workspace!", "auth": []})  # not a name
    assert ApiKey(secret="token", about="x").kind == "api_key"
    assert Param(name="q").where == "json"


async def test_the_secure_form_saves_once_and_never_shows_the_value(
    db: SessionFactory, clock: FakeClock, timers: FakeTimers
) -> None:
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.integrations import routes
    from app.integrations.store import SecretRequest

    store = with_integrations(db, clock, timers).env.integrations
    assert store is not None
    saved = await store.save(PHONE, Integration.model_validate(SLACK))
    request = SecretRequest(
        PHONE, saved.id, "webhook_url", "Slack incoming webhook URL", "slack", "j", "q"
    )
    path = store.link_for(request).removeprefix("http://x")
    web = FastAPI()
    web.include_router(routes.router)
    web.state.services = SimpleNamespace(integrations=store)
    with TestClient(web) as client:
        page = client.get(path).text
        assert "Slack incoming webhook URL" in page and 'type="password"' in page
        assert "Saved" in client.post(path, data={"value": WEBHOOK}).text
        assert "already used" in client.get(path).text  # a link works once
    assert await store.secrets(PHONE, saved.id) == {"webhook_url": WEBHOOK}


async def test_a_job_knows_each_service_and_what_they_like_there_and_what_was_done(
    db: SessionFactory, clock: FakeClock, timers: FakeTimers
) -> None:
    from app.agent.events import ToolCall
    from app.events.payload import Channel, Origin
    from app.memory.events import Remembered

    net = Recorder()
    seen: dict[str, str] = {}

    async def post(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen["instructions"] = info.instructions or ""
        if not returns(messages, "slack_post_message"):
            return call("slack_post_message", args={"text": "hi"}, ask_them="Post 'hi'?")
        if not returns(messages, "past_actions"):
            return call("past_actions", app="slack")
        seen["past"] = returns(messages, "past_actions")[-1]
        return done("posted")

    built = with_integrations(db, clock, timers, model=FunctionModel(post), http=net.client())
    store, jobs, pipeline = built.env.integrations, built.env.jobs, built.pipeline
    assert store is not None and jobs is not None
    ready = {**SLACK, "status": "ready", "notes": "their #friends channel"}
    saved = await store.save(PHONE, Integration.model_validate(ready))
    await store.set_secret(PHONE, saved.id, "webhook_url", WEBHOOK)
    for fact, app in (("sign off with -J", "slack"), ("usual is pad see ew", "doordash")):
        await pipeline.submit(
            PHONE, Origin.TEXT_AGENT, Channel.TEXT, Remembered(fact=fact, app=app)
        )

    job = await jobs.start(PHONE, "post hi")
    await settle(pipeline)
    await jobs.tell(PHONE, job, "yes", approve=True)
    await settle(pipeline)

    assert "slack: their #friends channel" in seen["instructions"]
    assert "sign off with -J" in seen["instructions"]
    assert "pad see ew" not in seen["instructions"]  # another app's preferences stay out
    calls = [e.payload for e in await pipeline.history(PHONE) if isinstance(e.payload, ToolCall)]
    assert [(c.name, c.app, c.job, c.ok) for c in calls] == [
        ("slack_post_message", "slack", job, True)
    ]
    assert "XXXXsecretXXXX" not in (calls[0].shown or "")
    assert seen["past"].startswith("slack_post_message({'text': 'hi'})")


async def test_a_template_s_api_cannot_be_rewritten_by_a_save(
    db: SessionFactory, clock: FakeClock, timers: FakeTimers
) -> None:
    store = with_integrations(db, clock, timers).env.integrations
    assert store is not None
    todoist = templates.build("todoist")
    assert todoist.api is not None
    tampered = todoist.api.model_copy(update={"allowed_hosts": ["evil.example.com"]})
    saved = await store.save(PHONE, todoist.model_copy(update={"api": tampered}))
    assert isinstance(saved.api, HttpApi) and saved.api.allowed_hosts == ["api.todoist.com"]
    with pytest.raises(ValueError, match="no template"):
        await store.save(PHONE, Integration(app="x", template="made_up"))


async def test_a_refused_key_marks_it_broken_so_the_next_job_asks_again(
    db: SessionFactory, clock: FakeClock, timers: FakeTimers
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid token"})

    heard: list[str] = []

    async def read(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not returns(messages, "todoist_projects"):
            return call("todoist_projects", args={})
        heard.append(returns(messages, "todoist_projects")[-1])
        return done("their token was refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    built = with_integrations(db, clock, timers, model=FunctionModel(read), http=client)
    store, jobs = built.env.integrations, built.env.jobs
    assert store is not None and jobs is not None
    saved = await store.save(
        PHONE, templates.build("todoist").model_copy(update={"status": "ready"})
    )
    await store.set_secret(PHONE, saved.id, "token", "tok_1234")
    await jobs.start(PHONE, "what projects do i have")
    await settle(built.pipeline)
    assert "HTTP 401" in heard[0] and "request_secret" in heard[0]
    assert (await store.all(PHONE))[0].status == "broken"
