"""Apps they sign in to, through Composio (faked here): research from the catalog, the texted
sign-in link and its callback, read or act from the catalog's labels, and the proxy's guards."""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agent.events import ToolCall
from app.database import SessionFactory
from app.integrations.composio import Tool
from app.integrations.integration import (
    Action,
    ComposioApi,
    ComposioConnection,
    Endpoint,
    Integration,
    Param,
)
from app.jobs import runner
from app.main import App
from tests.conftest import PHONE, CapturingMessenger, FakeClock, FakeTimers, settle
from tests.test_integrations import (
    asked,
    call,
    done,
    ended,
    jobs_model,
    returns,
    with_integrations,
)

VERSION = "20260915_00"
LINK = "https://connect.composio.dev/link/lk_1"


def tool(slug: str, toolkit: str, tags: list[str], deprecated: bool = False) -> dict[str, Any]:
    return {
        "slug": slug,
        "name": slug.split("_", 1)[1].replace("_", " ").capitalize(),
        "toolkit": {"slug": toolkit},
        "description": f"Does {slug}.",
        "input_parameters": {
            "properties": {"channel": {"type": "string", "description": "Channel id. More."}},
            "required": ["channel"],
        },
        "tags": tags,
        "is_deprecated": deprecated,
    }


CATALOG = {
    t["slug"]: t
    for t in (
        tool("SLACK_SEND_MESSAGE", "slack", ["openWorldHint", "createHint"]),
        tool("SLACK_FIND_CHANNELS", "slack", ["readOnlyHint", "openWorldHint"]),
        tool("SLACK_GET_AN_OLD_THING", "slack", ["readOnlyHint"], deprecated=True),
        tool("SHIPDAY_INSERT_ORDER", "shipday", ["createHint"]),  # a lookalike from search
    )
}


