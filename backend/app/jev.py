"""A small client for Jev (TypeSafe's decision model) through OpenRouter's Decisions API.

`POST https://openrouter.ai/api/alpha/decisions` with state and typed questions; not the
chat endpoint. Both calls return None on any failure or timeout, so callers always have
a plain fallback. See docs/proposed-design/05-routing-and-decider.md for measurements.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

URL = "https://openrouter.ai/api/alpha/decisions"


@dataclass(frozen=True)
class Choice:
    choice: str
    probabilities: dict[str, float]


class Jev:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "typesafe/jev-1.13",
        client: httpx.AsyncClient | None = None,
        timeout: float = 1.5,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.AsyncClient()
        self._timeout = timeout

    async def choice(
        self, question: str, criteria: Mapping[str, str], state: dict[str, Any]
    ) -> Choice | None:
        answer = await self._ask(
            state, {"type": "choice", "instructions": question, "criteria": dict(criteria)}
        )
        if answer is None:
            return None
        return Choice(choice=answer["choice"], probabilities=answer.get("probabilities", {}))

    async def yes_probability(self, question: str, state: dict[str, Any]) -> float | None:
        answer = await self._ask(state, {"type": "noul", "instructions": question})
        return None if answer is None else float(answer["noul"])

    async def _ask(self, state: dict[str, Any], question: dict[str, Any]) -> dict[str, Any] | None:
        try:
            response = await asyncio.wait_for(
                self._client.post(
                    URL,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={"model": self._model, "state": state, "questions": {"q": question}},
                ),
                self._timeout,
            )
            response.raise_for_status()
            return response.json()["answers"]["q"]
        except Exception as exc:
            log.warning("jev failed: %s", type(exc).__name__)
            return None
