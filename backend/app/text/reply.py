"""The text handler: the runner the text responder calls. Loads the user and history,
runs the agent once, delivers bubbles with typing delays. Interruption is task
cancellation, handled by the responder."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from pydantic_ai import Agent
from pydantic_ai.models import Model

from app.agent.agent import Bubbles, say
from app.agent.context import to_model_messages
from app.agent.deps import Deps
from app.pipeline import Pipeline
from app.routing.types import Medium
from app.text.messenger import Messenger
from app.text.responder import RunRequest, RunResult

Sleep = Callable[[float], Awaitable[None]]


def bubble_delay(text: str) -> float:
    return min(0.8 + 0.04 * len(text), 2.0)


class Reply:
    GAP = 0.4

    def __init__(
        self,
        agent: Agent[Deps, str],
        *,
        phone: str,
        pipeline: Pipeline,
        messenger: Messenger,
        model: Model,
        app_base_url: str,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._agent = agent
        self._phone = phone
        self._pipeline = pipeline
        self._messenger = messenger
        self._model = model
        self._app_base_url = app_base_url
        self._sleep = sleep

    async def __call__(self, request: RunRequest) -> RunResult:
        user = await self._pipeline.user(self._phone)
        history = to_model_messages(await self._pipeline.history(self._phone))
        deps = Deps(
            user=user,
            pipeline=self._pipeline,
            messenger=self._messenger,
            medium=Medium.TEXT,
            app_base_url=self._app_base_url,
        )
        result = await self._agent.run(
            None, message_history=history, deps=deps, output_type=Bubbles, model=self._model
        )
        bubbles = [b.strip() for b in result.output.bubbles if b.strip()]
        for i, text in enumerate(bubbles):
            await self._messenger.set_typing(self._phone, True)
            await self._sleep(bubble_delay(text) + (self.GAP if i else 0.0))
            await say(deps, text)
        await self._messenger.set_typing(self._phone, False)
        return RunResult(asked_question=bool(bubbles) and bubbles[-1].endswith("?"))
