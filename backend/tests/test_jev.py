from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx

from app.agent.slots import Slots
from app.calls.state import CallState
from app.routing.deciders import DefaultDecider, JevDecider
from app.routing.types import DecidedBy, Medium, RoutingContext, Run, Verb
from app.text.events import AgentMessage
from app.voice import decider as voice
from tests.conftest import ev, typing

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def ctx() -> RoutingContext:
    return RoutingContext(
        trigger=typing(True, 3),
        run=Run(medium=Medium.VOICE, started=NOW, last_agent_turn_was_question=True, inferred=True),
        floor=Medium.VOICE,
        call=CallState(),
        still_missing=Slots().missing(),
        recent=(ev(AgentMessage(text="what should I call you?")),),
        now=NOW,
    )


def decider(handler: httpx.MockTransport, timeout: float = 1.5) -> JevDecider:
    return JevDecider(
        api_key="k",
        model="typesafe/jev-1.13",
        question=voice.QUESTION,
        criteria=voice.CRITERIA,
        fallback=DefaultDecider(voice.DEFAULTS),
        client=httpx.AsyncClient(transport=handler),
        timeout=timeout,
    )


async def test_asks_one_choice_question_and_maps_the_answer() -> None:
    seen: dict[str, Any] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(
            200,
            json={
                "answers": {
                    "verb": {
                        "type": "choice",
                        "choice": "interrupt",
                        "probabilities": {"interrupt": 0.62, "absorb": 0.3, "defer": 0.08},
                        "confidence": 0.4,
                    }
                },
                "model": "typesafe/jev-1.13",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    v = await decider(httpx.MockTransport(handle)).decide(ctx())
    assert v.verb is Verb.INTERRUPT and v.by is DecidedBy.JEV and v.confidence == 0.62
    body: dict[str, Any] = seen["body"]
    assert body["model"] == "typesafe/jev-1.13" and seen["auth"] == "Bearer k"
    assert set(body["questions"]["verb"]["criteria"]) == {"interrupt", "absorb", "defer"}
    state = body["state"]
    assert state["channel"] == "voice call"
    assert state["assistant_last_turn_asked_a_question"] is True
    assert state["new_event"] == "the user has been typing a text message for 3 seconds"
    assert state["conversation"] == ["assistant: what should I call you?"]


async def test_falls_back_on_error() -> None:
    v = await decider(httpx.MockTransport(lambda r: httpx.Response(500))).decide(ctx())
    assert v.by is DecidedBy.DEFAULT and v.verb is Verb.ABSORB
    assert v.note and v.note.startswith("jev failed")


def test_each_medium_defines_the_verbs_differently() -> None:
    from app.text import decider as text

    assert set(text.CRITERIA) == set(voice.CRITERIA) == {Verb.INTERRUPT, Verb.ABSORB, Verb.DEFER}
    assert text.QUESTION != voice.QUESTION
    assert all(text.CRITERIA[v] != voice.CRITERIA[v] for v in text.CRITERIA)


async def test_falls_back_on_timeout() -> None:
    import asyncio

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    v = await decider(httpx.MockTransport(slow), timeout=0.05).decide(ctx())
    assert v.by is DecidedBy.DEFAULT and v.note == "jev failed: TimeoutError"
