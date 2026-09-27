"""One voice call: the browser's audio socket on one side, a GPT-Live session on the other.

While the call is up, these run at once:
- microphone: browser audio frames → the Live session
- speaker: the agent's audio → the browser
- captions: live transcript deltas → `partial` messages to the browser
- turns: each finished turn → a VoiceUtterance event
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
from typing import cast

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from pydantic_ai.messages import FunctionToolCallEvent, FunctionToolResultEvent, SpeechPart
from pydantic_ai.realtime import RealtimeSession, RealtimeTurnCompleteEvent
from pydantic_ai.realtime.openai_live import (
    OpenAILiveModel,
    OpenAILiveModelSettings,
    OpenAILiveResponsesDelegation,
)
from pydantic_ai.usage import UsageLimits

from app.agent import prompts
from app.agent.agent import agent
from app.agent.context import (
    last_lines,
    remembered,
    their_time,
    to_model_messages,
    trim_history,
    what_you_know,
)
from app.agent.deps import AgentEnv
from app.agent.objectives import guidance, settled
from app.events.event import Event
from app.events.payload import Channel, Origin
from app.jev import Jev
from app.pipeline import RECENT, Pipeline
from app.settings import get_settings
from app.users.user import Medium, User
from app.voice.call_state import CallEvent, CallTransition, Initiator
from app.voice.events import Speaker, VoiceUtterance
from app.voice.intent import commits, slip
from app.voice.responder import LiveCall, VoiceResponder
from app.web.protocol import TranscriptPartial

log = logging.getLogger(__name__)

SEED_MESSAGES, SEED_TOKENS = 128, 8192  # GPT-Live's limits on seeded history
NOW = "Where things stand now:"
REPLACES = f'Update: this replaces every earlier "{NOW}" section; follow this one.'
STATE_KINDS = {"slot_changed", "gmail", "call_opt_out", "graduated", "remembered", "forgot"}

Push = Callable[[str, BaseModel], Awaitable[None]]


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
    memory, events = await pipeline.conversation(phone)
    history = trim_history(
        to_model_messages(events, user.slots.zone()),
        max_messages=SEED_MESSAGES,
        max_tokens=SEED_TOKENS,
    )
    # The summary goes in the instructions, not the seeded history, so trimming never drops
    # it; nothing summarizes during a call, so it can't go stale. Facts come with the state.
    earlier = memory.summary.text if memory.summary else ""
    deps = env.deps(user, Medium.VOICE)
    # Instructions are fixed for the whole session, so they hold nothing that changes during
    # the call. Where things stand goes in as a silent note, again after every change.
    delegation = dict((live_model.settings or {}).get("openai_live_delegation", {}))
    delegation["instructions"] = prompts.VOICE_BACKEND
    settings = OpenAILiveModelSettings(
        openai_live_instructions=f"{prompts.PERSONA}\n\n{prompts.CALL}"
        + (f"\n\n# Earlier with them\n\n{earlier}" if earlier else ""),
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
            steer = get_settings().live_steer == "instructions"
            state = StateNotes(pipeline, phone, call, instructions=steer)
            await state.send_now()
            unsubscribe_state = pipeline.subscribe(phone, state.changed, kinds=STATE_KINDS)
            await session.send(_opener(user))
            listener = Listener(env, call, phone)
            transcript = Transcript(phone, pipeline, push, call, listener, env.jev)
            call.wake = lambda: listener.heard(voice=False)  # a text: the voice answers it first
            tasks = [
                asyncio.create_task(_microphone(websocket, session, end)),
                asyncio.create_task(_speaker(websocket, session)),
                asyncio.create_task(_captions(session, call, transcript)),
                asyncio.create_task(_turns(session, transcript)),
                asyncio.create_task(_signals(session, call)),
                asyncio.create_task(_hang_up_when_done(call, end)),
                asyncio.create_task(_silence(call)),
                asyncio.create_task(_time_limit(call)),
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
        # One "ended" event with the first reason; a second one is dropped by the pipeline.
        ended_event = CallEvent(transition=CallTransition.ENDED, reason=outcome["reason"])
        await pipeline.submit(phone, Origin.CALL, Channel.SYSTEM, ended_event)
        with contextlib.suppress(Exception):
            await websocket.close()


def _opener(user: User) -> str:
    """Say hi, then pick up the setup where it stands. The reason for the call is
    background, not a script: reading it out made the voice lead with the ask."""
    if user.slots.agent_name is None:  # the first call: say what it's for, once
        next_step = (
            "set up the call in one easy line: it's a quick setup, a name for you, theirs, "
            "and hooking up their google so you can actually do stuff, then you'll find "
            "something you can help them with. Then ease into naming you: to be a helpful "
            "assistant you need a name, so suggest coming up with one together. Warm, never a "
            "cold question"
        )
    elif user.slots.user_name is None:
        next_step = "ask their name"
    else:  # a callback: they may have called with something, so let them lead
        next_step = (
            "bridge back in a line (like you're picking up where you left off), and if they "
            "called about something, go with that. Don't open with a question from the setup"
        )
    if user.call.initiated_by is Initiator.USER:
        opener = "They just called you. Pick up like a friend would"
    else:
        opener = "They just picked up your call. Say hi like a friend would"
    opener += f", then {next_step}."
    if user.slots.agent_name:
        opener += " Your name is already on their screen; don't say it."
    return opener


async def _microphone(
    websocket: WebSocket, session: RealtimeSession, end: Callable[[str], None]
) -> None:
    async def frames() -> AsyncIterator[bytes]:
        try:
            while True:
                yield await websocket.receive_bytes()
        except (WebSocketDisconnect, RuntimeError):
            pass

    await session.send_audio(frames())
    # Hanging up in the app sends a hang-up message just before the audio closes; audio that
    # closes on its own is a dropped connection (or a closed tab). Give the message a moment.
    await asyncio.sleep(DROP_GRACE)
    end("dropped")


async def _speaker(websocket: WebSocket, session: RealtimeSession) -> None:
    async for chunk in session.stream_audio():
        await websocket.send_bytes(chunk)


def _speaker_of(raw: str) -> Speaker:
    return Speaker.USER if raw == "user" else Speaker.AGENT


class Transcript:
    """Captions and utterances for one call. Each finished turn is recorded under the id its
    captions used (so the browser replaces the caption), and wakes the back office."""

    def __init__(
        self,
        phone: str,
        pipeline: Pipeline,
        push: Push,
        call: LiveCall,
        listener: Listener,
        jev: Jev | None = None,
    ) -> None:
        self._phone = phone
        self._call = call
        self._pipeline = pipeline
        self._push = push
        self._listener = listener
        self._open: dict[Speaker, tuple[str, str]] = {}  # speaker → (turn_id, text so far)
        self._committed: set[str] = set()  # voice turns already handed over mid-sentence
        self._checking: set[str] = set()  # voice turns with a Jev question in flight
        self._jev = jev

    async def caption(self, speaker: Speaker, turn_id: str, text: str) -> None:
        self._open[speaker] = (turn_id, text)
        if speaker is Speaker.AGENT and turn_id not in self._committed and _sentence_end(text):
            asyncio.create_task(self._check_commit(turn_id, text))  # noqa: RUF006
        await self._show(speaker, turn_id, text, final=False)

    async def final(self, speaker: Speaker, text: str) -> None:
        """A finished turn carries no id; it is the turn its speaker's captions showed."""
        opened = self._open.pop(speaker, None)
        turn_id = opened[0] if opened else f"{speaker.value}-{uuid.uuid4().hex}"
        await self._show(speaker, turn_id, text, final=True)
        utterance = VoiceUtterance(speaker=speaker, text=text, turn_id=turn_id)
        await self._pipeline.submit(self._phone, Origin.VOICE_AGENT, Channel.VOICE, utterance)
        if speaker is Speaker.AGENT:
            self._call.said(text)
            asyncio.create_task(self._check_slip(text))  # noqa: RUF006 (short-lived)
        else:
            await self._call.user_started()  # their speech counts even if no caption came
        self._listener.heard(voice=speaker is Speaker.AGENT)

    async def _check_commit(self, turn_id: str, saying: str) -> None:
        """At each sentence the voice finishes, ask Jev whether it just said it's on something;
        if so, the back office acts now rather than when the whole turn ends."""
        if turn_id in self._checking:
            return
        self._checking.add(turn_id)
        try:
            user = await self._pipeline.user(self._phone)
            events = await self._pipeline.history(self._phone, limit=RECENT)
            recent = last_lines(events, user.slots.zone())
            if turn_id not in self._committed and await commits(self._jev, recent, saying):
                self._committed.add(turn_id)
                self._listener.heard(voice=True, saying=saying)
        finally:
            self._checking.discard(turn_id)

    async def _check_slip(self, line: str) -> None:
        """The voice re-offered something already done: tell it, so it corrects itself."""
        user = await self._pipeline.user(self._phone)
        if note := await slip(self._jev, user.slots, line):
            await self._call.whisper(note)

    async def finish(self) -> None:
        """The call ended mid-sentence: close those captions and record what was said, so
        the text side knows (the voice may have been halfway through something)."""
        for speaker, (turn_id, text) in list(self._open.items()):
            await self._show(speaker, turn_id, text, final=True)
            if text.strip():
                cut = VoiceUtterance(
                    speaker=speaker, text=f"{text.strip()}... (cut off)", turn_id=turn_id
                )
                await self._pipeline.submit(self._phone, Origin.VOICE_AGENT, Channel.VOICE, cut)
        self._open.clear()

    async def _show(self, speaker: Speaker, turn_id: str, text: str, *, final: bool) -> None:
        partial = TranscriptPartial(speaker=speaker, turn_id=turn_id, text=text, final=final)
        await self._push(self._phone, partial)


