"""One voice call: the browser's audio socket on one side, a GPT-Live session on the other.

While the call is up, these run at once:
- microphone: browser audio frames → the Live session
- speaker: the agent's audio → the browser
- captions: live transcript deltas → `partial` messages to the browser
- turns: finished turns, stitched back together (turns.py) → a VoiceUtterance event each
- quiet: closes a stitched turn once its speaker has been quiet for a moment
- signals: turn boundaries and tool activity → the voice responder
- state: whenever a fact changes, a silent note with where things stand

One brain: the voice only talks (and hangs up). After each finished turn, from either side,
the back office (Listener) records what was said and sends what was asked for or promised.

The call ends the first time any of these happens: the browser socket closes (the user
hung up, closed the tab, or lost the network), the agent's end_call tool fires, or the Live
session ends. Either way exactly one "call ended" event is recorded, with the reason.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Literal, cast

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict
from pydantic_ai.messages import FunctionToolCallEvent, FunctionToolResultEvent, SpeechPart
from pydantic_ai.realtime import RealtimeSession, RealtimeTurnCompleteEvent
from pydantic_ai.realtime.openai_live import (
    OpenAILiveModel,
    OpenAILiveModelSettings,
    OpenAILiveResponsesDelegation,
)

from app.agent import prompts
from app.agent.agent import agent
from app.agent.context import to_model_messages, trim_history, what_you_know
from app.agent.deps import AgentEnv
from app.events.event import Event
from app.events.payload import Channel, Origin
from app.pipeline import Pipeline
from app.users.user import Medium
from app.voice.call_events import CallEvent, CallTransition, Initiator
from app.voice.events import Speaker, VoiceUtterance
from app.voice.responder import LiveCall, VoiceResponder
from app.voice.turns import Line, TurnJoiner

log = logging.getLogger(__name__)

SEED_MESSAGES, SEED_TOKENS = 128, 8192  # GPT-Live's limits on seeded history
# An agent turn wakes the back office only if it may have promised something a tool does.
PROMISE = re.compile(r"\b(text|texting|texted|send|sending|link|spell|spelled|bye|goodbye)\b", re.I)
NOW = "Where things stand now:"
STATE_KINDS = {"slot_changed", "gmail", "contact_saved", "call_opt_out", "graduated"}

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


async def run_call(
    websocket: WebSocket,
    *,
    phone: str,
    env: AgentEnv,
    live_model: OpenAILiveModel,
    voice: VoiceResponder,
    push: Push,
) -> None:
    """Run one call until it ends. The caller has accepted the websocket."""
    pipeline = env.pipeline
    user = await pipeline.user(phone)
    history = trim_history(
        to_model_messages(await pipeline.history(phone)),
        max_messages=SEED_MESSAGES,
        max_tokens=SEED_TOKENS,
    )
    deps = env.deps(user, Medium.VOICE)
    # Instructions are fixed for the whole session, so they hold nothing that changes during
    # the call. Where things stand goes in as a silent note, again after every change.
    delegation = dict((live_model.settings or {}).get("openai_live_delegation", {}))
    delegation["instructions"] = prompts.VOICE_BACKEND
    settings = OpenAILiveModelSettings(
        openai_live_instructions=f"{prompts.PERSONA}\n\n{prompts.ONBOARDING}\n\n{prompts.CALL}",
        openai_live_delegation=cast(OpenAILiveResponsesDelegation, delegation),
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

    realtime = agent.realtime(
        live_model, deps=deps, message_history=history, model_settings=settings
    )
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
            state = StateNotes(pipeline, phone, call)
            await state.send_now()
            unsubscribe_state = pipeline.subscribe(phone, state.changed, kinds=STATE_KINDS)
            # Say hi, then pick up the setup where it stands. The reason for the call is
            # background, not a script: reading it out made the voice lead with the ask.
            next_step = (
                "ask what they want to call you"
                if user.slots.agent_name is None
                else "ask their name"
                if user.slots.user_name is None
                else "carry on from where you left off"
            )
            if user.call.initiated_by is Initiator.USER:
                opener = "They just called you. Pick up like a friend would"
            else:
                opener = "They just picked up your call. Say hi like a friend would"
            opener += f", then {next_step}."
            if user.slots.agent_name:
                opener += " Your name is already on their screen; don't say it."
            await session.send(opener)
            transcript = Transcript(phone, pipeline, push, Listener(env, call, phone))
            tasks = [
                asyncio.create_task(_microphone(websocket, session, end)),
                asyncio.create_task(_speaker(websocket, session)),
                asyncio.create_task(_captions(session, call, transcript)),
                asyncio.create_task(_turns(session, transcript)),
                asyncio.create_task(transcript.close_quiet_turns()),
                asyncio.create_task(_signals(session, call)),
            ]
            await ended.wait()
            unsubscribe()
            unsubscribe_state()
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            await transcript.finish()
    except Exception as exc:  # the Live session failed to open or died
        log.exception("%s: voice session failed", phone)
        end(f"session_error: {type(exc).__name__}")
    finally:
        closing = voice.calls.pop(phone, None)
        if closing is not None:
            closing.closed = True
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


class Transcript:
    """Captions and utterances for one call, stitched with a TurnJoiner. A turn is recorded
    (and the back office runs) once it closes: the other speaker started, or it went quiet."""

    def __init__(self, phone: str, pipeline: Pipeline, push: Push, listener: Listener) -> None:
        self._phone = phone
        self._pipeline = pipeline
        self._push = push
        self._listener = listener
        self._joiner = TurnJoiner()
        self._live: dict[Speaker, Line] = {}  # captions shown but not finished yet
        self._raw: dict[Speaker, str] = {}  # Live's id for each speaker's turn in progress

    async def caption(self, speaker: Speaker, raw_id: str, text: str) -> None:
        line, closed = self._joiner.caption(speaker, raw_id, text)
        self._live[speaker], self._raw[speaker] = line, raw_id
        await self._record(closed)
        await self._show(line, final=False)

    async def final(self, speaker: Speaker, text: str) -> None:
        """A finished turn. It carries no id; it is the turn its speaker's captions showed."""
        self._live.pop(speaker, None)
        raw_id = self._raw.pop(speaker, None) or f"{speaker.value}-{uuid.uuid4().hex}"
        line, closed = self._joiner.final(speaker, raw_id, text)
        await self._record(closed)
        await self._show(line, final=False)

    async def close_quiet_turns(self) -> None:
        while True:
            await asyncio.sleep(0.2)
            await self._record(self._joiner.due())

    async def finish(self) -> None:
        """The call ended: record the open turn; close captions cut off mid-sentence."""
        await self._record(self._joiner.close())
        for line in self._live.values():
            await self._show(line, final=True)

    async def _show(self, line: Line, *, final: bool) -> None:
        partial = TranscriptPartial(
            speaker=line.speaker, turn_id=line.turn_id, text=line.text, final=final
        )
        await self._push(self._phone, partial)

    async def _record(self, line: Line | None) -> None:
        if line is None or not line.text:
            return
        await self._show(line, final=True)
        utterance = VoiceUtterance(speaker=line.speaker, text=line.text, turn_id=line.turn_id)
        await self._pipeline.submit(self._phone, Origin.VOICE_AGENT, Channel.VOICE, utterance)
        if line.speaker is Speaker.USER or PROMISE.search(line.text):
            self._listener.heard()


