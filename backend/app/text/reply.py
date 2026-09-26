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
from app.users.user import Medium

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
        deps = env.deps(await pipeline.user(phone), Medium.TEXT)
        history = to_model_messages(await pipeline.history(phone))
        result = await agent.run(
            None, message_history=history, deps=deps, output_type=Bubbles, model=env.model
        )
        bubbles = [b.strip() for b in result.output.bubbles if b.strip()]
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

    async def _superseded(self, phone: str, through_seq: int) -> bool:
        """Did anything that wants a reply arrive after the events this reply answers?"""
        recent = await self._env.pipeline.history(phone, limit=20)
        return any(e.seq > through_seq and e.payload.should_route() for e in recent)
