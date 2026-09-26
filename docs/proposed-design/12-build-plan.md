# Build plan

## Stack

- **Python 3.12**, FastAPI, pydantic-ai 2.x (`pydantic-ai-slim[openai,openrouter,openai-realtime]`: OpenRouter for text, OpenAI Realtime for voice), SQLModel over SQLite, `cryptography` for Fernet, pydantic-settings. Tooling: uv, ruff, pyright strict, pytest with asyncio auto mode. Installed 2026-09-25: pydantic-ai-slim 2.51, fastapi 0.141, sqlmodel 0.0.47, openai 3.19.
- **Browser:** a small Vite plus React app served as static files by FastAPI. Only the WebRTC handshake, audio element, phone UI, and a WebSocket back to the server. Everything with logic is Python.
- **One long-running process** (see [11-hosting.md](11-hosting.md)), deployed on Fly.io or similar with a persistent volume for SQLite.
- **Models:** one OpenAI model for the shared agent (`gpt-6-sol` class; exact id to confirm), used directly for text and as the Live delegation backend, so voice is an extension of the text agent (see 15). GPT-Live (`gpt-live-1`) speaks. OpenRouter only for auxiliary work: one-shot tasks, the harness personas. `gpt-realtime-2.1` kept as the voice fallback behind the same interface. Model ids live in tier constants and settings, not scattered.

## Layout

```
backend/                  uv project; pyproject, .env (gitignored), .env.example
  app/                    the one package; no sub-package for "onboarding" since that is all there is
    settings.py           pydantic-settings; nothing else reads the environment
    main.py               FastAPI routes under /api, WebSocket at /ws, static files, actor registry
    actor.py              per-user actor: store cache, queue, consumer task, timers, live realtime handle
    core/                 pure, no I/O, pytest without keys
      types.py            Event envelope, payload union, Delta, Run, Verb, RoutingContext, Slots
      store.py            append-only log + slots, subscribe, write-through hook
      router.py           origin check, floor rule, dispatch to head
      heads.py            text head (debounce policy) and voice head, both taking a Decider
      policy.py           the policy function: fixed cases, then decider
      deciders.py         rule-based decider; Jev decider behind the same Protocol
      views.py            text context view, voice context view, decider view (budgeted), merge rules
    ai/                   cado-style LLM layer
      agent.py            THE shared pydantic-ai Agent: work instructions, tools, deps type; used by both handlers
      tiers.py            named tiers (TEXT_CHAT, VOICE, DECIDER), fallback chains, OpenRouter specs
      prompts/*.md        prompt files loaded as constants; test fails on orphaned files
      schemas.py          structured outputs (bubble list, etc.)
      jev.py              Jev wrapper with context budgeting and timeout fallback
    handlers/
      text.py             pydantic-ai agent: bubbles + tool calls, abortable, deps dataclass
      voice.py            agent.realtime() session on GPT-Live: audio pump to/from /ws/audio, verbs as send(), transcripts, end detection; Realtime fallback
      tools.py            shared tools: set names, record need, send gmail link, skip gmail, start/end call, graduate
    integrations/
      registry.py         provider definitions
      google.py           OAuth start/callback, token storage (encrypted)
      mock_inbox.py       labeled mock data for the first-win moment
    channels/
      base.py             Channel Protocol
      web.py              WebSocket implementation
    db/
      models.py           SQLModel tables
  tests/
    core/                 delta-sequence tests (these become the adversarial harness)
frontend/                 Vite + React phone UI, call screens, debug panel (see 14-frontend-contract.md)
docs/
```

## Build order, in slices that each demo on their own

1. **Core with tests.** Deltas simulated in code, no keys. "Hangs up after giving name" is just a delta sequence, so these tests are the harness seed.
2. **Text-only onboarding end to end.** Phone UI, text handler, tools, slots, debug panel. Completes the whole flow by text, which is the fallback requirement on its own.
3. **Voice.** One-day spike on GPT-Live first (connect, stream mic, see transcripts, `send()` a note, close). Then call screens, the audio relay socket, injection, hang-up handling. Fallback to Realtime if the spike fails.
4. **Gmail.** OAuth, the connected event injected mid-call, the mock-inbox moment.
5. **Polish.** Graduation path, prompt tuning, stress scripts, README and write-up, video.

Keep a short decisions file in the repo from the start and append as we go; it is the seed of the write-up.

## Conventions borrowed from cado backend-2 (style, not a verbatim copy)

Survey of `~/dev/cado/backend-2` on 2026-09-25, a pydantic-ai 1.107 stack with a domain-agnostic `libs/llm` library.

Adopt:

- **Tiers and specs.** Named tier constants in code, each an ordered fallback chain of model specs with per-member reasoning level and temperature. Missing API key fails loudly at agent build time.
- **Three-error contract:** input rejected, invalid output, unavailable. Nothing above the library handles anything else.
- **Lazy agent construction** so keys are read at call time; tests override with pydantic-ai's `TestModel` / `FunctionModel`.
- **Prompts as markdown files** loaded once into named constants, with a test that fails on orphaned files. Context views are assemblers over those constants.
- **Deps as a dataclass** carrying a session factory and a publish callback, not a live session.
- **Streaming via the agent's iteration API with a callback**, not an async generator, because of anyio cancel-scope issues. This matters for our interrupt verb, which cancels a running generation.
- **Fire-and-forget tasks held in a strong-ref set**, and a wall-clock cap on any run.
- **Note rows** injected into replayed history for things that happened between turns. Our channel-tagged system events are the same idea.
- Logfire instrumentation for traces during stress testing.

Write fresh (absent there): the append-only per-user event log, a token-budgeted context renderer for a small classifier (the Jev wrapper), the realtime handler.

## Submission shape

- Deployed link, repo, README.
- Live debug panel beside the phone showing slots, floor, call status, and each delta's verb and who decided it.
- Adversarial tests reported as pass rates (from the core delta-sequence tests plus LLM-played personas against the text flow).
- Short video: happy path; messy path with a hang-up and a declined Gmail; early graduation.
- Design write-up: value first and collect opportunistically; the store-first architecture and why; what was learned from Persona's real onboarding; edge cases handled; next steps (resume days later, drop-off per step, Durable Objects, self-building integrations).
