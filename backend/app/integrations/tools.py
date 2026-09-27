"""The tools a background job gets for integrations, built fresh for each run.

- Setting one up: save_integration, request_secret (they get a secure link; the job waits),
  try_endpoint (reads only), mark_ready.
- Using one: a tool per endpoint of each ready integration. Reads run; acts pause the job for
  their yes (pydantic-ai approval), with the question the job wrote.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx
from pydantic_ai import ApprovalRequired, CallDeferred, RunContext
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset

from app.integrations import http
from app.integrations.integration import Endpoint, Integration
from app.integrations.store import Integrations, SecretRequest
from app.jobs.agent import JobDeps

ToolsetsFor = Callable[[JobDeps], Awaitable[Sequence[AbstractToolset[JobDeps]]]]


def job_toolsets(integrations: Integrations, client: httpx.AsyncClient) -> ToolsetsFor:
    async def build(deps: JobDeps) -> Sequence[AbstractToolset[JobDeps]]:
        tools = _setup_tools(integrations, client)
        for integration in await integrations.all(deps.phone):
            if integration.status == "ready" and integration.api is not None:
                for endpoint in integration.api.endpoints:
                    _add_endpoint(tools, integrations, client, integration, endpoint)
        return [tools]

    return build


def _setup_tools(integrations: Integrations, client: httpx.AsyncClient) -> FunctionToolset[JobDeps]:
    tools = FunctionToolset[JobDeps]()

    @tools.tool
    async def connected_services(ctx: RunContext[JobDeps]) -> str:
        """Their integrations: id, status, notes, which secrets are saved, what each can do."""
        lines: list[str] = []
        for i in await integrations.all(ctx.deps.phone):
            missing = await integrations.missing(ctx.deps.phone, i)
            ends = [
                f"{e.name} ({e.method}, {e.effect}): {e.about}"
                for e in (i.api.endpoints if i.api else [])
            ]
            lines.append(
                f"[{i.id}] {i.app}{f' ({i.account})' if i.account else ''}: {i.status}; "
                f"secrets missing: {', '.join(missing) or 'none'}; "
                f"endpoints: {'; '.join(ends) or 'none'}"
                + (f"\nnotes: {i.notes}" if i.notes else "")
            )
        return "\n".join(lines) or "none yet"

    @tools.tool
    async def save_integration(ctx: RunContext[JobDeps], integration: Integration) -> str:
        """Create or update an integration: how they give access (auth: the secrets they create,
        like an API key, access token or webhook URL), the API (https base URL, the only hosts it
        needs, headers, endpoints with {param} and {secret:name} placeholders, each a read or an
        act), and notes on how to use it. Start with status setting_up. Never put a secret's
        value here; name it in auth and ask for it with request_secret."""
        try:
            saved = await integrations.save(ctx.deps.phone, integration)
        except ValueError as exc:
            return f"not saved: {exc}"
        missing = await integrations.missing(ctx.deps.phone, saved)
        return f"saved as {saved.id}; secrets still needed: {', '.join(missing) or 'none'}"

    @tools.tool
    async def request_secret(ctx: RunContext[JobDeps], integration_id: str, secret: str) -> str:
        """Text them a secure link to paste one of the integration's secrets. It goes straight
        into an encrypted store; you never see it. You wait until they've saved it. A secret
        only ever comes this way, including a new one when a saved one didn't work."""
        if not integrations.configured or integrations.text is None:
            return (
                "secrets can't be stored here (no CREDENTIALS_KEY); tell them it's not possible yet"
            )
        found = await integrations.get(ctx.deps.phone, integration_id)
        part = next((a for a in (found.auth if found else []) if a.secret == secret), None)
        if found is None or part is None or ctx.tool_call_id is None:
            return f"no secret {secret} on integration {integration_id}; check connected_services"
        link = integrations.link_for(
            SecretRequest(
                phone=ctx.deps.phone,
                integration=found.id,
                name=secret,
                about=part.about,
                app=found.app,
                job=ctx.deps.job,
                question=ctx.tool_call_id,
            )
        )
        await integrations.text(ctx.deps.phone, link)
        raise CallDeferred(
            metadata={
                "kind": "secret",
                "question": f"They were just texted a secure link to paste their {part.about} "
                f"for {found.app}. Tell them it's there; it carries on once they've saved it.",
            }
        )

    @tools.tool
    async def try_endpoint(
        ctx: RunContext[JobDeps], integration_id: str, endpoint: str, args: dict[str, Any]
    ) -> str:
        """Call one of an integration's read endpoints, to check it works while setting it up."""
        found = await integrations.get(ctx.deps.phone, integration_id)
        target = _endpoint(found, endpoint)
        if found is None or found.api is None or target is None:
            return f"no endpoint {endpoint} on {integration_id}"
        if not target.reads:
            return f"{endpoint} changes something; it can't be tried, only used once they say yes"
        return await _call(integrations, client, found, target, args, ctx.deps.phone)

    @tools.tool
    async def mark_ready(ctx: RunContext[JobDeps], integration_id: str, notes: str) -> str:
        """It's set up (its secrets are saved and a read worked, if it has one). `notes`: how to
        use it for them, short: what worked, quirks, what it can't do."""
        found = await integrations.get(ctx.deps.phone, integration_id)
        if found is None:
            return f"no integration {integration_id}"
        missing = await integrations.missing(ctx.deps.phone, found)
        if missing:
            return f"not ready: still needs {', '.join(missing)}"
        await integrations.save(
            ctx.deps.phone, found.model_copy(update={"status": "ready", "notes": notes[:3000]})
        )
        return f"{found.app} is connected"

    return tools


