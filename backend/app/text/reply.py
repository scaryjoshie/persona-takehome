"""One reply: run the agent on the conversation, then send its bubbles with typing delays.

There is no cancellation. Before each bubble the reply checks the log: if anything that
wants a reply arrived after `through_seq`, a newer reply is on its way and this one stops.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from app.agent.agent import Bubbles, agent, say
from app.agent.context import to_model_messages
from app.agent.deps import AgentEnv
from app.events.payload import Channel, Origin
from app.text.events import AgentMessage, Reaction, UserMessage, VoiceNote
from app.users.user import Medium
from app.voice.call_state import CallPhase

Sleep = Callable[[float], Awaitable[None]]

GAP = 0.4  # between bubbles


def typing_time(text: str) -> float:
    return min(0.8 + 0.04 * len(text), 2.0)


class Replier:
    def __init__(self, env: AgentEnv, *, sleep: Sleep = asyncio.sleep) -> None:
        self._env = env
        self._sleep = sleep

    async def reply(self, phone: str, through_seq: int) -> None:
        env = self._env
        pipeline = env.pipeline
        events = await pipeline.history(phone)
        first = not any(isinstance(e.payload, AgentMessage) for e in events)
        deps = env.deps(await pipeline.user(phone), Medium.TEXT, first_reply=first)
        history = to_model_messages(events)
        result = await agent.run(
            None, message_history=history, deps=deps, output_type=Bubbles, model=env.model
        )
        bubbles = [b.strip() for b in result.output.bubbles if b.strip()] + deps.after_reply
        if result.output.react:
            await self._react(phone, through_seq, result.output.react)
        try:
            for i, text in enumerate(bubbles):
                if await self._superseded(phone, through_seq):
                    return
                await self._env.messenger.set_typing(phone, True)
                await self._sleep(typing_time(text) + (GAP if i else 0.0))
                if await self._superseded(phone, through_seq):
                    return
                await say(deps, text)
        finally:
            await self._env.messenger.set_typing(phone, False)

    async def _react(self, phone: str, through_seq: int, emoji: str) -> None:
        """Tapback on their latest message among those this reply answers."""
        for event in reversed(await self._env.pipeline.history(phone, limit=30)):
            if event.seq <= through_seq and isinstance(event.payload, UserMessage | VoiceNote):
                text = event.payload.text if isinstance(event.payload, UserMessage) else None
                tapback = Reaction(target_seq=event.seq, target_text=text, emoji=emoji, by="agent")
                await self._env.pipeline.submit(phone, Origin.TEXT_AGENT, Channel.TEXT, tapback)
                return

    async def _superseded(self, phone: str, through_seq: int) -> bool:
        """Stop if a call has taken over the conversation, or if anything that wants a reply
        arrived after the events this reply answers."""
        user = await self._env.pipeline.user(phone)
        if user.call.phase in (CallPhase.RINGING, CallPhase.CONNECTING, CallPhase.CONNECTED):
            return True
        recent = await self._env.pipeline.history(phone, limit=20)
        return any(e.seq > through_seq and e.payload.should_route() for e in recent)
