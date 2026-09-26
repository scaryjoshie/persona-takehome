"""One voice call: the browser's audio socket on one side, a GPT-Live session on the other.

While the call is up, five things run at once:
- microphone: browser audio frames → the Live session
- speaker: the agent's audio → the browser
- captions: live transcript deltas → `partial` messages to the browser
- turns: each finished turn → a VoiceUtterance event (recorded, not routed)
- signals: turn boundaries and tool activity → the voice responder

The call ends the first time any of these happens: the browser socket closes (the user
hung up, closed the tab, or lost the network), the agent's end_call tool fires, or the Live
session ends. Either way exactly one "call ended" event is recorded, with the reason.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent
from pydantic_ai.messages import FunctionToolCallEvent, FunctionToolResultEvent, SpeechPart
from pydantic_ai.models import Model
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.realtime import RealtimeSession, RealtimeTurnCompleteEvent
from pydantic_ai.realtime.openai_live import OpenAILiveModel, OpenAILiveModelSettings

from app.agent import prompts
from app.agent.context import state_block, to_model_messages, trim_history
from app.agent.deps import Deps
from app.calls.events import CallEvent, CallTransition
from app.events.event import Event
from app.events.payload import Channel, Origin
from app.pipeline import Pipeline
from app.text.messenger import Messenger
from app.users.user import Medium
from app.voice.events import Speaker, VoiceUtterance
from app.voice.responder import LiveCall, VoiceResponder

log = logging.getLogger(__name__)

SEED_MESSAGES, SEED_TOKENS = 128, 8192  # GPT-Live's limits on seeded history

Push = Callable[[str, BaseModel], Awaitable[None]]


class TranscriptPartial(BaseModel):
    """A live caption: `text` is the turn's full transcript so far (replace, don't append).
    `final` closes the turn; the VoiceUtterance event with the same turn_id follows."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    type: Literal["partial"] = "partial"
    speaker: Speaker
    turn_id: str
    text: str
    final: bool


@dataclass(frozen=True)
class VoiceConfig:
    listener_model: Model  # the agent's model; runs the back-office listener
    api_key: str
    live_model: str  # "gpt-live-1"
    backend_model: str | None  # None = the agent's own model does the thinking
    app_base_url: str


async def run_call(
    websocket: WebSocket,
    *,
    phone: str,
    pipeline: Pipeline,
    agent: Agent[Deps, str],
    messenger: Messenger,
    push: Push,
    config: VoiceConfig,
    voice: VoiceResponder,
) -> None:
    """Run one call until it ends. The caller has accepted the websocket."""
    user = await pipeline.user(phone)
    history = trim_history(
        to_model_messages(await pipeline.history(phone)),
        max_messages=SEED_MESSAGES,
        max_tokens=SEED_TOKENS,
    )
    deps = Deps(
        user=user,
        pipeline=pipeline,
        messenger=messenger,
        medium=Medium.VOICE,
        app_base_url=config.app_base_url,
    )
    settings = OpenAILiveModelSettings(
        openai_live_instructions=(
            f"{prompts.SPEAKING}\n\nWhat you know right now:\n{state_block(user.slots, user.call)}"
        ),
    )
    if config.backend_model:
        settings["openai_live_delegation"] = {"model": config.backend_model}
    model = OpenAILiveModel(
        config.live_model, provider=OpenAIProvider(api_key=config.api_key), settings=settings
    )

    ended = asyncio.Event()
    outcome = {"reason": "user_hangup"}  # the first reason wins

    def end(why: str) -> None:
        if not ended.is_set():
            outcome["reason"] = why
            ended.set()

    async def on_call_event(event: Event) -> None:  # the agent's end_call tool
        if (
            isinstance(event.payload, CallEvent)
            and event.payload.transition is CallTransition.ENDED
        ):
            end(event.payload.reason or "ended")

    realtime = agent.realtime(model, deps=deps, message_history=history)
    try:
        async with realtime.session() as session:
            call = LiveCall(session)
            voice.calls[phone] = call
            unsubscribe = pipeline.subscribe(phone, on_call_event, kinds={"call"})
            await pipeline.submit(
                phone,
                Origin.CALL,
                Channel.SYSTEM,
                CallEvent(transition=CallTransition.CONNECTED, call_id=uuid.uuid4().hex),
            )
            reason_for_call = user.call.reason
            await session.send(
                "Internal note: the call just connected. Greet the user"
                + (f" and say why you are calling ({reason_for_call})." if reason_for_call else ".")
            )
            listener = Listener(agent, deps, config.listener_model, session, pipeline, phone)
            open_turns: dict[Speaker, tuple[str, str]] = {}  # speaker → (turn_id, text so far)
            tasks = [
                asyncio.create_task(_microphone(websocket, session, end)),
                asyncio.create_task(_speaker(websocket, session)),
                asyncio.create_task(_captions(session, phone, push, call, open_turns)),
                asyncio.create_task(_turns(session, phone, pipeline, push, listener, open_turns)),
                asyncio.create_task(_signals(session, call)),
            ]
            await ended.wait()
            unsubscribe()
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            for speaker, (turn_id, text) in open_turns.items():  # cut off mid-sentence
                final = TranscriptPartial(speaker=speaker, turn_id=turn_id, text=text, final=True)
                await push(phone, final)
    except Exception as exc:  # the Live session failed to open or died
        log.exception("%s: voice session failed", phone)
        end(f"session_error: {type(exc).__name__}")
    finally:
        voice.calls.pop(phone, None)
        reason = outcome["reason"]
        if reason != "agent_hangup":  # end_call already recorded its own ended event
            await pipeline.submit(
                phone,
                Origin.CALL,
                Channel.SYSTEM,
                CallEvent(transition=CallTransition.ENDED, reason=reason),
            )
        with contextlib.suppress(Exception):
            await websocket.close()


