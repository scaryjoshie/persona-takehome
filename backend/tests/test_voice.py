"""The voice medium: what happens to an event that arrives during a call."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.agent.call_notes import call_note
from app.events.payload import Channel, Origin
from app.gmail.events import GmailEvent, GmailPhase
from app.jev import Jev
from app.main import App
from app.pipeline import Pipeline
from app.text.events import Typing, UserMessage
from app.users.user import Medium
from app.voice.responder import LiveCall, VoiceResponder
from tests.conftest import PHONE


class FakeSession:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bool | None]] = []

    async def send(self, content: str, /, *, respond: bool | None = None) -> None:
        self.sent.append((content, respond))


async def on_a_call(app: App, voice: VoiceResponder) -> tuple[Pipeline, LiveCall, FakeSession]:
    pipeline = app.pipeline
    pipeline.responders[Medium.VOICE] = voice
    from app.voice.call_events import CallEvent, CallTransition

    for t in (CallTransition.CONNECTING, CallTransition.CONNECTED):
        await pipeline.submit(PHONE, Origin.CALL, Channel.SYSTEM, CallEvent(transition=t))
    session = FakeSession()
    call = LiveCall(session)
    voice.calls[PHONE] = call
    return pipeline, call, session


def jev_answering(choice: str) -> Jev:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["questions"]["q"]["type"] == "choice"
        return httpx.Response(
            200, json={"answers": {"q": {"choice": choice, "probabilities": {choice: 0.7}}}}
        )

    return Jev(api_key="k", client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))


async def last_decision(pipeline: Pipeline) -> dict[str, Any]:
    events = [e for e in await pipeline.history(PHONE) if e.kind == "decision"]
    return events[-1].payload.model_dump()


async def test_when_the_agent_is_quiet_the_note_goes_straight_in(app: App) -> None:
    pipeline, _, session = await on_a_call(app, VoiceResponder(notes=call_note))
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, UserMessage(text="it's sam@x.com"))
    assert len(session.sent) == 1 and "texted" in session.sent[0][0]
    assert (await last_decision(pipeline))["verb"] == "send"


async def test_a_text_while_speaking_interrupts(app: App) -> None:
    pipeline, call, session = await on_a_call(app, VoiceResponder(notes=call_note))
    call.speaking = True
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, UserMessage(text="wait"))
    assert session.sent[-1][1] is True
    assert (await last_decision(pipeline))["verb"] == "interrupt"


async def test_without_jev_typing_is_absorbed_and_gmail_deferred(app: App) -> None:
    pipeline, call, session = await on_a_call(app, VoiceResponder(notes=call_note))
    call.speaking = True
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, Typing(active=True, seconds=3))
    assert session.sent[-1][1] is False
    connected = GmailEvent(phase=GmailPhase.CONNECTED, email="s@x.com")
    await pipeline.submit(PHONE, Origin.GOOGLE, Channel.SYSTEM, connected)
    assert len(call.deferred) == 1 and (await last_decision(pipeline))["by"] == "fallback"
    await call.turn_complete(asked_question=False)
    assert call.deferred == [] and session.sent[-1][1] is True


async def test_jev_picks_the_verb_while_speaking(app: App) -> None:
    voice = VoiceResponder(notes=call_note, jev=jev_answering("interrupt"))
    pipeline, call, session = await on_a_call(app, voice)
    call.speaking = True
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, Typing(active=True, seconds=3))
    decision = await last_decision(pipeline)
    assert decision["verb"] == "interrupt" and decision["by"] == "jev"
    assert session.sent[-1][1] is True


async def test_interrupt_waits_while_a_tool_runs(app: App) -> None:
    voice = VoiceResponder(notes=call_note, jev=jev_answering("interrupt"))
    pipeline, call, _ = await on_a_call(app, voice)
    call.speaking, call.tool_running = True, True
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, Typing(active=True, seconds=3))
    assert (await last_decision(pipeline))["verb"] == "defer" and len(call.deferred) == 1


async def test_no_call_in_progress_drops(app: App) -> None:
    voice = VoiceResponder(notes=call_note)
    pipeline, _, _ = await on_a_call(app, voice)
    voice.calls.clear()
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, UserMessage(text="hello?"))
    assert (await last_decision(pipeline))["verb"] == "drop"
