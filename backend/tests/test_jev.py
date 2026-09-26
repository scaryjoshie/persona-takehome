from __future__ import annotations

import asyncio

import httpx

from app.jev import Jev


def jev(handler: httpx.MockTransport, timeout: float = 1.5) -> Jev:
    return Jev(api_key="k", client=httpx.AsyncClient(transport=handler), timeout=timeout)


async def test_choice_and_yes_probability() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer k"
        q = __import__("json").loads(request.content)["questions"]["q"]
        if q["type"] == "choice":
            answer = {"type": "choice", "choice": "defer", "probabilities": {"defer": 0.7}}
        else:
            answer = {"type": "noul", "noul": 0.2}
        return httpx.Response(200, json={"answers": {"q": answer}})

    client = jev(httpx.MockTransport(handle))
    choice = await client.choice("q?", {"defer": "d", "absorb": "a"}, {"x": 1})
    assert choice is not None and choice.choice == "defer" and choice.probabilities["defer"] == 0.7
    assert await client.yes_probability("done?", {}) == 0.2


async def test_failures_and_timeouts_return_none() -> None:
    assert await jev(httpx.MockTransport(lambda r: httpx.Response(500))).choice("q", {}, {}) is None

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    assert await jev(httpx.MockTransport(slow), timeout=0.05).yes_probability("q", {}) is None
