"""The text handler: the runner the text driver calls. One agent run, then bubbles
delivered with typing delays. Interruption is task cancellation, handled by the driver."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from pydantic_ai import Agent
from pydantic_ai.models import Model

from app.agent.agent import Reply, say
from app.agent.deps import Deps
from app.agent.views import to_model_messages
from app.text.driver import RunRequest, RunResult

Sleep = Callable[[float], Awaitable[None]]


def bubble_delay(text: str) -> float:
    return min(0.8 + 0.04 * len(text), 3.0)


class TextHandler:
    GAP = 0.4

    def __init__(
        self, agent: Agent[Deps, str], deps: Deps, *, model: Model, sleep: Sleep = asyncio.sleep
    ) -> None:
        self._agent = agent
        self._deps = deps
        self._model = model
        self._sleep = sleep

    async def __call__(self, request: RunRequest) -> RunResult:
        history = to_model_messages(self._deps.user.store.events)
        result = await self._agent.run(
            None, message_history=history, deps=self._deps, output_type=Reply, model=self._model
        )
        bubbles = [b.strip() for b in result.output.bubbles if b.strip()]
        channel, phone = self._deps.channel, self._deps.user.phone
        for i, text in enumerate(bubbles):
            await channel.set_typing(phone, True)
            await self._sleep(bubble_delay(text) + (self.GAP if i else 0.0))
            await say(self._deps, text)
        await channel.set_typing(phone, False)
        return RunResult(asked_question=bool(bubbles) and bubbles[-1].endswith("?"))
