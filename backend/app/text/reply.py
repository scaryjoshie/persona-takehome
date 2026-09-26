"""One reply: run the agent on the conversation, then send its bubbles with typing delays.

There is no cancellation. Before each bubble the reply checks the log: if anything that
wants a reply arrived after `through_seq`, a newer reply is on its way and this one stops.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from pydantic_ai import Agent
from pydantic_ai.models import Model

from app.agent.agent import Bubbles, say
from app.agent.context import to_model_messages
from app.agent.deps import Deps
from app.pipeline import Pipeline
from app.text.messenger import Messenger
from app.users.user import Medium

Sleep = Callable[[float], Awaitable[None]]

GAP = 0.4  # between bubbles


def typing_time(text: str) -> float:
    return min(0.8 + 0.04 * len(text), 2.0)


class Replier:
    def __init__(
        self,
        agent: Agent[Deps, str],
        *,
        pipeline: Pipeline,
        messenger: Messenger,
        model: Model,
        app_base_url: str,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._agent = agent
        self._pipeline = pipeline
        self._messenger = messenger
        self._model = model
        self._app_base_url = app_base_url
        self._sleep = sleep

    async def reply(self, phone: str, through_seq: int) -> None:
        pipeline = self._pipeline
        deps = Deps(
            user=await pipeline.user(phone),
            pipeline=pipeline,
            messenger=self._messenger,
            medium=Medium.TEXT,
            app_base_url=self._app_base_url,
        )
        history = to_model_messages(await pipeline.history(phone))
        result = await self._agent.run(
            None, message_history=history, deps=deps, output_type=Bubbles, model=self._model
        )
        bubbles = [b.strip() for b in result.output.bubbles if b.strip()]
        try:
            for i, text in enumerate(bubbles):
                if await self._superseded(phone, through_seq):
                    return
                await self._messenger.set_typing(phone, True)
                await self._sleep(typing_time(text) + (GAP if i else 0.0))
                if await self._superseded(phone, through_seq):
                    return
                await say(deps, text)
        finally:
            await self._messenger.set_typing(phone, False)

    async def _superseded(self, phone: str, through_seq: int) -> bool:
        """Did anything that wants a reply arrive after the events this reply answers?"""
        recent = await self._pipeline.history(phone, limit=20)
        return any(e.seq > through_seq and e.payload.should_route() for e in recent)