async def _captions(session: RealtimeSession, call: LiveCall, transcript: Transcript) -> None:
    async for update in session.stream_transcripts(delta=True):
        speaker = _speaker_of(update.speaker)
        if speaker is Speaker.AGENT:
            call.speaking, call.voice_owes_reply = True, False
        else:
            await call.user_started()
        # Live numbers turns across both speakers
        await transcript.caption(speaker, f"{speaker.value}-{update.index}", update.transcript)


async def _turns(session: RealtimeSession, transcript: Transcript) -> None:
    async for part in session.stream_transcripts():
        if part.transcript:
            await transcript.final(_speaker_of(part.speaker), part.transcript)


def _sentence_end(text: str) -> bool:
    return text.rstrip().endswith((".", "?", "!"))


GOODBYE = re.compile(r"\b(bye|goodbye|talk soon|see you|later)\b", re.I)
SILENCE = 20.0  # seconds of nobody talking before the voice checks in
DROP_GRACE = 0.6  # seconds for a hang-up message to beat the audio closing
HANG_UP_WAIT = 10.0  # seconds: hang up anyway if no goodbye plays
MAX_CALL = 600.0  # seconds: calls are billed by the minute, so a forgotten tab can't run on
WRAP_UP = 45.0  # seconds before MAX_CALL that the voice starts wrapping up


