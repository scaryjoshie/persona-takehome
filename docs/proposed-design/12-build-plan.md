# Build plan

## Stack

- **Python 3.12**, FastAPI, pydantic-ai 2.x (`pydantic-ai-slim[openai,openrouter,openai-realtime]`: OpenRouter for text, OpenAI Realtime for voice), SQLModel over SQLite, `cryptography` for Fernet, pydantic-settings. Tooling: uv, ruff, pyright strict, pytest with asyncio auto mode. Installed 2026-09-25: pydantic-ai-slim 2.51, fastapi 0.141, sqlmodel 0.0.47, openai 3.19.
- **Browser:** a small Vite plus React app served as static files by FastAPI. Only the WebRTC handshake, audio element, phone UI, and a WebSocket back to the server. Everything with logic is Python.
- **One long-running process** (see [11-hosting.md](11-hosting.md)), deployed on Fly.io or similar with a persistent volume for SQLite.
- **Models:** one OpenAI model for the shared agent (`gpt-6-sol` class; exact id to confirm), used directly for text and as the Live delegation backend, so voice is an extension of the text agent (see 15). GPT-Live (`gpt-live-1`) speaks. OpenRouter only for auxiliary work: one-shot tasks, the harness personas. `gpt-realtime-2.1` kept as the voice fallback behind the same interface. Model ids live in tier constants and settings, not scattered.

## Layout (as built, 2026-09-26, after the restructure)

```
backend/app/
  main.py        assemble (pipeline + the two media), from_settings, and the FastAPI app
  pipeline.py    submit: save (applying state changes), publish, hand to the medium with the floor
  payloads.py    every event kind in one union
  jev.py         Jev client (OpenRouter Decisions API); returns None on failure
  services.py    what routes get from the app state
  database.py  settings.py  timers.py  cli.py
  events/   payload.py  event.py  decision.py  models.py  service.py
  users/    models.py  user.py (User, Medium)  service.py
  gmail/    events.py
  text/     events.py (incl. ReplyDue, ReplyStarted)  timing.py (pure)  responder.py  reply.py  voice_notes.py
  voice/    events.py  call_state.py (CallEvent, CallState, next_state)  responder.py (VoiceResponder, LiveCall, call_note)  call.py (one call: audio, captions, call agent)  routes.py
  agent/    agent.py  deps.py (incl. Messenger)  prompts.py + prompts/*.md  objectives.py  context.py  slots.py  events.py  model.py
  web/      protocol.py (every browser message)  routes.py  sockets.py  schema.py
  previews/ fetch.py  routes.py
```

The medium contract is one method, `handle(event, user, ctx) -> Decision | None`. Each medium owns its own vocabulary. The text medium is stateless (decisions come from the log and the user row); the voice medium's only in-process state is the live call.

## Build order, in slices that each demo on their own

1. **Core with tests.** *Done 2026-09-25, restructured 2026-09-26 (44 tests): sections own their types and rendering; SQLite is the only data store; runtime holds live things only; actions own transactions.*
2a. **Shared agent + text handler.** *Done 2026-09-26: markdown prompts, seven tools, bubble delivery, CLI; smoke-tested on the real model.* Deltas simulated in code, no keys. "Hangs up after giving name" is just a delta sequence, so these tests are the harness seed.
2b. **Text-only onboarding end to end.** *Backend done 2026-09-26: session endpoint, browser WebSocket, web messenger, schema export, static serving; verified against the real model over a real socket. Remaining: point the frontend at it.* Phone UI, text handler, tools, slots, debug panel. Completes the whole flow by text, which is the fallback requirement on its own.
3. **Voice.** *Built 2026-09-26: `/ws/audio`, GPT-Live session via pydantic-ai, captions, utterances, call agent, greeting, hang-up from any side. Verified with synthesized speech. Not built: the gpt-realtime fallback.* One-day spike on GPT-Live first (connect, stream mic, see transcripts, `send()` a note, close). Then call screens, the audio relay socket, injection, hang-up handling. Fallback to Realtime if the spike fails.
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
