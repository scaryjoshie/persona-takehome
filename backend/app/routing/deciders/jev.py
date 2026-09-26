"""Jev (TypeSafe's decision model) via OpenRouter's Decisions API.

One `choice` question over the three verbs, with the run and the recent conversation as
state. Events render themselves (`Payload.turn`), so no agent code is needed here. If the
call fails or exceeds the timeout, the fallback decider answers instead, and the verdict
says so.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.routing.deciders.protocol import Decider
from app.routing.types import DecidedBy, Medium, RoutingContext, Verb, Verdict

log = logging.getLogger(__name__)

URL = "https://openrouter.ai/api/alpha/decisions"

CRITERIA = {
    Verb.INTERRUPT: (
        "The event is a reply to something the assistant is waiting on, or needs a response "
        "right now. Address it immediately."
    ),
    Verb.ABSORB: "The event is background information the assistant should know but not remark on.",
    Verb.DEFER: (
        "The event deserves a response, but the assistant is mid-response on something else "
        "and should finish first."
    ),
}


class JevDecider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        fallback: Decider,
        client: httpx.AsyncClient | None = None,
        timeout: float = 1.5,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._fallback = fallback
        self._client = client or httpx.AsyncClient()
        self._timeout = timeout

    async def decide(self, ctx: RoutingContext) -> Verdict:
        try:
            answer = await asyncio.wait_for(self._ask(ctx), self._timeout)
        except Exception as exc:  # timeout, network, or an unexpected response shape
            log.warning("jev decider failed (%s); using fallback", exc)
            verdict = await self._fallback.decide(ctx)
            return verdict.model_copy(update={"note": f"jev failed: {type(exc).__name__}"})
        verb = Verb(answer["choice"])
        probabilities: dict[str, float] = answer.get("probabilities", {})
        confidence = probabilities.get(verb.value)
        if confidence is None:
            confidence = float(answer.get("confidence", 0.0))
        return Verdict(
            verb=verb,
            confidence=confidence,
            by=DecidedBy.JEV,
            note=", ".join(f"{k}={v:.2f}" for k, v in sorted(probabilities.items())),
        )

    async def _ask(self, ctx: RoutingContext) -> dict[str, Any]:
        response = await self._client.post(
            URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "state": state_for(ctx),
                "questions": {
                    "verb": {
                        "type": "choice",
                        "instructions": (
                            "A new event arrived while the assistant is responding. "
                            "How should the assistant handle it?"
                        ),
                        "criteria": {verb.value: text for verb, text in CRITERIA.items()},
                    }
                },
            },
        )
        response.raise_for_status()
        return response.json()["answers"]["verb"]


def state_for(ctx: RoutingContext) -> dict[str, Any]:
    """What Jev sees: where the conversation is, what the assistant is doing, the event."""
    conversation: list[str] = []
    for event in ctx.recent:
        turn = event.payload.turn(event.ts)
        if turn is not None:
            conversation.append(f"{turn.role.value}: {turn.text}")
    run = ctx.run
    return {
        "channel": "voice call" if ctx.floor is Medium.VOICE else "text messages",
        "assistant_currently_responding": run is not None,
        "assistant_last_turn_asked_a_question": bool(run and run.last_agent_turn_was_question),
        "still_needed_from_user": list(ctx.still_missing),
        "conversation": conversation[-12:],
        "new_event": ctx.trigger.payload.describe(),
    }
