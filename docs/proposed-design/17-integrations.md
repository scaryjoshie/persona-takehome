# Integrations

Agreed 2026-09-27 with Joshua; not built yet. Builds on background jobs ([16](16-background-agents.md)). Google stays its own hand-built section (app/google/); this is for everything else.

## What an integration is

A per-user data row the agent creates and maintains itself, not code per app. Code holds only a small packaged list of *parts*; each app is a combination of them, written as data.

```python
class Integration(BaseModel):
    app: str                       # "slack", "canvas", "doordash"
    account: str = ""              # which workspace or account, if they have two
    notes: str = ""                # how to use it for this user: what worked, quirks (capped, rewritten not appended)
    status: Literal["setting_up", "ready", "broken"] = "setting_up"
    auth: list[ComposioConnection | OAuthTokens | ApiKey | BrowserLogin]   # how we're let in
    api: ComposioApi | McpApi | HttpApi | None = None                     # an API to act through, if any
```

Each part is a small typed model with its own `kind` (a discriminated union, like events). Parts are added rarely and deliberately; apps are added as data with no code.

| Part | Holds | Tools it contributes |
|---|---|---|
| `ComposioConnection` + `ComposioApi` | Composio's connection id, the toolkit ("SLACK") | find/run actions from Composio's catalog, run on Composio's side |
| `OAuthTokens` + `McpApi` | tokens (vault), the server URL | the MCP server's own tools |
| `ApiKey` + `HttpApi` | a key or webhook (vault); base URL, allowed hosts (fixed at creation), endpoints marked read or act | one tool per endpoint; the executor fills `{secret:name}` in headers or paths |
| `BrowserLogin` | site, Kernel profile id, how login works (hosted page, email code, sms code) | `browse(task)` scoped to that site and login |

The browser is a capability, not an integration: any job can browse public pages. A saved login is one kind of credential an integration can hold.

## Storage

One `integration` table: columns only for what code filters on (id, phone, app, account, status, created/updated/verified), the rest in a JSON `data` column validated back into the model on read, and secret values in a separate encrypted column (Fernet, `CREDENTIALS_KEY`), never in `data`, an event, or a prompt. The agent names the secrets it needs; it never sees their values. Strict on write, tolerant on read (unknown fields ignored), new fields get defaults, so old rows keep loading.

## Where things live

- **How to use the service** (for this user): the integration's `notes`, written by jobs from what actually worked.
- **Their preferences** ("usual order: pad see ew, no peanuts"): memory, as facts with `app=`. Loaded only when that app is in use, never into the chat agent's everyday prompt. Saved only when they said or confirmed it.
- **What happened**: `ToolCall(app=…, job=…)` events recorded by the runner, and a `past_actions(app, n)` tool for jobs. The service's own history (order history, message history) is often the better source.

## Who uses them

Background jobs do all the work. The chat agent sees one line per connected app and starts jobs; it never loads integration tools or notes. A job gets every ready integration's tools and notes. Setting one up ("connect my Canvas") and repairing a broken one are jobs too: research, pick the parts, write the row, get the credential (a connect link, a one-time secure form, or a Kernel login), prove it with one harmless read, mark it ready.

## Rules

- Writes need their yes, enforced in code: every tool is read or act (unknown means act); an act pauses the job (pydantic-ai approval) and the question reaches them like any job question; a yes applies to that one pending call.
- Troubleshooting may look freely; anything that changes something outside our own rows (log in again, new token, re-authorize, account settings) is a question to them first.
- Browser: approval is per task, not per click; the browser agent's instructions forbid account or password changes without a yes to that exact change.
- Notes written by an agent that read web pages are notes, not rules: they can't widen allowed hosts or turn an act into a read.

## Separation

All of it lives in `app/integrations/`. The rest of the app touches it at three points: one line adding the integrations toolset to jobs (and `connect_app` for the chat agent, which starts a setup job), one "connected apps" line in "What you know", and events through the pipeline.

## Build order

1. The model, table, vault and the `ApiKey` + `HttpApi` parts (Slack incoming webhooks, Canvas access tokens): no vendor account needed. Setup job, secure form, approvals, `past_actions`.
2. Composio (needs a key), then MCP, then `BrowserLogin` with Kernel (needs a key).
