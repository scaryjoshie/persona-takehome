"""What a background job gets for integrations, built fresh for each run.

- Setting one up: save_integration, request_secret (they get a secure link; the job waits),
  try_endpoint (reads only), mark_ready.
- An app they sign in to (Composio, when configured): app_actions (the catalog's actions for
  a task, each a read or an act by the catalog's own label), connect_app (texts the sign-in
  link; the job waits) and app_run. A few tools whatever the catalog's size.
- Using one: a tool per endpoint of each ready integration, or app_run for a signed-in app.
  Reads run; acts pause the job for their yes (pydantic-ai approval), with the question the
  job wrote. Every call is recorded as a compact ToolCall event with its app; past_actions
  reads them back.
- What to know: each connected service's notes, and what they like there (remembered facts
  about that app), so preferences load only when that service is in play.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from pydantic_ai import ApprovalRequired, CallDeferred, RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.agent.events import ToolCall
from app.events.payload import Channel, Origin
from app.integrations import http, templates
from app.integrations.composio import Composio, ComposioError, Tool, args_summary
from app.integrations.integration import (
    Action,
    ApiKey,
    ComposioApi,
    ComposioConnection,
    Endpoint,
    HttpApi,
    Integration,
)
from app.integrations.store import Integrations, SecretRequest, SignIn
from app.jobs.agent import JobDeps
from app.jobs.runner import Extras, ExtrasFor

SHOWN = 300  # characters of a result kept in the log


def job_extras(integrations: Integrations, client: httpx.AsyncClient) -> ExtrasFor:
    async def build(deps: JobDeps) -> Extras:
        tools = _setup_tools(integrations, client)
        if integrations.composio is not None:
            _app_tools(tools, integrations, integrations.composio)
        known: list[str] = []
        for integration in await integrations.all(deps.phone):
            api = integration.api
            if integration.status != "ready" or api is None:
                continue
            runs = ""
            if isinstance(api, HttpApi):
                for endpoint in api.endpoints:
                    _add_endpoint(tools, integrations, client, integration, endpoint)
            else:
                runs = f" Use it with app_run on {integration.id}: " + "; ".join(
                    [f"{a.slug} ({a.effect}), inputs: {a.args}" for a in api.actions]
                    + [f"{e.name} ({e.method}, {e.effect}): {e.about}" for e in api.endpoints]
                )
            likes = [f.text for f in await deps.pipeline.facts(deps.phone, app=integration.app)]
            known.append(
                f"- {integration.app}: {integration.notes or 'no notes yet'}{runs}"
                + (f" What they like there: {'; '.join(likes)}" if likes else "")
            )
        about = "Services they connected:\n" + "\n".join(known) if known else ""
        return Extras(toolsets=[tools], instructions=about)

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
            if isinstance(i.api, ComposioApi):
                ends = [f"{a.slug} ({a.effect}): {a.about}" for a in i.api.actions] + ends
            lines.append(
                f"[{i.id}] {i.app}{f' ({i.account})' if i.account else ''}: {i.status}; "
                f"secrets missing: {', '.join(missing) or 'none'}; "
                f"endpoints: {'; '.join(ends) or 'none'}"
                + (f"\nnotes: {i.notes}" if i.notes else "")
            )
        return "\n".join(lines) or "none yet"

    async def use_template(
        ctx: RunContext[JobDeps], template: str, host: str = "", account: str = ""
    ) -> str:
        try:
            built = templates.build(template, host=host, account=account)
        except (KeyError, ValueError) as exc:
            return f"not saved: {exc}"
        saved = await integrations.save(ctx.deps.phone, built)
        missing = await integrations.missing(ctx.deps.phone, saved)
        return f"saved as {saved.id}; secrets still needed: {', '.join(missing) or 'none'}"

    tools.add_function(
        use_template,
        description="Set up a common service from a known-good template, instead of "
        f"designing it: {templates.summary()}. `host`: only for templates that need it. "
        "Then ask for its secret with request_secret.",
    )

    @tools.tool
    async def save_integration(ctx: RunContext[JobDeps], integration: Integration) -> str:
        """Create or update an integration: how they give access (auth: the secrets they create,
        like an API key, access token or webhook URL), the API (https base URL, the only hosts it
        needs, headers, endpoints with {param} and {secret:name} placeholders, each a read or an
        act), and notes on how to use it. Start with status setting_up. Never put a secret's
        value here; name it in auth and ask for it with request_secret."""
        if isinstance(integration.api, ComposioApi) or integration.composio is not None:
            return "not saved: an app they sign in to is set up with connect_app"
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
        keys = [a for a in (found.auth if found else []) if isinstance(a, ApiKey)]
        part = next((a for a in keys if a.secret == secret), None)
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
        if not found.reads(target):
            return f"{endpoint} changes something; it can't be tried, only used once they say yes"
        return await _call(integrations, client, found, target, args, ctx.deps)

    @tools.tool
    async def past_actions(ctx: RunContext[JobDeps], app: str, n: int = 10) -> str:
        """What was done in a connected service before (by any background task), newest last."""
        done = [
            e.payload
            for e in await ctx.deps.pipeline.history(ctx.deps.phone)
            if isinstance(e.payload, ToolCall) and e.payload.app == app
        ][-n:]
        return (
            "\n".join(
                f"{c.name}({c.args}){'' if c.ok else ' (failed)'}: {c.shown or ''}" for c in done
            )
            or "nothing yet"
        )

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
        if found.composio is not None:
            unproven = await _unproven(integrations, found, ctx.deps)
            if unproven:
                return f"not ready: {unproven}"
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

    if integration.reads(endpoint):

        async def read(ctx: RunContext[JobDeps], args: dict[str, Any]) -> str:
            return await _call(integrations, client, integration, endpoint, args, ctx.deps)

        tools.add_function(read, name=name, description=about + notes)
        return

    async def act(ctx: RunContext[JobDeps], args: dict[str, Any], ask_them: str) -> str:
        if not ctx.tool_call_approved:
            raise ApprovalRequired(metadata={"question": ask_them})
        return await _call(integrations, client, integration, endpoint, args, ctx.deps)

    tools.add_function(
        act,
        name=name,
        description=f"{about} It changes something, so it asks them first: `ask_them` is the "
        f"yes-or-no question, with the specifics (exactly what, where).{notes}",
    )


def _endpoint(integration: Integration | None, name: str) -> Endpoint | None:
    if integration is None or not isinstance(integration.api, HttpApi):
        return None
    return next((e for e in integration.api.endpoints if e.name == name), None)


async def _call(
    integrations: Integrations,
    client: httpx.AsyncClient,
    integration: Integration,
    endpoint: Endpoint,
    args: dict[str, Any],
    deps: JobDeps,
) -> str:
    """Call it, and record what happened as a compact event (their args, a short result; the
    secrets were filled in by us and blanked out of the result)."""
    assert isinstance(integration.api, HttpApi)
    secrets = await integrations.secrets(deps.phone, integration.id)
    try:
        reply = await http.call(client, integration.api, endpoint, args, secrets)
        out, ok = reply.text, reply.ok
    except http.Refused as exc:
        return f"not called: {exc}"
    except httpx.HTTPError as exc:
        out, ok = http.scrub(f"the request failed: {type(exc).__name__}: {exc}", secrets), False
    else:
        if reply.rejected:  # the key expired or was revoked: it needs a new one
            await integrations.save(deps.phone, integration.model_copy(update={"status": "broken"}))
            out += "\nTheir access was refused; ask them for a new one with request_secret."
    await _record(deps, integration, f"{integration.app}_{endpoint.name}", args, out, ok)
    return out


async def _record(
    deps: JobDeps, integration: Integration, name: str, args: dict[str, Any], out: str, ok: bool
) -> None:
    call = ToolCall(
        name=name, args=args, shown=out[:SHOWN], app=integration.app, ok=ok, job=deps.job
    )
    await deps.pipeline.submit(deps.phone, Origin.JOB, Channel.SYSTEM, call)


# ---- apps they sign in to (Composio) -------------------------------------------------------

ABOUT = 250  # characters of a catalog description shown


def _app_tools(
    tools: FunctionToolset[JobDeps], integrations: Integrations, composio: Composio
) -> None:
    @tools.tool
    async def app_actions(ctx: RunContext[JobDeps], app: str, what: str) -> str:
        """What an app they sign in to (OAuth) can do, from Composio's catalog: the actions for
        `what` ("post a message to a channel"), each a read or an act, with its inputs, and the
        catalog's plan and known pitfalls. `app`: the catalog's name ("slack", "notion",
        "googledrive"). Only what this returns exists."""
        try:
            toolkit = await composio.toolkit(app.lower())
            if toolkit is None:
                return f"{app} isn't in the catalog"
            if not toolkit.signs_in:
                return (
                    f"{toolkit.name} has no sign-in here; if it takes a key, use save_integration"
                )
            user = await integrations.composio_user(ctx.deps.phone)
            found = await composio.search(user, toolkit.slug, what)
            results: list[dict[str, Any]] = found.get("results") or [{}]
            result = results[0]
            slugs = list(
                dict.fromkeys(
                    result.get("primary_tool_slugs", []) + result.get("related_tool_slugs", [])
                )
            )
            listed = await composio.tools(toolkit.slug, toolkit.version, slugs)
        except ComposioError as exc:
            return str(exc)
        # Only this app's current actions: search can return other apps' lookalikes.
        ours = [t for t in listed if t.toolkit == toolkit.slug and not t.deprecated]
        lines = [f"{toolkit.name} (catalog version {toolkit.version}), for {what!r}:"]
        lines += [
            f"- {t.slug} ({'read' if t.reads else 'act'}) {t.name}: "
            f"{t.description[:ABOUT]} Inputs: {args_summary(t.inputs)}"
            for t in sorted(ours, key=lambda t: slugs.index(t.slug) if t.slug in slugs else 99)
        ] or ["- nothing in the catalog for that"]
        for title, key in (("Plan", "recommended_plan_steps"), ("Pitfalls", "known_pitfalls")):
            if result.get(key):
                lines += [f"{title}:", *(f"- {step}" for step in result[key])]
        return "\n".join(lines)

    @tools.tool
    async def connect_app(
        ctx: RunContext[JobDeps],
        app: str,
        actions: list[str],
        endpoints: list[Endpoint] | None = None,
        account: str = "",
    ) -> str:
        """Set up an app they sign in to, with the catalog actions you'll use (slugs from
        app_actions, including a read to check it with), and only for what the catalog lacks,
        `endpoints` of the app's own API (paths under it, like "/users/me"). Texts them the
        sign-in link and waits until they're back. If it returns what they said instead (they
        missed the link, or say they're done), call it again: it carries on if they're signed
        in, and otherwise points them to the link they have or texts a fresh one once that's
        expired. Call it again to change the actions too."""
        phone, app = ctx.deps.phone, app.lower()
        try:
            toolkit = await composio.toolkit(app)
            if toolkit is None or not toolkit.signs_in:
                return f"{app} can't be signed in to here; check app_actions"
            name = re.sub(r"[^a-z0-9_]", "_", toolkit.slug)[:40]
            existing = next(
                (i for i in await integrations.all(phone) if (i.app, i.account) == (name, account)),
                None,
            )
            if existing is not None and existing.composio is None:
                return f"{name} is already set up another way ({existing.id}); pass an account"
            part = existing.composio if existing else None
            before = existing.api if existing and isinstance(existing.api, ComposioApi) else None
            version = before.version if before else toolkit.version
            found = await composio.tools(toolkit.slug, version, actions)
            chosen = {t.slug: t for t in found if t.toolkit == toolkit.slug and not t.deprecated}
            unknown = [a for a in actions if a not in chosen]
            if unknown:
                return f"not saved: not {toolkit.name} actions in the catalog: {', '.join(unknown)}"
            if part is None:
                user = await integrations.composio_user(phone)
                config = await composio.new_auth_config(toolkit.slug, f"{name} for {user}")
                part = ComposioConnection(toolkit=toolkit.slug, user_id=user, auth_config_id=config)
            await composio.allow(part.auth_config_id, list(chosen))
            integration = Integration(
                id=existing.id if existing else "",
                app=name,
                account=account,
                notes=existing.notes if existing else "",
                status=existing.status if existing else "setting_up",
                auth=[part],
                api=ComposioApi(
                    toolkit=toolkit.slug,
                    version=version,
                    actions=[_action(chosen[a]) for a in actions],
                    endpoints=endpoints or [],
                ),
            )
            saved = await integrations.save(phone, integration)
            if (
                part.connected_account_id
                and await composio.status(part.connected_account_id) == "ACTIVE"
            ):
                return f"saved as {saved.id}; they're already signed in"
            if integrations.text is None or ctx.tool_call_id is None:
                return f"saved as {saved.id}, but they can't be texted a link from here"
            if integrations.rewait(saved.id, ctx.deps.job, ctx.tool_call_id):
                raise CallDeferred(
                    metadata={
                        "kind": "connect",
                        "question": f"The {toolkit.name} sign-in link they were texted a few "
                        "minutes ago still works. Point them to it; it carries on once they've "
                        "signed in.",
                    }
                )
            sign_in = SignIn(phone, saved.id, toolkit.name, ctx.deps.job, ctx.tool_call_id)
            link = await composio.link(
                part.auth_config_id, part.user_id, integrations.callback_for(sign_in)
            )
        except (ComposioError, ValueError) as exc:
            return f"not connected: {exc}"
        signed = part.model_copy(update={"connected_account_id": link.connected_account})
        await integrations.save(phone, saved.model_copy(update={"auth": [signed]}))
        await integrations.text(phone, link.url)
        raise CallDeferred(
            metadata={
                "kind": "connect",
                "question": f"They were just texted a link to sign in to {toolkit.name}. Tell "
                "them it's there; it carries on once they've signed in.",
            }
        )

    @tools.tool
    async def app_run(
        ctx: RunContext[JobDeps],
        integration_id: str,
        action: str,
        args: dict[str, Any],
        ask_them: str = "",
    ) -> str:
        """Run one of a signed-in app's actions (its slug) or endpoints (its name). A read just
        runs. An act changes something, so it asks them first: `ask_them` is the yes-or-no
        question, with the specifics (exactly what, where). Look ids up with reads; never guess."""
        found = await integrations.get(ctx.deps.phone, integration_id)
        api, part = (found.api, found.composio) if found else (None, None)
        if found is None or not isinstance(api, ComposioApi) or part is None:
            return f"no signed-in app {integration_id}; check connected_services"
        if not part.connected_account_id:
            return "they haven't signed in yet (connect_app)"
        chosen = next((a for a in api.actions if a.slug == action.upper()), None)
        target = chosen or next((e for e in api.endpoints if e.name == action), None)
        if target is None:
            return f"{action} isn't one of its actions; add it with connect_app"
        reads = found.reads(target) if isinstance(target, Endpoint) else target.effect == "read"
        if not reads:
            if not ask_them:
                return f"{action} changes something: pass ask_them, the question for their yes"
            if not ctx.tool_call_approved:
                raise ApprovalRequired(metadata={"question": ask_them})
        name = target.slug if isinstance(target, Action) else f"{found.app}_{target.name}"
        try:
            if isinstance(target, Action):
                ok, data = await composio.execute(
                    target.slug,
                    user=part.user_id,
                    connected_account=part.connected_account_id,
                    version=api.version,
                    args=args,
                )
            else:
                ok, data = await _proxy(composio, part, target, args)
            out = http.compact(data)
        except http.Refused as exc:
            return f"not called: {exc}"
        except ComposioError as exc:
            ok, out = False, str(exc)
        if not ok:
            try:
                state = await composio.status(part.connected_account_id)
            except ComposioError:
                state = ""
            if state and state != "ACTIVE":  # their sign-in lapsed or was revoked
                await integrations.save(
                    ctx.deps.phone, found.model_copy(update={"status": "broken"})
                )
                out += f"\nTheir sign-in is {state}; connect_app again texts them a new link."
        await _record(ctx.deps, found, name, args, out, ok)
        return out


def _action(tool: Tool) -> Action:
    """Read or act is the catalog's label for the pinned version, never the job's say."""
    return Action(
        slug=tool.slug,
        about=tool.name,
        args=args_summary(tool.inputs),
        effect="read" if tool.reads else "act",
    )


async def _proxy(
    composio: Composio, part: ComposioConnection, endpoint: Endpoint, args: dict[str, Any]
) -> tuple[bool, Any]:
    """An endpoint through Composio's proxy: always a path under the app's own API, so their
    sign-in never goes to another host."""
    path, query, body, form = http.fill_params(endpoint, args)
    if not path.startswith("/") or path.startswith("//") or "://" in path:
        raise http.Refused("an endpoint is a path under the app's API, like /users/me")
    if form:
        raise http.Refused("form fields can't go through the proxy; send JSON")
    status, data = await composio.proxy(
        connected_account=part.connected_account_id,
        method=endpoint.method,
        path=path,
        query=query,
        body=body or None,
    )
    return 200 <= status < 300, {"status": status, "data": data}


async def _unproven(integrations: Integrations, found: Integration, deps: JobDeps) -> str:
    """Why a signed-in app isn't connected yet: it is once Composio says the account is ACTIVE
    and one of its reads worked in this job."""
    part, composio = found.composio, integrations.composio
    if part is None or composio is None or not part.connected_account_id:
        return "they haven't signed in (connect_app)"
    try:
        state = await composio.status(part.connected_account_id)
    except ComposioError as exc:
        return str(exc)
    if state != "ACTIVE":
        return f"their sign-in is {state}"
    api = found.api
    reads: set[str] = (
        {a.slug for a in api.actions if a.effect == "read"}
        | {f"{found.app}_{e.name}" for e in api.endpoints if found.reads(e)}
        if isinstance(api, ComposioApi)
        else set()
    )
    worked = any(
        isinstance(e.payload, ToolCall)
        and e.payload.job == deps.job
        and e.payload.ok
        and e.payload.name in reads
        for e in await deps.pipeline.history(deps.phone)
    )
    return "" if worked else "check it with one read first (app_run)"
