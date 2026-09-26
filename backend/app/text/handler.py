"""The text handler: the runner the text driver calls. Loads the user and history,
runs the agent once, delivers bubbles with typing delays. Interruption is task
cancellation, handled by the driver."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from pydantic_ai import Agent
from pydantic_ai.models import Model

from app.actions import Actions
from app.agent.agent import Reply, say
from app.agent.deps import Deps
from app.agent.views import to_model_messages
from app.routing.types import Medium
from app.text.driver import RunRequest, RunResult
from app.text.messenger import Messenger

Sleep = Callable[[float], Awaitable[None]]


def bubble_delay(text: str) -> float:
    return min(0.8 + 0.04 * len(text), 3.0)


class TextHandler:
    GAP = 0.4

    def __init__(
        self,
        agent: Agent[Deps, str],
        *,
        phone: str,
        actions: Actions,
        messenger: Messenger,
        model: Model,
        app_base_url: str,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._agent = agent
        self._phone = phone
        self._actions = actions
        self._messenger = messenger
        self._model = model
        self._app_base_url = app_base_url
        self._sleep = sleep

    async def __call__(self, request: RunRequest) -> RunResult:
        user = await self._actions.user(self._phone)
        history = to_model_messages(await self._actions.history(self._phone))
        deps = Deps(
            user=user,
            actions=self._actions,
            messenger=self._messenger,
            medium=Medium.TEXT,
            app_base_url=self._app_base_url,
        )
        result = await self._agent.run(
            None, message_history=history, deps=deps, output_type=Reply, model=self._model
        )
        bubbles = [b.strip() for b in result.output.bubbles if b.strip()]
        for i, text in enumerate(bubbles):
            await self._messenger.set_typing(self._phone, True)
            await self._sleep(bubble_delay(text) + (self.GAP if i else 0.0))
            await say(deps, text)
        await self._messenger.set_typing(self._phone, False)
        return RunResult(asked_question=bool(bubbles) and bubbles[-1].endswith("?"))
