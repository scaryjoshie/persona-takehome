"""Connected Google accounts: the stored token, and the agent's email and calendar tools
against a fake Google."""

from __future__ import annotations

import json

import httpx
from cryptography.fernet import Fernet
from pydantic_ai.messages import ToolCallPart

from app.agent.agent import agent
from app.agent.deps import AgentEnv, Deps
from app.database import SessionFactory
from app.events.payload import Channel, Origin
from app.google.accounts import Google
from app.google.events import GmailEvent, GmailPhase
from app.google.models import GoogleAccountRow
from app.pipeline import Pipeline
from app.text.reply import Replier
from app.users.user import User
from tests.conftest import PHONE, CapturingMessenger
from tests.test_agent import scripted, tools_for


class FakeGoogle:
    """Just enough of Google's token, Gmail and Calendar APIs; records what was asked."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.sent: list[str] = []
        self.events: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        if path == "/token":
            return httpx.Response(200, json={"access_token": "fresh"})
        if path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        if path.endswith("/messages/m1"):
            headers = [{"name": "From", "value": "Maria"}, {"name": "Subject", "value": "heater"}]
            return httpx.Response(
                200, json={"snippet": "next week", "payload": {"headers": headers}}
            )
        if path.endswith("/drafts"):
            return httpx.Response(200, json={"id": "d1"})
        if path.endswith("/drafts/send"):
            self.sent.append(json.loads(request.content)["id"])
            return httpx.Response(200, json={})
        if path.endswith("/events") and request.method == "POST":
            self.events.append(json.loads(request.content)["summary"])
            return httpx.Response(200, json={"htmlLink": "x"})
        if path.endswith("/events"):
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json={})


async def connected(db: SessionFactory, pipeline: Pipeline, fake: FakeGoogle) -> Google:
    google = Google(
        db,
        creds=("id", "secret"),
        key=Fernet.generate_key().decode(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake)),
    )
    await google.save(PHONE, "kate@gmail.com", "refresh-me")
    event = GmailEvent(phase=GmailPhase.CONNECTED, email="kate@gmail.com")
    await pipeline.submit(PHONE, Origin.GOOGLE, Channel.SYSTEM, event, route=False)
    return google


async def reply(
    pipeline: Pipeline, messenger: CapturingMessenger, google: Google, *turns: list[ToolCallPart]
) -> None:
    """One text reply whose model makes these tool calls."""
    model = scripted(*turns, [ToolCallPart("final_result", {"bubbles": ["ok"]})])
    env = AgentEnv(pipeline, messenger, model, "http://x", google=google)
    with agent.override(model=model):
        await Replier(env).reply(PHONE, 0)


async def test_search_draft_send_and_schedule(
    db: SessionFactory, pipeline: Pipeline, messenger: CapturingMessenger
) -> None:
    fake = FakeGoogle()
    google = await connected(db, pipeline, fake)
    draft = {"to": "maria@x.com", "subject": "heater", "body": "any update?"}
    await reply(
        pipeline,
        messenger,
        google,
        [ToolCallPart("search_email", {"query": "from:maria"})],
        [ToolCallPart("draft_email", draft)],
        [ToolCallPart("send_draft", {"draft_id": "d1"})],
        [ToolCallPart("create_event", {"title": "Dentist", "start": "2026-10-02 15:00"})],
    )
    assert fake.sent == ["d1"] and fake.events == ["Dentist"]
    assert fake.calls.count("POST /token") == 1  # the access token is reused


async def test_the_tools_appear_only_once_google_is_connected(
    db: SessionFactory, pipeline: Pipeline, messenger: CapturingMessenger
) -> None:
    before = await tools_for(pipeline, messenger, lambda env, u: env.deps(u, u.floor))
    google = await connected(db, pipeline, FakeGoogle())

    def with_google(env: AgentEnv, u: User) -> Deps:
        return AgentEnv(env.pipeline, env.messenger, env.model, "", google=google).deps(u, u.floor)

    after = await tools_for(pipeline, messenger, with_google)
    assert "send_draft" not in before and {"search_email", "send_draft", "create_event"} <= after


async def test_tokens_are_stored_encrypted_and_disconnect_revokes(
    db: SessionFactory, pipeline: Pipeline
) -> None:
    fake = FakeGoogle()
    google = await connected(db, pipeline, fake)
    async with db() as s:
        row = await s.get(GoogleAccountRow, PHONE)
    assert row is not None and "refresh-me" not in row.refresh_token
    await google.disconnect(PHONE)
    async with db() as s:
        assert await s.get(GoogleAccountRow, PHONE) is None
    assert fake.calls[-1] == "POST /revoke"