async def _microphone(
    websocket: WebSocket, session: RealtimeSession, end: Callable[[str], None]
) -> None:
    async def frames() -> AsyncIterator[bytes]:
        try:
            while True:
                yield await websocket.receive_bytes()
        except (WebSocketDisconnect, RuntimeError):
            end("user_hangup")

    await session.send_audio(frames())
    end("user_hangup")


async def _speaker(websocket: WebSocket, session: RealtimeSession) -> None:
    async for chunk in session.stream_audio():
        await websocket.send_bytes(chunk)


def _speaker_of(raw: str) -> Speaker:
    return Speaker.USER if raw == "user" else Speaker.AGENT


async def _captions(
    session: RealtimeSession,
    phone: str,
    push: Push,
    call: LiveCall,
    open_turns: dict[Speaker, tuple[str, str]],
) -> None:
    async for update in session.stream_transcripts(delta=True):
        speaker = _speaker_of(update.speaker)
        if speaker is Speaker.AGENT:
            call.speaking = True
        turn_id = f"{speaker.value}-{update.index}"  # Live numbers turns across both speakers
        open_turns[speaker] = (turn_id, update.transcript)
        await push(
            phone,
            TranscriptPartial(
                speaker=speaker, turn_id=turn_id, text=update.transcript, final=False
            ),
        )


async def _turns(
    session: RealtimeSession,
    phone: str,
    pipeline: Pipeline,
    push: Push,
    listener: Listener,
    open_turns: dict[Speaker, tuple[str, str]],
) -> None:
    """Close each finished turn under the same id its captions used, so the browser
    replaces the caption instead of adding a second copy."""
    closed = 0
    async for part in session.stream_transcripts():
        if not part.transcript:
            continue
        speaker = _speaker_of(part.speaker)
        opened = open_turns.pop(speaker, None)
        turn_id = opened[0] if opened else None
        if turn_id is None:  # a turn with no captions (rare); give it an id of its own
            closed += 1
            turn_id = f"{speaker.value}-final-{closed}"
        await push(
            phone,
            TranscriptPartial(speaker=speaker, turn_id=turn_id, text=part.transcript, final=True),
        )
        utterance = VoiceUtterance(speaker=speaker, text=part.transcript, turn_id=turn_id)
        await pipeline.submit(phone, Origin.VOICE_AGENT, Channel.VOICE, utterance)
        if speaker is Speaker.USER:
            listener.heard()


async def _signals(session: RealtimeSession, call: LiveCall) -> None:
    last_agent_text = ""
    async for event in session:
        match event:
            case FunctionToolCallEvent():
                call.tool_running = True
            case FunctionToolResultEvent():
                call.tool_running = False
            case RealtimeTurnCompleteEvent():
                await call.turn_complete(asked_question=last_agent_text.rstrip().endswith("?"))
            case _:
                pass
        parts = session.new_messages()
        if parts:
            last = parts[-1]
            for p in getattr(last, "parts", []):
                if isinstance(p, SpeechPart) and p.speaker != "user" and p.transcript:
                    last_agent_text = p.transcript


class NoteForVoice(BaseModel):
    note: str | None = None


class Listener:
    """The back office. After each user turn, run the agent once with the call so far: its
    tools record what the user said, and its note goes to the voice as a silent note.

    GPT-Live is supposed to delegate this itself, but in practice it often does not
    (docs/proposed-design/research/gpt-live-behavior.md), so we do not depend on it.
    Runs one at a time; turns that arrive during a run are covered by the next run."""

    def __init__(
        self,
        agent: Agent[Deps, str],
        deps: Deps,
        model: Model,
        session: RealtimeSession,
        pipeline: Pipeline,
        phone: str,
    ) -> None:
        self._agent = agent
        self._deps = deps
        self._model = model
        self._session = session
        self._pipeline = pipeline
        self._phone = phone
        self._task: asyncio.Task[None] | None = None
        self._again = False

    def heard(self) -> None:
        if self._task and not self._task.done():
            self._again = True
            return
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            self._again = False
            try:
                user = await self._pipeline.user(self._phone)
                history = to_model_messages(await self._pipeline.history(self._phone))
                deps = Deps(
                    user=user,
                    pipeline=self._deps.pipeline,
                    messenger=self._deps.messenger,
                    medium=Medium.VOICE,
                    app_base_url=self._deps.app_base_url,
                )
                result = await self._agent.run(
                    None,
                    message_history=history,
                    deps=deps,
                    output_type=NoteForVoice,
                    model=self._model,
                    instructions=prompts.LISTENER,
                )
                if result.output.note:
                    await self._session.send(
                        f"Internal note from the back office: {result.output.note}", respond=False
                    )
            except Exception:
                log.exception("%s: listener run failed", self._phone)
            if not self._again:
                return