def _add_endpoint(
    tools: FunctionToolset[JobDeps],
    integrations: Integrations,
    client: httpx.AsyncClient,
    integration: Integration,
    endpoint: Endpoint,
) -> None:
    name = f"{integration.app}_{endpoint.name}"[:64]
    params = "; ".join(
        f"{p.name}{'' if p.required else ' (optional)'}: {p.about}" for p in endpoint.params
    )
    about = f"{integration.app}: {endpoint.about}" + (f" Arguments: {params}." if params else "")
    notes = f" Notes: {integration.notes}" if integration.notes else ""

    if endpoint.reads:

        async def read(ctx: RunContext[JobDeps], args: dict[str, Any]) -> str:
            return await _call(integrations, client, integration, endpoint, args, ctx.deps.phone)

        tools.add_function(read, name=name, description=about + notes)
        return

    async def act(ctx: RunContext[JobDeps], args: dict[str, Any], ask_them: str) -> str:
        if not ctx.tool_call_approved:
            raise ApprovalRequired(metadata={"question": ask_them})
        return await _call(integrations, client, integration, endpoint, args, ctx.deps.phone)

    tools.add_function(
        act,
        name=name,
        description=f"{about} It changes something, so it asks them first: `ask_them` is the "
        f"yes-or-no question, with the specifics (exactly what, where).{notes}",
    )


def _endpoint(integration: Integration | None, name: str) -> Endpoint | None:
    if integration is None or integration.api is None:
        return None
    return next((e for e in integration.api.endpoints if e.name == name), None)


async def _call(
    integrations: Integrations,
    client: httpx.AsyncClient,
    integration: Integration,
    endpoint: Endpoint,
    args: dict[str, Any],
    phone: str,
) -> str:
    assert integration.api is not None
    secrets = await integrations.secrets(phone, integration.id)
    try:
        return await http.call(client, integration.api, endpoint, args, secrets)
    except http.Refused as exc:
        return f"not called: {exc}"
    except httpx.HTTPError as exc:
        return http.scrub(f"the request failed: {type(exc).__name__}: {exc}", secrets)
