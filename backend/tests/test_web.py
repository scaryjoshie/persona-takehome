"""The browser protocol end to end: a real WebSocket through FastAPI's test client,
with a scripted model instead of OpenAI."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from app.database import create_schema, make_engine, make_sessions
from app.google import api
from app.google import routes as google_routes
from app.google.accounts import Google
from app.main import assemble
from app.services import Services
from app.text import voice_notes as voice_note_routes
from app.voice import routes as voice_routes
from app.web import routes as web_routes
from app.web.routes import normalize
from app.web.sockets import Sockets, WebMessenger


async def reply_hi(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart("final_result", {"bubbles": ["hi"]})])


calls: list[str] = []


async def fake_transcribe(audio: bytes, filename: str, content_type: str) -> str | None:
    return "call yourself mila"


async def fake_call(websocket: WebSocket, phone: str) -> None:
    """Stands in for the Live session: echo one audio frame, then hang up."""
    calls.append(phone)
    frame = await websocket.receive_bytes()
    await websocket.send_bytes(frame)
    await websocket.close()


CLAIMS = base64.urlsafe_b64encode(json.dumps({"email": "kate@gmail.com"}).encode()).decode()


def fake_google(request: httpx.Request) -> httpx.Response:
    """Google's token endpoint and a one-message Gmail."""
    if request.url.path == "/token":
        body = {"access_token": "t", "refresh_token": "r", "id_token": f"h.{CLAIMS}.s"}
        return httpx.Response(200, json=body)
    if request.url.path.endswith("/messages"):
        return httpx.Response(200, json={"messages": [{"id": "m1"}]})
    if request.url.path.endswith("/settings/timezone"):
        return httpx.Response(200, json={"value": "America/Denver"})
    headers = [{"name": "From", "value": "ConEd"}, {"name": "Subject", "value": "Bill"}]
    return httpx.Response(200, json={"snippet": "due soon", "payload": {"headers": headers}})


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
    sockets = Sockets()
    google = Google(
        make_sessions(engine),
        creds=("client-id", "client-secret"),
        key=Fernet.generate_key().decode(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake_google)),
    )
    built = assemble(
        google=google,
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
    web.state.services = Services(
        pipeline=built.pipeline,
        voice=built.voice,
        sockets=sockets,
        run_call=fake_call,
        transcribe=fake_transcribe,
        voice_notes_dir=tmp_path,
        app_base_url="http://x",
        google=google,
    )
    web.include_router(google_routes.router)
    web.include_router(web_routes.router)
    web.include_router(voice_routes.router)
    web.include_router(voice_note_routes.router)
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


def test_audio_without_a_call_is_refused(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/ws/audio?phone=15550004444") as audio:
            audio.receive_bytes()
    assert refused.value.code == 4400


def test_audio_runs_the_call_once_a_call_is_connecting(client: TestClient) -> None:
    with client.websocket_connect("/ws?phone=15550005555") as ws:
        receive(ws)
        ws.send_text(json.dumps({"type": "call", "action": "start"}))
        receive_until(ws, "call")
        with client.websocket_connect("/ws/audio?phone=15550005555") as audio:
            audio.send_bytes(b"\x00\x01" * 480)
            assert audio.receive_bytes() == b"\x00\x01" * 480
    assert calls[-1] == "15550005555"


def test_partial_is_in_the_exported_schema() -> None:
    from app.web.schema import schema

    server = json.dumps(schema()["server_message"])
    assert '"partial"' in server and "turn_id" in server


def test_two_sockets_each_get_every_event_once(client: TestClient) -> None:
    with (
        client.websocket_connect("/ws?phone=15550006666") as a,
        client.websocket_connect("/ws?phone=15550006666") as b,
    ):
        receive(a)
        receive(b)
        a.send_text(json.dumps({"type": "call", "action": "start"}))
        for ws in (a, b):
            seen = receive_until(ws, "call")  # the call event, then slots and call state
            seen += [receive(ws), receive(ws)]
            kinds = [m["type"] for m in seen]
            assert kinds.count("event") == 1 and kinds.count("call") == 1, kinds


def events_of(client: TestClient, phone: str) -> list[dict[str, Any]]:
    snap = client.post("/api/session", json={"phone": phone}).json()
    return [e["payload"] for e in snap["events"]]


def test_reply_and_tapback_quote_their_target(client: TestClient) -> None:
    with client.websocket_connect("/ws?phone=15550007777") as ws:
        receive(ws)
        ws.send_text(json.dumps({"type": "message", "text": "first"}))
        first = receive_until(ws, "user_message")[-1]["event"]["seq"]
        ws.send_text(json.dumps({"type": "message", "text": "about that", "reply_to": first}))
        reply = receive_until(ws, "user_message")[-1]["event"]["payload"]
        assert reply["reply_to"] == first and reply["reply_to_text"] == "first"
        ws.send_text(json.dumps({"type": "react", "target_seq": first, "emoji": "❤️"}))
        tapback = receive_until(ws, "reaction")[-1]["event"]["payload"]
        assert tapback["target_text"] == "first" and tapback["by"] == "user"
        assert tapback["emoji"] == "❤️" and not tapback["removed"]


def test_saving_the_contact_card(client: TestClient) -> None:
    phone = "15550008888"
    with client.websocket_connect(f"/ws?phone={phone}") as ws:
        receive(ws)
        ws.send_text(json.dumps({"type": "contact", "action": "save"}))  # no name yet: ignored
    assert all(p["kind"] != "contact_saved" for p in events_of(client, phone))


def test_voice_message_upload_transcribes_and_plays_back(client: TestClient) -> None:
    phone = "15550009999"
    with client.websocket_connect(f"/ws?phone={phone}") as ws:
        receive(ws)
        r = client.post(
            f"/api/voice-note?phone={phone}",
            files={"audio": ("note.webm", b"fake-opus", "audio/webm;codecs=opus")},
            data={"duration_ms": "2400"},
        )
        assert r.status_code == 200
        audio_id = r.json()["audio_id"]
        note = receive_until(ws, "voice_note")[-1]["event"]["payload"]
        assert note == {
            "kind": "voice_note",
            "audio_id": audio_id,
            "duration_ms": 2400,
            "transcript": "call yourself mila",
        }
    played = client.get(f"/api/voice-note/{audio_id}")
    assert played.status_code == 200 and played.content == b"fake-opus"
    assert client.get("/api/voice-note/../../etc/passwd").status_code == 404
    bad = client.post(
        f"/api/voice-note?phone={phone}", files={"audio": ("x.txt", b"hi", "text/plain")}
    )
    assert bad.status_code == 415


def test_the_google_link_goes_straight_to_google(client: TestClient) -> None:
    to_google = client.get("/api/auth/google/start?phone=15550009999", follow_redirects=False)
    assert to_google.headers["location"].startswith(api.AUTH_URL)


def test_real_google_connects_and_peeks_at_the_inbox(client: TestClient) -> None:
    phone = "15550009997"
    to_google = client.get(f"/api/auth/google/start?phone={phone}", follow_redirects=False)
    location = to_google.headers["location"]
    assert location.startswith(api.AUTH_URL) and "access_type=offline" in location
    state = parse_qs(urlparse(location).query)["state"][0]
    done = client.get(f"/api/auth/google/callback?state={state}&code=c")
    assert "kate@gmail.com" in done.text
    connected = [p for p in events_of(client, phone) if p["kind"] == "gmail"][-1]
    assert connected["email"] == "kate@gmail.com" and connected["inbox"][0]["sender"] == "ConEd"
    kinds = [p["kind"] for p in events_of(client, phone)]
    assert kinds.index("timezone_learned") < kinds.index("gmail")  # their calendar's, first
    again = client.get(f"/api/auth/google/callback?state={state}&code=c")
    assert "expired" in again.text  # a state works once


def test_declining_on_googles_screen_is_a_failed_connection(client: TestClient) -> None:
    phone = "15550009996"
    to_google = client.get(f"/api/auth/google/start?phone={phone}", follow_redirects=False)
    state = parse_qs(urlparse(to_google.headers["location"]).query)["state"][0]
    client.get(f"/api/auth/google/callback?state={state}&error=access_denied")
    assert [p for p in events_of(client, phone) if p["kind"] == "gmail"][-1]["phase"] == "failed"


def test_calls_per_ip_limit() -> None:
    from app.voice.routes import over_call_limit

    assert [over_call_limit("10.0.0.9", 2) for _ in range(3)] == [False, False, True]
    assert over_call_limit("10.0.0.10", 2) is False  # another IP has its own count


def test_the_browser_timezone_is_ignored(client: TestClient) -> None:
    """A texting assistant can't see their device's timezone, so the simulator doesn't either."""
    with client.websocket_connect("/ws?phone=+15550004444&tz=America/Los_Angeles") as ws:
        assert ws.receive_json()["slots"]["timezone"] is None
