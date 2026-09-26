"""Connected Google accounts: the stored token, the demo account, and the agent's tools."""

from __future__ import annotations

import httpx
from cryptography.fernet import Fernet
from pydantic_ai.messages import ToolCallPart

from app.agent.agent import agent
from app.agent.deps import AgentEnv, Deps
from app.database import SessionFactory
from app.events.payload import Channel, Origin
from app.google.accounts import DEMO_EMAIL, DemoAccount, Google
from app.google.events import GmailEvent, GmailPhase
from app.google.models import GoogleAccountRow
from app.pipeline import Pipeline
from app.text.reply import Replier
from app.users.user import User
from tests.conftest import PHONE, CapturingMessenger
from tests.test_agent import scripted


async def connect_demo(pipeline: Pipeline) -> None:
    demo = GmailEvent(phase=GmailPhase.CONNECTED, email=DEMO_EMAIL, demo=True)
    await pipeline.submit(PHONE, Origin.GOOGLE, Channel.SYSTEM, demo, route=False)


async def reply(
    pipeline: Pipeline, messenger: CapturingMessenger, google: Google, *turns: list[ToolCallPart]
) -> list[str]:
    """Run one text reply whose model makes these tool calls; return the tool results."""
    seen: list[str] = []
    model = scripted(*turns, [ToolCallPart("final_result", {"bubbles": ["ok"]})])
    env = AgentEnv(pipeline, messenger, model, "http://x", google=google)
    with agent.override(model=model):
        await Replier(env).reply(PHONE, 0)
    for e in await pipeline.history(PHONE):
        if e.kind == "tool_call":
            seen.append(e.payload.model_dump()["name"])
    return seen


async def test_the_demo_account_drafts_sends_and_schedules(
    db: SessionFactory, pipeline: Pipeline, messenger: CapturingMessenger
) -> None:
    google = Google(db)
    await connect_demo(pipeline)
    await reply(
        pipeline,
        messenger,
        google,
        [ToolCallPart("search_email", {"query": "landlord"})],
        [
            ToolCallPart(
                "draft_email", {"to": "maria@x.com", "subject": "heater", "body": "any update?"}
            )
        ],
    )
    account = await google.account(PHONE, (await pipeline.user(PHONE)).slots)
    assert isinstance(account, DemoAccount)
    draft_id = next(iter(account.drafts))
    await reply(
        pipeline,
        messenger,
        google,
        [ToolCallPart("send_draft", {"draft_id": draft_id})],
        [ToolCallPart("create_event", {"title": "Dentist", "start": "2026-10-02 15:00"})],
    )
    assert account.sent == [draft_id]
    assert any(e["title"] == "Dentist" for e in await account.upcoming(7))


async def test_the_tools_appear_only_once_google_is_connected(
    db: SessionFactory, pipeline: Pipeline, messenger: CapturingMessenger
) -> None:
    from tests.test_agent import tools_for

    google = Google(db)
    before = await tools_for(pipeline, messenger, lambda env, u: env.deps(u, u.floor))
    await connect_demo(pipeline)

    def with_google(env: AgentEnv, u: User) -> Deps:
        return AgentEnv(env.pipeline, env.messenger, env.model, "", google=google).deps(u, u.floor)

    after = await tools_for(pipeline, messenger, with_google)
    assert "send_draft" not in before and {"search_email", "send_draft", "create_event"} <= after


async def test_real_tokens_are_stored_encrypted_and_refreshed(db: SessionFactory) -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"access_token": "fresh"})

    google = Google(
        db,
        creds=("id", "secret"),
        key=Fernet.generate_key().decode(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    await google.save(PHONE, "kate@gmail.com", "refresh-me")
    async with db() as s:
        row = await s.get(GoogleAccountRow, PHONE)
    assert row is not None and "refresh-me" not in row.refresh_token
    assert await google.access_token(PHONE) == "fresh"
    assert await google.access_token(PHONE) == "fresh" and calls == ["/token"]  # cached
    await google.disconnect(PHONE)
    async with db() as s:
        assert await s.get(GoogleAccountRow, PHONE) is None
    assert calls[-1] == "/revoke"