async def _hang_up_when_done(call: LiveCall, end: Callable[[str], None]) -> None:
    """After end_call: wait for the voice to finish a goodbye (one it says next, or one it
    already said), let it play out, then hang up."""
    while True:
        await call.hang_up_asked.wait()
        asked = time.monotonic()
        while time.monotonic() - asked < HANG_UP_WAIT:  # noqa: ASYNC110 (polls conditions)
            if call.hang_up_reason == "silence" and call.heard_at > asked:
                break  # they spoke after all: stay on the line
            said_bye = call.agent_lines > call.hang_up_after or GOODBYE.search(call.last_agent_line)
            if said_bye and not call.speaking:
                await asyncio.sleep(1.0)
                return end(call.hang_up_reason)
            await asyncio.sleep(0.2)
        else:
            return end(call.hang_up_reason)
        call.hang_up_asked.clear()
        call.hang_up_reason, call.check_ins = "agent_hangup", 0


async def _silence(call: LiveCall) -> None:
    """They've gone quiet (muted, walked away): check in once, then offer text and hang up."""
    while True:
        await asyncio.sleep(1.0)
        if call.speaking:
            call.last_sound = time.monotonic()
        if time.monotonic() - call.last_sound < SILENCE:
            continue
        call.last_sound, call.check_ins = time.monotonic(), call.check_ins + 1
        if call.check_ins == 1:
            await call.send(
                "They've gone quiet. Check in once, gently, in a few words.", speak=True
            )
        else:
            await call.send(
                "Still quiet. Say in a sentence that you'll keep going by text, and say bye.",
                speak=True,
            )
            call.hang_up_reason = "silence"
            call.hang_up_after = call.agent_lines
            call.hang_up_asked.set()


async def _time_limit(call: LiveCall) -> None:
    """Calls are capped at MAX_CALL: near the end, wrap up and hang up; text carries on."""
    await asyncio.sleep(MAX_CALL - WRAP_UP)
    await call.send(
        "The call is nearly at its 10-minute limit. Wrap up in a sentence or two: say you'll "
        "keep going by text, and say bye.",
        speak=True,
    )
    await asyncio.sleep(WRAP_UP)
    call.hang_up_reason = "time_limit"
    call.hang_up_after = call.agent_lines
    call.hang_up_asked.set()


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


