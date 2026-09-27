"""Connected Google accounts: the stored token, and the agent's email and calendar tools
against a fake Google."""

from __future__ import annotations

import json

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic_ai.messages import ToolCallPart

from app.agent.agent import agent
from app.agent.deps import AgentEnv, Deps
from app.agent.events import ToolCall
from app.database import SessionFactory
from app.events.payload import Channel, Origin
from app.google import drafts, preview
from app.google.accounts import Google
from app.google.events import EmailDraft, GmailEvent, GmailPhase
from app.google.models import GoogleAccountRow
from app.pipeline import Pipeline
from app.text.events import UserMessage
from app.text.reply import Replier
from app.users.user import User
from tests.conftest import PHONE, CapturingMessenger
from tests.test_agent import scripted, tools_for

DENTIST = {
    "id": "e1",
    "summary": "Dentist",
    "start": {"dateTime": "2026-10-02T15:00:00-05:00"},
    "end": {"dateTime": "2026-10-02T15:30:00-05:00"},
}


class FakeGoogle:
    """Just enough of Google's token, Gmail and Calendar APIs; records what was asked."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.sent: list[str] = []
        self.events: list[str] = []
        self.moved: list[dict[str, dict[str, str]]] = []

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
        if path.endswith("/drafts") or "/drafts/d" in path:
            return httpx.Response(200, json={"id": "d1"})
        if path.endswith("/drafts/send"):
            self.sent.append(json.loads(request.content)["id"])
            return httpx.Response(200, json={})
        if path.endswith("/events") and request.method == "POST":
            self.events.append(json.loads(request.content)["summary"])
            return httpx.Response(200, json={"htmlLink": "x"})
        if path.endswith("/events"):
            return httpx.Response(200, json={"items": [DENTIST]})
        if path.endswith("/events/e1") and request.method == "PATCH":
            self.moved.append(json.loads(request.content))
            return httpx.Response(200, json=DENTIST)
        if path.endswith("/events/e1") and request.method == "DELETE":
            return httpx.Response(204)
        if path.endswith("/events/e1"):
            return httpx.Response(200, json=DENTIST)
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
        [ToolCallPart("create_event", {"title": "Dentist", "start": "2026-10-02 15:00"})],
    )
    card = [e.payload for e in await pipeline.history(PHONE) if isinstance(e.payload, EmailDraft)]
    assert len(card) == 1 and card[0].gmail_id == "d1" and not card[0].missing
    assert fake.events == ["Dentist"] and fake.calls.count("POST /token") == 1  # token reused

    ref = card[0].ref
    await reply(pipeline, messenger, google, [ToolCallPart("send_draft", {"ref": ref})])
    assert fake.sent == []  # they haven't answered since seeing the card
    yes = UserMessage(text="yes send it")
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, yes, route=False)
    await reply(pipeline, messenger, google, [ToolCallPart("send_draft", {"ref": ref})])
    await reply(pipeline, messenger, google, [ToolCallPart("send_draft", {"ref": ref})])
    assert fake.sent == ["d1"]  # once, by the stored draft's id


async def test_moving_and_cancelling_an_event_by_its_id(
    db: SessionFactory, pipeline: Pipeline, messenger: CapturingMessenger
) -> None:
    fake = FakeGoogle()
    google = await connected(db, pipeline, fake)
    later = {"event_id": "e1", "start": "2026-10-03 10:00", "minutes": 60}
    await reply(
        pipeline,
        messenger,
        google,
        [ToolCallPart("upcoming_events", {})],
        [ToolCallPart("move_event", {"event_id": "e1", "start": "2026-10-03 09:00"})],
        [ToolCallPart("move_event", later)],
        [ToolCallPart("cancel_event", {"event_id": "e1"})],
    )
    calls = [e.payload for e in await pipeline.history(PHONE) if isinstance(e.payload, ToolCall)]
    listed = next(c for c in calls if c.name == "upcoming_events")
    assert listed.shown is not None and listed.shown.startswith("[e1] ")
    assert [m["end"]["dateTime"][11:16] for m in fake.moved] == ["09:30", "11:00"]  # kept 30 min
    assert fake.calls[-1] == "DELETE /calendar/v3/calendars/primary/events/e1"
    assert {c.app for c in calls if c.name in ("move_event", "cancel_event")} == {"google"}


async def test_editing_a_draft_updates_the_same_one_and_gaps_block_sending(
    db: SessionFactory, pipeline: Pipeline
) -> None:
    fake = FakeGoogle()
    google = await connected(db, pipeline, fake)
    account = await google.account(PHONE, (await pipeline.user(PHONE)).slots)
    assert account is not None
    first = await drafts.save(pipeline, PHONE, account, ref=None, to="", subject="hi", body="yo")
    assert first.missing == ["to"]
    yes = UserMessage(text="yes send it")
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, yes, route=False)
    with pytest.raises(ValueError, match="missing"):
        await drafts.send(pipeline, PHONE, account, first.ref)
    fixed = await drafts.save(
        pipeline, PHONE, account, ref=first.ref, to="a@b.c", subject="", body=""
    )
    assert fixed.ref == first.ref and "PUT /gmail/v1/users/me/drafts/d1" in fake.calls
    assert (fixed.subject, fixed.body) == ("hi", "yo")  # fixing the address kept the message
    with pytest.raises(ValueError, match="answered"):  # the new version hasn't been answered
        await drafts.send(pipeline, PHONE, account, first.ref)
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, yes, route=False)
    await drafts.send(pipeline, PHONE, account, first.ref)
    assert fake.sent == ["d1"]


def test_a_draft_picture_shows_what_is_missing() -> None:
    picture = preview.render(EmailDraft(ref="r", subject="Heater <3", body="Hi Maria"))
    assert picture.startswith("<svg") and ">missing<" in picture
    assert "Heater &lt;3" in picture and "Still needs: who it&#x27;s to" in picture


async def test_the_tools_appear_only_once_google_is_connected(
    db: SessionFactory, pipeline: Pipeline, messenger: CapturingMessenger
) -> None:
    before = await tools_for(pipeline, messenger, lambda env, u: env.deps(u, u.floor))
    google = await connected(db, pipeline, FakeGoogle())

    def with_google(env: AgentEnv, u: User) -> Deps:
        return AgentEnv(env.pipeline, env.messenger, env.model, "", google=google).deps(u, u.floor)

    after = await tools_for(pipeline, messenger, with_google)
    assert "send_draft" not in before and {"search_email", "send_draft", "create_event"} <= after
    assert {"move_event", "cancel_event"} <= after


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
