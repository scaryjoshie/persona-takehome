# Persona onboarding take-home

A web simulation of Persona's onboarding. It texts you first, gets you to name it, tries to hop on a voice call to learn your name and one thing you need, connects Gmail, and then shows it's useful by finding something in your inbox that matches what you said. Text is the fallback at every step: you can decline the call, hang up, drop, go quiet, or never pick up, and it carries on by text with full context.

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

The iPhone bezel artwork in `frontend/public/bezels/` is not in git (Apple's assets); without it the phone falls back to a CSS frame.

### Gmail

The link the agent texts opens a small page with two options:

- **Use a demo inbox** — always available; a handful of realistic sample emails.
- **Connect my real Gmail** — shown when `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` are set. The Google project is in Testing mode, so only its test users can connect. It reads the latest ~15 inbox headers (sender, subject, snippet) once, keeps that snapshot, and discards the token. Nothing is sent, deleted, or read again.

## How it works (short version)

- **One event log per user** (SQLite) is the source of truth. Texts, call turns, tool calls, and state changes are all events; both text and voice render the same log, so the agent is one person across channels.
- **Text:** a message waits for a short quiet window (or Jev says it's clearly finished), then one agent run replies with 0–4 bubbles.
- **Calls:** GPT-Live talks and listens. A back-office agent runs on the log after each turn: it records what was said and sends what was asked for or promised (links, spellings), but never decides anything itself. The voice gets short background notes when facts change.
- **Onboarding steps** are objectives (`backend/app/agent/objectives.py`, words in `backend/app/agent/prompts/objectives/`): only the open step's guidance is shown, with scripts reserved for fixed moments.
- Design notes and research: `docs/proposed-design/` (drafts; the code is the current truth).

## Testing

```bash
cd backend
uv run pytest -q && uv run ruff check app tests evals && uv run pyright

# against a running server on :8765 (reads live models; costs a little)
uv run python -m evals.scenarios   # ~40 scripted text edge cases → evals/out/scenarios/
uv run python -m evals.calls       # scripted voice calls (macOS `say`) → evals/out/calls/
uv run python -m evals.simulate    # LLM-played personas + a judge → evals/out/<time>.md
```