class StateNotes:
    """Where things stand, as a silent note: once at the start, then after every change.
    Changes that land together (a back-office run often records two) go as one note."""

    SETTLE = 0.3  # seconds

    def __init__(
        self, pipeline: Pipeline, phone: str, call: LiveCall, *, instructions: bool = False
    ) -> None:
        self._pipeline = pipeline
        self._phone = phone
        self._call = call
        self._instructions = instructions  # as Live instructions rather than notes
        self._task: asyncio.Task[None] | None = None

    async def send_now(self) -> None:
        """The first note: the only one with lines to say (updates repeat, lines shouldn't)."""
        note = await self._note(scripts=True)
        if self._instructions:
            await self._call.steer(note)
        else:
            await self._call.send(note, speak=False)

    def changed(self, event: Event) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._hold_soon())

    async def _hold_soon(self) -> None:
        await asyncio.sleep(self.SETTLE)
        note = await self._note(scripts=False)
        if self._instructions:  # instructions pile up: say this one replaces the last
            done = settled(*(await self._state()), Medium.VOICE)
            finished = (
                f"\nAlready done, so don't bring these up again: {', '.join(done)}." if done else ""
            )
            await self._call.steer_when_quiet(f"{REPLACES}{finished}\n{note}")
            return
        self._call.held = [t for t in self._call.held if not t.startswith(NOW)]  # superseded
        await self._call.whisper(note)

    async def _state(self) -> tuple[User, list[Event]]:
        user = await self._pipeline.user(self._phone)
        return user, list(await self._pipeline.history(self._phone, limit=RECENT))

    async def _note(self, *, scripts: bool) -> str:
        user, events = await self._state()
        stage = guidance(user, events, Medium.VOICE, scripts=scripts)
        facts = remembered(await self._pipeline.memory(self._phone), summary=False)
        now = their_time(user.slots, self._pipeline.now())
        parts = (f"{now}\n{what_you_know(user.slots, user.call)}", facts, stage)
        return f"{NOW}\n" + "\n\n".join(p for p in parts if p)


USER_SETTLE = 0.8  # seconds of quiet after their last piece before it counts as their turn
VOICE_TURN = (
    "You were woken because the voice just finished a turn: carry out what it just said "
    "it's doing, if they asked for it or agreed to it."
)
VOICE_SAYING = (
    "You were woken because the voice, still mid-sentence, just said it's on something: "
    "carry it out, if they asked for it or agreed to it. So far it has said:"
)
THEIR_TURN = (
    "You were woken because they just finished a turn: record facts only. Anything to send "
    "or draft waits for the voice to say it's on it; you'll be woken again then."
)
BACK_OFFICE_STEPS = 4  # model requests per run: a runaway run must not block the next turn
BACK_OFFICE_SECONDS = 15.0


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
        self._timer: asyncio.Task[None] | None = None
        self._again = False
        self._act = False  # a voice turn is waiting to be acted on
        self._saying: str | None = None  # its words so far, when it committed mid-sentence

    def heard(self, *, voice: bool, saying: str | None = None) -> None:
        """A turn finished. After the voice's, run now, and its commitments may be carried out.
        After theirs, wait for a pause first: Live splits one sentence into several finished
        pieces ("Okay" / "that's all for now"), and a piece isn't their whole answer.
        `saying`: the voice is mid-sentence and has just said it's on something."""
        self._act = self._act or voice
        self._saying = saying or self._saying
        if self._timer is not None:
            self._timer.cancel()
        self._timer = asyncio.create_task(self._after(0.0 if voice else USER_SETTLE))

    async def _after(self, delay: float) -> None:
        await asyncio.sleep(delay)
        if self._task and not self._task.done():
            self._again = True
            return
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            self._again = False
            act, self._act = self._act, False
            saying, self._saying = self._saying, None
            started = time.monotonic()
            try:
                pipeline = self._env.pipeline
                user = await pipeline.user(self._phone)
                _, events = await pipeline.conversation(self._phone)
                history = to_model_messages(events, user.slots.zone())
                woke = VOICE_TURN if act else THEIR_TURN
                if saying:  # not logged yet: the voice is still talking
                    woke = f'{VOICE_SAYING} "{saying}"'
                run = agent.run(
                    None,
                    message_history=history,
                    deps=self._env.deps(user, Medium.VOICE, back_office=True, may_act=act),
                    output_type=str,  # plain text: a structured note got answered in prose
                    model=self._env.model,
                    instructions=f"{prompts.LISTENER}\n\n{woke}",
                    usage_limits=UsageLimits(request_limit=BACK_OFFICE_STEPS),
                )
                note = (await asyncio.wait_for(run, BACK_OFFICE_SECONDS)).output.strip()
                if note and note.strip(".").lower() not in ("null", "none"):
                    await self._call.whisper(note)
            except Exception:
                log.exception("%s: listener run failed", self._phone)
            log.info("%s: back office ran in %.1fs", self._phone, time.monotonic() - started)
            if not self._again:
                return
