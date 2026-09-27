"""One voice call: the browser's audio socket on one side, a GPT-Live session on the other.

While the call is up, these run at once:
- microphone: browser audio frames → the Live session
- speaker: the agent's audio → the browser
- captions: live transcript deltas → `partial` messages to the browser
- turns: each finished turn → a VoiceUtterance event
- signals: turn boundaries and tool activity → the voice responder
- state: whenever a fact changes, a silent note with where things stand

One brain: the voice only talks (and hangs up). After each finished turn, from either side,
the call agent (CallAgent) records what was said and sends what was asked for or promised.

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

from app.agent import objectives, prompts
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
from app.events.event import Event
from app.events.payload import Channel, Origin
from app.jev import Jev
from app.jobs.runner import lines as job_lines
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
# Not remembered/forgot: a fact learned on the call was said on the call, so the voice heard
# it; re-sending instructions for it only adds churn (and idle appends can prompt speech).
STATE_KINDS = {
    "slot_changed",
    "gmail",
    "call_opt_out",
    "graduated",
    "job_started",
    "job_asked",
    "job_ended",
}

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
        # Where things stand goes in with the session, before the voice can say anything: sent
        # after the call opened, it often landed after the voice had started, without its
        # objective. Every change after that goes in as an update.
        openai_live_instructions=f"{prompts.PERSONA}\n\n{prompts.CALL}"
        + (f"\n\n# Earlier with them\n\n{earlier}" if earlier else "")
        + f"\n\n{await where_things_stand(env, phone, playbook=True)}",
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
            state = StateNotes(env, phone, call, instructions=steer)
            await state.start()
            unsubscribe_state = pipeline.subscribe(phone, state.changed, kinds=STATE_KINDS)
            await session.send(_opener(user))
            call_agent = CallAgent(env, call, phone)
            transcript = Transcript(phone, pipeline, push, call, call_agent, env.jev)
            call.wake = lambda: call_agent.heard(voice=False)  # a text: the voice answers it first
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
    if user.slots.agent_name is None:  # the first call: only the objective at hand, no agenda
        # Spelling out the plan here got recited clause by clause ("a name for me, yours,
        # and your google, so i can actually do stuff...").
        next_step = "get into the first thing, a name for you, the way a friend would"
    elif user.slots.user_name is None:
        next_step = "go into your objective"
    elif user.call.initiated_by is Initiator.AGENT and user.call.reason:
        # A callback you placed: open on what it's for now. "Pick up where you left off"
        # made the voice replay the previous call's last lines, on a different topic.
        next_step = f"get to what this call is about ({user.call.reason}) in a line"
    else:  # they called you: let them lead
        next_step = "let them say what they called about. Don't open with a question from the setup"
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
    captions used (so the browser replaces the caption), and wakes the call agent."""

    def __init__(
        self,
        phone: str,
        pipeline: Pipeline,
        push: Push,
        call: LiveCall,
        call_agent: CallAgent,
        jev: Jev | None = None,
    ) -> None:
        self._phone = phone
        self._call = call
        self._pipeline = pipeline
        self._push = push
        self._call_agent = call_agent
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
        self._call_agent.heard(voice=speaker is Speaker.AGENT)

    async def _check_commit(self, turn_id: str, saying: str) -> None:
        """At each sentence the voice finishes, ask Jev whether it just said it's on something;
        if so, the call agent acts now rather than when the whole turn ends."""
        if turn_id in self._checking:
            return
        self._checking.add(turn_id)
        try:
            user = await self._pipeline.user(self._phone)
            events = await self._pipeline.history(self._phone, limit=RECENT)
            recent = last_lines(events, user.slots.zone())
            if turn_id not in self._committed and await commits(self._jev, recent, saying):
                self._committed.add(turn_id)
                self._call_agent.heard(voice=True, saying=saying)
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
    """Where things stand for the voice: in the session's instructions from the start (see
    run_call), then again after every change. Changes that land together (a call agent run
    often records two) go as one note."""

    SETTLE = 0.3  # seconds

    def __init__(
        self, env: AgentEnv, phone: str, call: LiveCall, *, instructions: bool = False
    ) -> None:
        self._env = env
        self._phone = phone
        self._call = call
        self._instructions = instructions  # as Live instructions rather than notes
        self._task: asyncio.Task[None] | None = None
        self._at: str | None = None  # the objective it was on; set by start()

    async def start(self) -> None:
        """Remember where onboarding stands as the call begins."""
        now = objectives.ONBOARDING.current((await self._env.pipeline.user(self._phone)).slots)
        self._at = now.name if now else None

    def changed(self, event: Event) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._hold_soon())

    async def _hold_soon(self) -> None:
        await asyncio.sleep(self.SETTLE)
        # The chain moved on: said as soon as the voice is free, never left to a quiet update
        # that lands after its turn (it stalled there, waiting, after each objective).
        slots = (await self._env.pipeline.user(self._phone)).slots
        moved = objectives.ONBOARDING.moved(self._at, slots)
        now = objectives.ONBOARDING.current(slots)
        self._at = now.name if now else None
        if moved:
            await self._call.tell(moved)
        note = await where_things_stand(self._env, self._phone)
        if self._instructions:  # instructions pile up: say this one replaces the last
            await self._call.steer_when_quiet(f"{REPLACES}\n{note}")
            return
        self._call.held = [t for t in self._call.held if not t.startswith(NOW)]  # superseded
        await self._call.whisper(note)


async def where_things_stand(env: AgentEnv, phone: str, *, playbook: bool = False) -> str:
    """For the voice: the time, what's known, open tasks, what it remembers, and where it is in
    onboarding (`playbook`: with how to handle each objective ahead, at the start of a call)."""
    pipeline = env.pipeline
    user = await pipeline.user(phone)
    stage = objectives.ONBOARDING.render(user.slots, Medium.VOICE, playbook=playbook)
    facts = remembered(await pipeline.memory(phone), summary=False, numbered=False)
    open_jobs = job_lines(await env.jobs.open(phone), speaking=True) if env.jobs else []
    now = their_time(user.slots, pipeline.now())
    services = [i.describe() for i in await env.integrations.all(phone)] if env.integrations else []
    known = "\n".join([now, what_you_know(user.slots, user.call, services), *open_jobs])
    return f"{NOW}\n" + "\n\n".join(p for p in (known, facts, stage) if p)


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
CALL_AGENT_STEPS = 4  # model requests per run: a runaway run must not block the next turn
CALL_AGENT_SECONDS = 15.0


class CallAgent:
    """The call agent. After each finished turn, from either side, run the agent once with
    the call so far. Its tools record what the user said and do what the user asked for or
    the voice promised; it never decides anything itself (call_agent.md). Its output, if any,
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
                    deps=self._env.deps(user, Medium.VOICE, call_agent=True, may_act=act),
                    output_type=str,  # plain text: a structured note got answered in prose
                    model=self._env.model,
                    instructions=f"{prompts.CALL_AGENT}\n\n{woke}",
                    usage_limits=UsageLimits(request_limit=CALL_AGENT_STEPS),
                )
                note = (await asyncio.wait_for(run, CALL_AGENT_SECONDS)).output.strip()
                if note and note.strip(".").lower() not in ("null", "none"):
                    # After acting, the note is the outcome of something they asked for ("sent"):
                    # they're waiting on it. After their turn it's only background.
                    await (self._call.tell(note) if act else self._call.whisper(note))
            except Exception:
                log.exception("%s: call agent run failed", self._phone)
            log.info("%s: call agent ran in %.1fs", self._phone, time.monotonic() - started)
            if not self._again:
                return
