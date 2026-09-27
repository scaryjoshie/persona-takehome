"""Composio over plain HTTP: sign-ins (OAuth) to apps we have no OAuth app of our own for, and
their catalog of actions. Only what the integration parts need: we never hand a model Composio's
own meta tools (multi-execute, the workbench, managing connections), which would bypass our yes
and our texted link.

Checked against the live API (v3.1) on 2026-09-27: link() replaced initiate() for managed OAuth,
executing needs a pinned toolkit version, and the catalog tags tools with MCP hints
(readOnlyHint, destructiveHint) that decide read or act here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import httpx

BASE = "https://backend.composio.dev/api/v3.1"
TIMEOUT = 30.0
OAUTH = {"OAUTH2", "OAUTH1", "DCR_OAUTH"}  # a sign-in page; anything else is a key we collect


class ComposioError(Exception):
    """Composio refused or failed; the message is safe to show a job."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Toolkit:
    slug: str
    name: str
    version: str  # the catalog's current one, which a new integration pins
    signs_in: bool  # Composio has a managed OAuth app for it


@dataclass(frozen=True)
class Tool:
    slug: str
    toolkit: str
    name: str  # short: "Send message"
    description: str
    inputs: dict[str, Any]  # JSON schema
    tags: tuple[str, ...]
    deprecated: bool

    @property
    def reads(self) -> bool:
        """The catalog's own label, never a model's: untagged means it changes something."""
        return "readOnlyHint" in self.tags and "destructiveHint" not in self.tags


@dataclass(frozen=True)
class Link:
    url: str
    connected_account: str  # known before they finish


class Composio:
    def __init__(self, key: str, client: httpx.AsyncClient) -> None:
        self._key = key
        self._client = client
        self._sessions: dict[tuple[str, str], str] = {}  # (user, toolkit) -> search session

    async def _send(self, method: str, path: str, **kw: Any) -> Any:
        try:
            response = await self._client.request(
                method, BASE + path, headers={"x-api-key": self._key}, timeout=TIMEOUT, **kw
            )
        except httpx.HTTPError as exc:
            raise ComposioError(f"Composio didn't answer: {type(exc).__name__}") from exc
        try:
            body: Any = response.json() if response.content else {}
        except ValueError:
            body = {}
        if response.is_error:
            raise ComposioError(
                f"Composio said {response.status_code}: {_reason(body)}", response.status_code
            )
        return body

    # ---- the catalog ------------------------------------------------------------------------

    async def toolkit(self, slug: str) -> Toolkit | None:
        try:
            found = await self._send("GET", f"/toolkits/{slug}")
        except ComposioError as exc:
            if exc.status == 404:
                return None
            raise
        return Toolkit(
            slug=found["slug"],
            name=found.get("name", slug),
            version=found.get("meta", {}).get("version", ""),
            signs_in=bool(OAUTH & set(found.get("composio_managed_auth_schemes") or [])),
        )

    async def tools(self, toolkit: str, version: str, slugs: list[str]) -> list[Tool]:
        """These tools as of a pinned version, with their tags and input schemas."""
        if not slugs:
            return []
        params = {"tool_slugs": ",".join(slugs), f"toolkit_versions[{toolkit}]": version}
        found = await self._send("GET", "/tools", params=params)
        return [_tool(t) for t in found.get("items", [])]

    async def search(self, user: str, toolkit: str, what: str) -> dict[str, Any]:
        """Composio's semantic search, in a session that sees only this toolkit and can't
        connect or run anything: matching tools with their schemas, a plan, known pitfalls."""
        session = self._sessions.get((user, toolkit))
        if session is None:
            created = await self._send(
                "POST",
                "/tool_router/session",
                json={
                    "user_id": user,
                    "toolkits": {"enable": [toolkit]},
                    "manage_connections": {"enable": False},
                    "workbench": {"enable": False},
                },
            )
            session = self._sessions[(user, toolkit)] = created["session_id"]
        return await self._send(
            "POST", f"/tool_router/session/{session}/search", json={"queries": [{"use_case": what}]}
        )

    # ---- sign-ins --------------------------------------------------------------------------

    async def new_auth_config(self, toolkit: str, name: str) -> str:
        """One per integration, on Composio's managed OAuth app, so its allowlist is theirs."""
        made = await self._send(
            "POST",
            "/auth_configs",
            json={
                "toolkit": {"slug": toolkit},
                "auth_config": {"type": "use_composio_managed_auth", "name": name},
            },
        )
        return made["auth_config"]["id"]

    async def allow(self, auth_config: str, slugs: list[str]) -> None:
        """Composio refuses to run anything else with this sign-in (defense in depth)."""
        await self._send(
            "PATCH",
            f"/auth_configs/{auth_config}",
            json={
                "type": "default",
                "tool_access_config": {"tools_available_for_execution": slugs},
            },
        )

    async def link(self, auth_config: str, user: str, callback_url: str) -> Link:
        made = await self._send(
            "POST",
            "/connected_accounts/link",
            json={"auth_config_id": auth_config, "user_id": user, "callback_url": callback_url},
        )
        return Link(made["redirect_url"], made["connected_account_id"])

    async def status(self, connected_account: str) -> str:
        """INITIATED, ACTIVE, FAILED, EXPIRED, INACTIVE, REVOKED…"""
        found = await self._send("GET", f"/connected_accounts/{connected_account}")
        return str(found.get("status", ""))

    # ---- running ---------------------------------------------------------------------------

    async def execute(
        self, slug: str, *, user: str, connected_account: str, version: str, args: dict[str, Any]
    ) -> tuple[bool, Any]:
        done = await self._send(
            "POST",
            f"/tools/execute/{slug}",
            json={
                "connected_account_id": connected_account,
                "user_id": user,
                "version": version,
                "arguments": args,
            },
        )
        ok = bool(done.get("successful"))
        return ok, done.get("data") if ok else done.get("error")

    async def proxy(
        self,
        *,
        connected_account: str,
        method: str,
        path: str,
        query: dict[str, Any],
        body: dict[str, Any] | None,
    ) -> tuple[int, Any]:
        """Call the app's own API with their sign-in, at a path under its base URL."""
        done = await self._send(
            "POST",
            "/tools/execute/proxy",
            json={
                "connected_account_id": connected_account,
                "endpoint": path,
                "method": method,
                "body": body,
                "parameters": [
                    {"name": k, "value": str(v), "type": "query"} for k, v in query.items()
                ],
            },
        )
        return int(done.get("status", 0)), done.get("data")


def _reason(body: Any) -> str:
    try:
        return str(body["error"]["message"])
    except (KeyError, TypeError):
        return "no reason given"


def _tool(found: dict[str, Any]) -> Tool:
    return Tool(
        slug=found["slug"],
        toolkit=str(cast(dict[str, Any], found.get("toolkit") or {}).get("slug", "")).lower(),
        name=found.get("name", found["slug"]),
        description=found.get("description", ""),
        inputs=found.get("input_parameters") or {},
        tags=tuple(found.get("tags") or ()),
        deprecated=bool(found.get("is_deprecated")),
    )


def args_summary(schema: dict[str, Any], chars: int = 600) -> str:
    """An input schema in a line: name (type, required): what it is."""
    required = set(schema.get("required") or [])
    props = cast(dict[str, dict[str, Any]], schema.get("properties") or {})
    parts = [
        f"{name} ({p.get('type', 'any')}{', required' if name in required else ''}): "
        f"{str(p.get('description', '')).split('. ')[0][:120]}"
        for name, p in props.items()
    ]
    return "; ".join(parts)[:chars] or "none"
