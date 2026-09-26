"""The browser protocol end to end: a real WebSocket through FastAPI's test client,
with a scripted model instead of OpenAI."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from starlette.testclient import WebSocketTestSession

from app.database import create_schema, make_engine, make_sessions
from app.main import build_app
from app.web.routes import make_router, normalize
from app.web.sockets import Sockets, WebMessenger


async def reply_hi(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart("final_result", {"bubbles": ["hi"]})])


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
    sockets = Sockets()
    pipeline = build_app(
        db=make_sessions(engine),
        messenger=WebMessenger(sockets),
        model=FunctionModel(reply_hi),
        app_base_url="http://x",
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        await create_schema(engine)
        yield
        await engine.dispose()

    web = FastAPI(lifespan=lifespan)
    web.include_router(make_router(pipeline, sockets))
    with TestClient(web) as c:
        yield c


def receive(ws: WebSocketTestSession) -> dict[str, Any]:
    return json.loads(ws.receive_text())


def receive_until(ws: WebSocketTestSession, want: str, limit: int = 20) -> list[dict[str, Any]]:
    """Messages up to and including the first with type/kind `want`."""
    seen: list[dict[str, Any]] = []
    for _ in range(limit):
        m = receive(ws)
        seen.append(m)
        kind = m.get("event", {}).get("payload", {}).get("kind")
        if m["type"] == want or kind == want:
            return seen
    raise AssertionError(f"never saw {want}: {[x['type'] for x in seen]}")


def test_normalize() -> None:
    assert normalize(" +1 (555) 000-1111 ") == "15550001111"
    assert normalize(" 15550001111") == "15550001111"  # a '+' decoded from a query string


def test_session_returns_an_empty_snapshot(client: TestClient) -> None:
    r = client.post("/api/session", json={"phone": "+1 555 000 1111"})
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "snapshot" and body["events"] == []
    assert body["floor"] == "text" and body["call"]["phase"] == "none"
    assert body["slots"]["agent_name"] is None


def test_message_round_trip_with_agent_reply(client: TestClient) -> None:
    with client.websocket_connect("/ws?phone=+15550001111") as ws:
        assert receive(ws)["type"] == "snapshot"
        ws.send_text(json.dumps({"type": "message", "text": "hey"}))
        first = receive(ws)
        assert first["type"] == "event" and first["event"]["payload"]["text"] == "hey"
        seen = receive_until(ws, "agent_message")
        assert any(m["type"] == "typing" and m["active"] for m in seen)
        assert seen[-1]["event"]["payload"]["text"] == "hi"

    snap = client.post("/api/session", json={"phone": "+15550001111"}).json()
    kinds = [e["payload"]["kind"] for e in snap["events"]]
    assert kinds[0] == "user_message" and "agent_message" in kinds


def test_typing_is_never_stored_and_bad_messages_are_ignored(client: TestClient) -> None:
    with client.websocket_connect("/ws?phone=+15550002222") as ws:
        receive(ws)
        ws.send_text(json.dumps({"type": "typing", "active": True}))
        ws.send_text("not json")
        ws.send_text(json.dumps({"type": "call", "action": "start"}))
        seen = receive_until(ws, "call")
        assert all(m.get("event", {}).get("payload", {}).get("kind") != "typing" for m in seen)
        state = receive_until(ws, "call")[-1]
        assert state["type"] == "call" and state["call"]["phase"] == "connecting"


def test_reset_clears_the_user(client: TestClient) -> None:
    with client.websocket_connect("/ws?phone=+15550003333") as ws:
        receive(ws)
        ws.send_text(json.dumps({"type": "call", "action": "start"}))
        receive_until(ws, "call")
        ws.send_text(json.dumps({"type": "reset"}))
        snap = receive_until(ws, "snapshot")[-1]
        assert snap["events"] == [] and snap["call"]["phase"] == "none"
