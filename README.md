# Persona onboarding take-home

A web simulation of Persona's onboarding. It texts you first, gets you to name it, tries to hop on a voice call to learn your name and one thing you need, connects Gmail, and then shows it's useful by finding something in your inbox that matches what you said, and can keep working your email and calendar after that. Text is the fallback at every step: you can decline the call, hang up, drop, go quiet, or never pick up, and it carries on by text with full context.

## Run it

```bash
# backend (Python 3.12+, uv)
cd backend
cp .env.example .env        # add OPENAI_API_KEY (required) and OPENROUTER_API_KEY (optional, enables Jev)
uv sync
uv run uvicorn app.main:app --port 8000 --reload --reload-include '*.md'

# frontend (Node 20+, pnpm)
cd frontend
pnpm install
pnpm dev                    # http://localhost:5173, proxies to the backend on :8000
```

Enter any 10-digit number on the first screen; each number is its own user. Chrome is the most reliable for the voice call (mic permission required).

To start everyone over (all conversations and progress, connected Google accounts revoked at Google, voice notes): `uv run python -m app.reset --yes` in `backend/` (without `--yes` it only counts). It backs up the database first and refuses during a live call. On Fly: `fly ssh console -a persona-onboarding -C "python -m app.reset --yes"`.


### Google (Gmail + Calendar)

The link the agent texts goes straight to Google's sign-in. It asks for Gmail (read, draft, send; never permanent delete) and Calendar events. The refresh token is stored encrypted (`CREDENTIALS_KEY`), so the agent can keep searching and reading email, drafting (and, after an explicit yes, sending) replies, and reading and adding calendar events. Tell the agent "disconnect my gmail" to revoke access.

Setup: a Google Cloud OAuth "Web application" client with the redirect URI `{APP_BASE_URL}/api/auth/google/callback`, the Gmail and Calendar APIs enabled, and `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/`CREDENTIALS_KEY` in `backend/.env`. The deployed app is published but not verified by Google: anyone can connect after Google's "unverified app" warning (up to 100 users). In Testing mode instead, only the project's test users can connect.

## How it works (short version)

Diagrams: [`docs/diagrams/routing.png`](docs/diagrams/routing.png) (how any event moves) and [`docs/diagrams/call-architecture.png`](docs/diagrams/call-architecture.png) (a call).

- **One event stream per user** (SQLite) is the source of truth. Texts, call transcript lines, tool calls, timers and the Google sign-in are all events; each is saved, then the Router sends it to whichever side has the floor (text, or voice during a call). Text and voice read the same log, so the agent is one person across channels.
- **Text:** a Jev filtration head decides whether to reply now or wait (debounce, or Jev says the message is clearly finished); then one agent run replies with 0–4 bubbles.
- **Calls:** the GPT-Live Session talks and listens (about 1s replies). It has no tools but hanging up; the **Call Agent** (our text model with the tools) acts during the call. It acts only on two keys: the user asked or agreed, and the voice then said it's on it ("texting you the link"); a Jev Commitment head catches that moment mid-sentence. The voice is kept current through Live's `instructions.append` (the Call Brief: what's known and the next step, sent only between turns), `thinking.append` (facts) and `commentary.append` (things to say now). Call lines are read in spoken order (Live's turn ids), not log order.
- **Onboarding steps** are objectives (`backend/app/agent/objectives.py`, words in `backend/app/agent/prompts/objectives/`): only the open step's guidance is shown, with scripts reserved for fixed moments.
- Design notes and research: `docs/proposed-design/` (drafts from before the build; the code is the current truth).

### Why events

Everything is event-driven and immediately persisted. Events are the unit by which actions are handled. Context is mostly read in a stateless manner.

- **Probably scalable:** this should scale well on serverless, since any instance can handle an event: actions are a function of context + the most recent event + active processes (held statefully now, would move to Redis).
- **Easy routing:** events live in one stream, so routing to the text or call handler is simple control flow.
- **Efficient passing of event deltas:** filtering and categorizing events with Jev is an extremely cheap way to decide whether the expensive work of writing texts should be interrupted and restarted, or whether to prompt it after or just add to context.
- **Very extendable:** more than user texts and calls can come in. Calendar notifications, updates from background agents, or things the agent is monitoring can stream through the same pipeline; Gmail sign-ups already do.

At scale some tiered system may be needed, where events to the main stream are heavily summarized, but this design is reasonable for onboarding. GPT-Live audio does not route through event deltas; its transcripts and actions do.

## Testing

```bash
cd backend
uv run pytest -q && uv run ruff check app tests evals && uv run pyright

# against a running server on :8765 (reads live models; costs a little)
uv run python -m evals.scenarios   # ~40 scripted text edge cases → evals/out/scenarios/
uv run python -m evals.calls       # scripted voice calls (macOS `say`) → evals/out/calls/
uv run python -m evals.simulate    # LLM-played personas + a judge → evals/out/<time>.md
```
