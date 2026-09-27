"""Call an HttpApi endpoint with their secrets filled in by us, never by the model.

The URL is checked after filling (a webhook URL is itself a secret): https only, and only the
integration's allowed hosts. Redirects aren't followed. Whatever comes back has every secret
value blanked out before a model sees it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import quote, urlsplit

import httpx

from app.integrations.integration import Endpoint, HttpApi

SECRET = re.compile(r"\{secret:([a-z][a-z0-9_]*)\}")
PARAM = re.compile(r"\{([a-z][a-z0-9_]*)\}")
REPLY_CHARS = 6000
TIMEOUT = 15.0


class Refused(Exception):
    """The call wasn't made: a missing secret or argument, or a host it may not reach."""


async def call(
    client: httpx.AsyncClient,
    api: HttpApi,
    endpoint: Endpoint,
    args: dict[str, Any],
    secrets: dict[str, str],
) -> Reply:
    def fill(template: str) -> str:
        def one(match: re.Match[str]) -> str:
            if match[1] not in secrets:
                raise Refused(f"the secret {match[1]} isn't saved yet")
            return secrets[match[1]]

        return SECRET.sub(one, template)

    path, query, body, form = fill_params(endpoint, args)
    url = fill(api.base_url).rstrip("/") + fill(path)
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise Refused("only https is allowed")
    if (parts.hostname or "").lower() not in {h.lower() for h in api.allowed_hosts}:
        raise Refused(f"{parts.hostname} isn't one of this integration's allowed hosts")

    response = await client.request(
        endpoint.method,
        url,
        params=query or None,
        json=body or None,
        data=form or None,
        headers={name: fill(value) for name, value in api.headers.items()},
        timeout=TIMEOUT,
        follow_redirects=False,
    )
    text = f"HTTP {response.status_code}: {_text(response)}"
    if "link" in response.headers:  # where the next page is
        text += f"\nLink: {response.headers['link']}"
    return Reply(response.status_code, scrub(text, secrets))


def fill_params(
    endpoint: Endpoint, args: dict[str, Any]
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """The endpoint's path with its {param}s filled in, and its query, JSON body and form."""
    known = {p.name for p in endpoint.params}
    unknown = set(args) - known
    if unknown:
        raise Refused(f"unknown arguments: {', '.join(sorted(unknown))}")
    missing = [p.name for p in endpoint.params if p.required and p.name not in args]
    if missing:
        raise Refused(f"missing arguments: {', '.join(missing)}")

    def path_value(match: re.Match[str]) -> str:
        return quote(str(args[match[1]]), safe="") if match[1] in args else match[0]

    def sent(where: str) -> dict[str, Any]:
        return {
            p.sent_as: args[p.name] for p in endpoint.params if p.where == where and p.name in args
        }

    path = PARAM.sub(path_value, endpoint.path)
    return path, sent("query"), {**endpoint.body, **sent("json")}, sent("form")


@dataclass(frozen=True)
class Reply:
    status: int
    text: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def rejected(self) -> bool:
        """Their access was refused: an expired or revoked key."""
        return self.status == 401


def _text(response: httpx.Response) -> str:
    try:
        return compact(response.json())
    except ValueError:
        return response.text[:REPLY_CHARS] or "(empty)"


def compact(value: Any) -> str:
    """A reply as short JSON for a model: without empty fields, capped."""
    return json.dumps(_trim(value), ensure_ascii=False)[:REPLY_CHARS] or "(empty)"


def _trim(value: Any) -> Any:
    """Without empty fields, so a list of results fits."""
    if isinstance(value, dict):
        items = cast(dict[str, Any], value).items()
        kept = {k: _trim(v) for k, v in items}
        return {k: v for k, v in kept.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [_trim(v) for v in cast(list[Any], value)]
    return value


def scrub(text: str, secrets: dict[str, str]) -> str:
    for value in secrets.values():
        if len(value) >= 4:
            text = text.replace(value, "[secret]")
    return text
