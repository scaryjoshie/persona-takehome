"""One reply: run the agent on the conversation, then send its bubbles with typing delays.

There is no cancellation. Before each bubble the reply checks the log: if anything that
wants a reply arrived after `through_seq`, a newer reply is on its way and this one stops.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from pydantic_ai.exceptions import ModelHTTPError

from app.agent.agent import Bubbles, agent, say
from app.agent.context import to_model_messages
from app.agent.deps import AgentEnv
from app.events.payload import Channel, Origin
from app.memory.summarize import summarize_if_due
from app.text.events import AgentMessage, Reaction, UserMessage, VoiceNote
from app.users.user import Medium
from app.voice.call_state import CallPhase

log = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]

GAP = 0.4  # between bubbles
# When the model call fails, the user still gets an answer rather than silence.
REFUSED = "that's not something i can help with"
GLITCHED = "sorry, my brain glitched for a sec. can you say that again?"


def typing_time(text: str) -> float:
    return min(0.8 + 0.04 * len(text), 2.0)


class Replier:
    def __init__(self, env: AgentEnv, *, sleep: Sleep = asyncio.sleep) -> None:
        self._env = env
        self._sleep = sleep

    async def reply(self, phone: str, through_seq: int) -> None:
        env = self._env
        pipeline = env.pipeline
        memory, events = await pipeline.conversation(phone)
        first = memory.summary is None and not any(
            isinstance(e.payload, AgentMessage) for e in events
        )
        deps = env.deps(await pipeline.user(phone), Medium.TEXT, first_reply=first)
        history = to_model_messages(events, deps.user.slots.zone())
        await env.messenger.set_typing(phone, True)  # the dots cover the thinking time
        started = time.monotonic()
        try:
            result = await agent.run(
                None, message_history=history, deps=deps, output_type=Bubbles, model=env.model
            )
            output = result.output
        except ModelHTTPError as exc:  # the provider refused the request, or it failed
            log.warning("%s: reply failed: %s", phone, exc)
            refused = exc.status_code == 400
            output = Bubbles(bubbles=[REFUSED if refused else GLITCHED])
        except BaseException:
            await env.messenger.set_typing(phone, False)
            raise
        thought = time.monotonic() - started
        bubbles = [b.strip() for b in output.bubbles if b.strip()] + deps.after_reply
        if output.react:
            await self._react(phone, through_seq, output.react)
        try:
            for i, text in enumerate(bubbles):
                if deps.placed_call or await self._superseded(phone, through_seq):
                    return
                await self._env.messenger.set_typing(phone, True)
                # The first bubble's typing already ran while the model was thinking.
                typed = typing_time(text) - (thought if i == 0 else -GAP)
                if typed > 0:
                    await self._sleep(typed)
                if deps.placed_call or await self._superseded(phone, through_seq):
                    return
                await say(deps, text)
        finally:
            await self._env.messenger.set_typing(phone, False)
            pipeline.spawn(summarize_if_due(pipeline, env.model, phone))  # only when it's time

    async def _react(self, phone: str, through_seq: int, emoji: str) -> None:
        """Tapback on their latest message among those this reply answers."""
        for event in reversed(await self._env.pipeline.history(phone, limit=30)):
            if event.seq <= through_seq and isinstance(event.payload, UserMessage | VoiceNote):
                text = event.payload.text if isinstance(event.payload, UserMessage) else None
                tapback = Reaction(target_seq=event.seq, target_text=text, emoji=emoji, by="agent")
                await self._env.pipeline.submit(phone, Origin.TEXT_AGENT, Channel.TEXT, tapback)
                return

    async def _superseded(self, phone: str, through_seq: int) -> bool:
        """Stop if a call is live (the voice has the conversation), or if anything that wants
        a reply arrived after the events this reply answers. A ringing call does not stop a
        reply: an unanswered ring must never silence the text side."""
        if (await self._env.pipeline.user(phone)).call.phase is CallPhase.CONNECTED:
            return True
        recent = await self._env.pipeline.history(phone, limit=20)
        return any(e.seq > through_seq and e.payload.should_route() for e in recent)
