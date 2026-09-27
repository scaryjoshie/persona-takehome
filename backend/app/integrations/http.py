"""Call an HttpApi endpoint with their secrets filled in by us, never by the model.

The URL is checked after filling (a webhook URL is itself a secret): https only, and only the
integration's allowed hosts. Redirects aren't followed. Whatever comes back has every secret
value blanked out before a model sees it.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.integrations.integration import Endpoint, HttpApi

SECRET = re.compile(r"\{secret:([a-z][a-z0-9_]*)\}")
PARAM = re.compile(r"\{([a-z][a-z0-9_]*)\}")
REPLY_CHARS = 4000
TIMEOUT = 15.0


class Refused(Exception):
    """The call wasn't made: a missing secret or argument, or a host it may not reach."""


async def call(
    client: httpx.AsyncClient,
    api: HttpApi,
    endpoint: Endpoint,
    args: dict[str, Any],
    secrets: dict[str, str],
) -> str:
    def fill(template: str) -> str:
        def one(match: re.Match[str]) -> str:
            if match[1] not in secrets:
                raise Refused(f"the secret {match[1]} isn't saved yet")
            return secrets[match[1]]

        return SECRET.sub(one, template)

    known = {p.name for p in endpoint.params}
    unknown = set(args) - known
    if unknown:
        raise Refused(f"unknown arguments: {', '.join(sorted(unknown))}")
    missing = [p.name for p in endpoint.params if p.required and p.name not in args]
    if missing:
        raise Refused(f"missing arguments: {', '.join(missing)}")

    def path_value(match: re.Match[str]) -> str:
        return quote(str(args[match[1]]), safe="") if match[1] in args else match[0]

    path = PARAM.sub(path_value, endpoint.path)
    url = fill(api.base_url).rstrip("/") + fill(path)
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise Refused("only https is allowed")
    if (parts.hostname or "").lower() not in {h.lower() for h in api.allowed_hosts}:
        raise Refused(f"{parts.hostname} isn't one of this integration's allowed hosts")

    query = {p.name: args[p.name] for p in endpoint.params if p.where == "query" and p.name in args}
    body = {p.name: args[p.name] for p in endpoint.params if p.where == "json" and p.name in args}
    response = await client.request(
        endpoint.method,
        url,
        params=query or None,
        json=body or None,
        headers={name: fill(value) for name, value in api.headers.items()},
        timeout=TIMEOUT,
        follow_redirects=False,
    )
    return scrub(f"HTTP {response.status_code}: {_text(response)}", secrets)


def _text(response: httpx.Response) -> str:
    try:
        text = json.dumps(response.json(), ensure_ascii=False)
    except ValueError:
        text = response.text
    return text[:REPLY_CHARS] or "(empty)"


def scrub(text: str, secrets: dict[str, str]) -> str:
    for value in secrets.values():
        if len(value) >= 4:
            text = text.replace(value, "[secret]")
    return text