class FakeComposio:
    """Composio's API as far as we use it. `status` is the connected account's."""

    def __init__(self) -> None:
        self.status = "INITIATED"
        self.sent: list[tuple[str, str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v3.1")
        body = json.loads(request.content) if request.content else None
        self.sent.append((request.method, path, body))
        match request.method, path:
            case "GET", "/toolkits/slack":
                return ok(
                    slug="slack",
                    name="Slack",
                    meta={"version": VERSION},
                    composio_managed_auth_schemes=["OAUTH2"],
                )
            case "GET", "/toolkits/spotify":
                return ok(slug="spotify", name="Spotify", composio_managed_auth_schemes=[])
            case "GET", "/toolkits/doordash":
                return httpx.Response(
                    404, json={"error": {"message": "Toolkit doordash not found"}}
                )
            case "GET", "/tools":
                assert request.url.params["toolkit_versions[slack]"] == VERSION
                slugs = request.url.params["tool_slugs"].split(",")
                return ok(items=[CATALOG[s] for s in slugs if s in CATALOG])
            case "POST", "/tool_router/session":
                return ok(session_id="trs_1")
            case "POST", "/tool_router/session/trs_1/search":
                return ok(
                    results=[
                        {
                            "primary_tool_slugs": ["SLACK_SEND_MESSAGE", "SHIPDAY_INSERT_ORDER"],
                            "related_tool_slugs": ["SLACK_FIND_CHANNELS", "SLACK_GET_AN_OLD_THING"],
                            "recommended_plan_steps": ["Resolve the channel id first"],
                            "known_pitfalls": ["not_in_channel if the bot isn't a member"],
                        }
                    ]
                )
            case "POST", "/auth_configs":
                return ok(auth_config={"id": "ac_1"})
            case "PATCH", "/auth_configs/ac_1":
                return ok()
            case "POST", "/connected_accounts/link":
                return ok(redirect_url=LINK, connected_account_id="ca_1", expires_at="soon")
            case "GET", "/connected_accounts/ca_1":
                return ok(id="ca_1", status=self.status)
            case "POST", "/tools/execute/proxy":
                return ok(status=200, data={"id": "U1"})
            case "POST", _ if path.startswith("/tools/execute/"):
                if self.status != "ACTIVE":
                    return ok(successful=False, data={}, error="invalid_auth")
                return ok(successful=True, data={"channels": [{"id": "C1", "name": "friends"}]})
            case _:
                return httpx.Response(500)

    def calls(self, method: str, path: str) -> list[Any]:
        return [body for m, p, body in self.sent if (m, p) == (method, path)]

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


def ok(**body: Any) -> httpx.Response:
    return httpx.Response(200, json=body)


def built_with(
    db: SessionFactory,
    clock: FakeClock,
    timers: FakeTimers,
    api: FakeComposio,
    model: Any,
    **kw: Any,
) -> App:
    return with_integrations(
        db, clock, timers, model=model, http=api.client(), composio_api_key="ck_test", **kw
    )


def test_read_or_act_is_the_catalogs_label_and_untagged_means_act() -> None:
    def labelled(*tags: str) -> Tool:
        return Tool("X", "x", "x", "", {}, tags, False)

    assert labelled("readOnlyHint", "idempotentHint").reads
    assert not labelled("readOnlyHint", "destructiveHint").reads
    assert not labelled().reads and not labelled("createHint").reads


async def test_research_shows_only_this_app_s_current_actions_with_plan_and_pitfalls(
    db: SessionFactory, clock: FakeClock, timers: FakeTimers
) -> None:
    heard: list[str] = []

    async def research(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        heard[:] = returns(messages, "app_actions")
        apps = ["slack", "doordash", "spotify"]
        if len(heard) < len(apps):
            return call("app_actions", app=apps[len(heard)], what="post a message to a channel")
        return done("researched")

    api = FakeComposio()
    built = built_with(db, clock, timers, api, FunctionModel(research))
    assert built.env.jobs is not None
    await built.env.jobs.start(PHONE, "what can you do in slack")
    await settle(built.pipeline)
    # The session sees one app and can't connect or run anything.
    [session] = api.calls("POST", "/tool_router/session")
    assert session["toolkits"] == {"enable": ["slack"]}
    assert session["manage_connections"] == {"enable": False}
    assert session["workbench"] == {"enable": False}
    assert session["user_id"] != PHONE

    [slack, doordash, spotify] = heard
    assert "SLACK_SEND_MESSAGE (act) Send message" in slack
    assert "SLACK_FIND_CHANNELS (read)" in slack and "channel (string, required)" in slack
    assert "SHIPDAY" not in slack and "OLD_THING" not in slack
    assert "Resolve the channel id first" in slack and "not_in_channel" in slack
    assert doordash == "doordash isn't in the catalog"
    assert "Spotify has no sign-in here" in spotify


async def test_a_job_connects_slack_through_the_sign_in_link_then_sends_only_after_a_yes(
    db: SessionFactory, clock: FakeClock, timers: FakeTimers
) -> None:
    api = FakeComposio()
    messenger = CapturingMessenger()
    chosen = ["SLACK_SEND_MESSAGE", "SLACK_FIND_CHANNELS"]

    async def setup(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not returns(messages, "connect_app"):
            return call("connect_app", app="slack", actions=chosen)
        assert store is not None
        integration = (await store.all(PHONE))[0].id
        tried = returns(messages, "mark_ready")
        if not tried:  # before any read: refused
            return call("mark_ready", integration_id=integration, notes="their #friends")
        if not returns(messages, "app_run"):
            return call(
                "app_run", integration_id=integration, action="slack_find_channels", args={}
            )
        if len(tried) == 1:
            return call("mark_ready", integration_id=integration, notes="their #friends")
        return done(f"{tried[0]} / {tried[1]}")

    built = built_with(db, clock, timers, api, FunctionModel(setup), messenger=messenger)
    jobs, store = built.env.jobs, built.env.integrations
    assert jobs is not None and store is not None
    job = await jobs.start(PHONE, "connect their Slack")
    await settle(built.pipeline)

    # The link came by text from code; the job waits for the sign-in, not for their words.
    assert LINK in messenger.sent
    assert "sign in to Slack" in (await asked(built.pipeline))[-1]
    assert "sign-in link" in runner.lines(await jobs.open(PHONE))[0]
    await jobs.tell(PHONE, job, "done!")
    await settle(built.pipeline)
    assert await ended(built.pipeline) == []
    # Composio runs only the chosen actions with this sign-in.
    assert api.calls("PATCH", "/auth_configs/ac_1") == [
        {"type": "default", "tool_access_config": {"tools_available_for_execution": chosen}}
    ]
    saved = (await store.all(PHONE))[0]
    part = saved.composio
    assert part is not None and part.connected_account_id == "ca_1"
    assert part.user_id.startswith("u_") and PHONE not in part.user_id
    assert isinstance(saved.api, ComposioApi) and saved.api.version == VERSION
    assert [(a.slug, a.effect) for a in saved.api.actions] == [
        ("SLACK_SEND_MESSAGE", "act"),
        ("SLACK_FIND_CHANNELS", "read"),
    ]

    [link] = api.calls("POST", "/connected_accounts/link")
    token = link["callback_url"].rsplit("/", 1)[1]
    api.status = "ACTIVE"
    assert await store.signed_in(token) is not None
    await settle(built.pipeline)
    [finished] = await ended(built.pipeline)
    assert finished.outcome == "done"
    assert finished.text == "not ready: check it with one read first (app_run) / slack is connected"
    assert (await store.all(PHONE))[0].describe() == (
        "slack: connected; can send message, find channels"
    )

    # A second job sends a message: it waits for their yes, then runs at the pinned version.
    async def send(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen["instructions"] = info.instructions or ""
        if not returns(messages, "app_run"):
            return call(
                "app_run",
                integration_id=saved.id,
                action="SLACK_SEND_MESSAGE",
                args={"channel": "C1", "markdown_text": "dinner at 7"},
                ask_them="Send 'dinner at 7' to #friends?",
            )
        return done("sent")

    seen: dict[str, str] = {}
    with jobs_model(jobs, FunctionModel(send)):
        job2 = await jobs.start(PHONE, "tell #friends dinner is at 7")
        await settle(built.pipeline)
        assert (await asked(built.pipeline))[-1] == "Send 'dinner at 7' to #friends?"
        assert api.calls("POST", "/tools/execute/SLACK_SEND_MESSAGE") == []
        await jobs.tell(PHONE, job2, "yes", approve=True)
        await settle(built.pipeline)
    assert f"app_run on {saved.id}: SLACK_SEND_MESSAGE (act)" in seen["instructions"]
    assert api.calls("POST", "/tools/execute/SLACK_SEND_MESSAGE") == [
        {
            "connected_account_id": "ca_1",
            "user_id": part.user_id,
            "version": VERSION,
            "arguments": {"channel": "C1", "markdown_text": "dinner at 7"},
        }
    ]
    calls = [
        e.payload for e in await built.pipeline.history(PHONE) if isinstance(e.payload, ToolCall)
    ]
    assert [(c.name, c.app, c.job) for c in calls] == [
        ("SLACK_FIND_CHANNELS", "slack", job),
        ("SLACK_SEND_MESSAGE", "slack", job2),
    ]


async def test_a_sign_in_that_didn_t_go_through_tells_the_job(
    db: SessionFactory, clock: FakeClock, timers: FakeTimers
) -> None:
    api = FakeComposio()

    async def setup(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        result = returns(messages, "connect_app")
        if not result:
            return call("connect_app", app="slack", actions=["SLACK_FIND_CHANNELS"])
        return done(result[-1])

    built = built_with(db, clock, timers, api, FunctionModel(setup))
    jobs, store = built.env.jobs, built.env.integrations
    assert jobs is not None and store is not None
    await jobs.start(PHONE, "connect their Slack")
    await settle(built.pipeline)
    [link] = api.calls("POST", "/connected_accounts/link")
    api.status = "FAILED"  # whatever the redirect says, Composio's status decides
    done_ = await store.signed_in(link["callback_url"].rsplit("/", 1)[1])
    assert done_ is not None and done_[1] is False
    await settle(built.pipeline)
    assert (await ended(built.pipeline))[-1].text == (
        "their Slack sign-in didn't go through (status FAILED)"
    )


async def test_endpoints_through_the_proxy_stay_under_the_app_and_a_lapsed_sign_in_breaks_it(
    db: SessionFactory, clock: FakeClock, timers: FakeTimers
) -> None:
    api = FakeComposio()
    api.status = "ACTIVE"
    heard: list[str] = []
    runs: list[tuple[str, dict[str, Any]]] = [
        ("me", {}),  # a GET read: runs
        ("steal", {}),  # not a path under the app: refused
        ("search", {"q": "x"}),  # a POST the agent called a read: still asks
        ("SLACK_FIND_CHANNELS", {}),  # fails: the sign-in lapsed meanwhile
    ]

    async def use(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        heard[:] = returns(messages, "app_run")
        if len(heard) < len(runs):
            action, args = runs[len(heard)]
            return call(
                "app_run", integration_id=saved.id, action=action, args=args, ask_them="ok?"
            )
        return done("used")

    built = built_with(db, clock, timers, api, FunctionModel(use))
    jobs, store = built.env.jobs, built.env.integrations
    assert jobs is not None and store is not None
    endpoints = [
        Endpoint(name="me", about="who they are", path="/users.me", effect="read"),
        Endpoint(name="steal", about="x", path="https://evil.example.com/x", effect="read"),
        Endpoint(
            name="search",
            about="x",
            method="POST",
            path="/search",
            params=[Param(name="q")],
            effect="read",
        ),
    ]
    saved = await store.save(
        PHONE,
        Integration(
            app="slack",
            status="ready",
            auth=[ComposioConnection(toolkit="slack", user_id="u_1", connected_account_id="ca_1")],
            api=ComposioApi(
                toolkit="slack",
                version=VERSION,
                actions=[Action(slug="SLACK_FIND_CHANNELS", about="Find channels", effect="read")],
                endpoints=endpoints,
            ),
        ),
    )
    job = await jobs.start(PHONE, "look around slack")
    await settle(built.pipeline)
    assert (await asked(built.pipeline))[-1] == "ok?"  # the POST waits for a yes
    assert api.calls("POST", "/tools/execute/proxy") == [
        {
            "connected_account_id": "ca_1",
            "endpoint": "/users.me",
            "method": "GET",
            "body": None,
            "parameters": [],
        }
    ]
    assert heard[1] == "not called: an endpoint is a path under the app's API, like /users/me"
    api.status = "EXPIRED"
    await jobs.tell(PHONE, job, "no", approve=False)
    await settle(built.pipeline)
    assert len(api.calls("POST", "/tools/execute/proxy")) == 1
    assert "Their sign-in is EXPIRED; connect_app again" in heard[3]
    assert (await store.all(PHONE))[0].status == "broken"


async def test_an_app_they_sign_in_to_is_not_written_by_save_integration(
    db: SessionFactory, clock: FakeClock, timers: FakeTimers
) -> None:
    async def write(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        result = returns(messages, "save_integration")
        if not result:
            forged = {
                "app": "slack",
                "auth": [{"toolkit": "slack", "user_id": "u_1"}],
                "api": {
                    "toolkit": "slack",
                    "version": VERSION,
                    "actions": [{"slug": "SLACK_SEND_MESSAGE", "about": "Send", "effect": "read"}],
                },
            }
            return call("save_integration", integration=forged)
        return done(result[-1])

    built = built_with(db, clock, timers, FakeComposio(), FunctionModel(write))
    assert built.env.jobs is not None and built.env.integrations is not None
    await built.env.jobs.start(PHONE, "connect slack")
    await settle(built.pipeline)
    assert (await ended(built.pipeline))[-1].text == (
        "not saved: an app they sign in to is set up with connect_app"
    )
    assert await built.env.integrations.all(PHONE) == []


def test_the_sign_in_page_says_how_it_went() -> None:
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.integrations import routes
    from app.integrations.store import SignIn

    class Store:
        async def signed_in(self, token: str) -> tuple[SignIn, bool] | None:
            if token not in ("ok", "failed"):
                return None
            return SignIn(PHONE, "i", "slack", "j", "q"), token == "ok"

    web = FastAPI()
    web.include_router(routes.router)
    web.state.services = SimpleNamespace(integrations=Store())
    with TestClient(web) as client:
        assert "Slack is connected" in client.get("/api/signed-in/ok?status=success").text
        assert "didn't connect" in client.get("/api/signed-in/failed").text
        assert "expired" in client.get("/api/signed-in/other").text