async def _captions(session: RealtimeSession, call: LiveCall, transcript: Transcript) -> None:
    async for update in session.stream_transcripts(delta=True):
        speaker = _speaker_of(update.speaker)
        if speaker is Speaker.AGENT:
            call.speaking = True
        else:
            await call.user_started()
        # Live numbers turns across both speakers
        await transcript.caption(speaker, f"{speaker.value}-{update.index}", update.transcript)


async def _turns(session: RealtimeSession, transcript: Transcript) -> None:
    async for part in session.stream_transcripts():
        if part.transcript:
            await transcript.final(_speaker_of(part.speaker), part.transcript)


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


class StateNotes:
    """Where things stand, as a silent note: once at the start, then after every change.
    Changes that land together (a back-office run often records two) go as one note."""

    SETTLE = 0.3  # seconds

    def __init__(self, pipeline: Pipeline, phone: str, call: LiveCall) -> None:
        self._pipeline = pipeline
        self._phone = phone
        self._call = call
        self._task: asyncio.Task[None] | None = None

    async def send_now(self) -> None:
        await self._call.send(await self._note(), speak=False)

    def changed(self, event: Event) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._hold_soon())

    async def _hold_soon(self) -> None:
        await asyncio.sleep(self.SETTLE)
        note = await self._note()
        self._call.held = [t for t in self._call.held if not t.startswith(NOW)]  # superseded
        self._call.hold(note)

    async def _note(self) -> str:
        user = await self._pipeline.user(self._phone)
        return f"{NOW}\n{what_you_know(user.slots, user.call)}"


class Listener:
    """The back office. After each finished turn, from either side, run the agent once with
    the call so far. Its tools record what the user said and do what the user asked for or
    the voice promised; it never decides anything itself (listener.md). Its output, if any,
    goes to the voice as a silent note.

    GPT-Live's own delegation is not relied on: in practice it rarely delegates
    (docs/proposed-design/research/gpt-live-behavior.md), and the voice's backend only has
    end_call. Runs one at a time; turns that arrive during a run are covered by the next run."""

    def __init__(self, env: AgentEnv, call: LiveCall, phone: str) -> None:
        self._env = env
        self._call = call
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
            started = time.monotonic()
            try:
                pipeline = self._env.pipeline
                user = await pipeline.user(self._phone)
                history = to_model_messages(await pipeline.history(self._phone))
                result = await agent.run(
                    None,
                    message_history=history,
                    deps=self._env.deps(user, Medium.VOICE, back_office=True),
                    output_type=NoteForVoice,
                    model=self._env.model,
                    instructions=prompts.LISTENER,
                )
                if result.output.note:
                    self._call.hold(result.output.note)
            except Exception:
                log.exception("%s: listener run failed", self._phone)
            log.info("%s: back office ran in %.1fs", self._phone, time.monotonic() - started)
            if not self._again:
                return
