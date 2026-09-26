# Proposed design (draft, not final)

> **Take with a grain of salt.** This folder captures a design discussion from 2026-09-25. Nothing here is final. Several choices may turn out to be wrong or not optimal once code exists and the flow has been stress-tested. Treat every file as a proposal to be argued with, not a spec.

## What this is

Design notes for the Persona take-home: a web simulation of Persona's onboarding that collects an agent name (text, before the call), then the user's name, a connected Gmail, and something they need help with (attempted over a voice call, with text as the fallback). The onboarding must survive users who break the rules, must not feel like a form, and should let users who already know what they want graduate early.

## Files

| File | Contents |
|---|---|
| [01-assignment.md](01-assignment.md) | The brief, verbatim, and how it will likely be graded |
| [02-persona-observations.md](02-persona-observations.md) | Notes from using Persona's real onboarding, and what each problem implies |
| [03-architecture.md](03-architecture.md) | Store-first event model, the floor, router, heads, handlers, what runs where |
| [04-events-and-types.md](04-events-and-types.md) | Event envelope, payload union, slots, storage unit, rendering and merging |
| [05-routing-and-decider.md](05-routing-and-decider.md) | Deltas, policy function, routing context, runs, verbs per medium, Jev |
| [06-turn-taking-policy.md](06-turn-taking-policy.md) | Debounce, hold, multi-bubble delivery, cancel vs finish |
| [07-voice-realtime.md](07-voice-realtime.md) | OpenAI Realtime over WebRTC with a server sideband, tools, transcripts, disconnects |
| [07b-gpt-live-option.md](07b-gpt-live-option.md) | Alternative voice layer: GPT-Live 1 with client delegation, and how it changes the design |
| [08-storage.md](08-storage.md) | SQLModel tables, in-memory cache with write-through, credentials encryption |
| [09-protocol.md](09-protocol.md) | HTTP and WebSocket protocol, channel port, call state machine |
| [10-gmail-and-integrations.md](10-gmail-and-integrations.md) | OAuth facts, the mock-inbox decision, provider registry, self-building integrations |
| [11-hosting.md](11-hosting.md) | Long-running process vs serverless, multi-user, costs |
| [12-build-plan.md](12-build-plan.md) | Stack, repo layout, build slices, conventions borrowed from cado |
| [13-open-questions.md](13-open-questions.md) | Decisions still to make |
| [14-frontend-contract.md](14-frontend-contract.md) | Wiring, endpoints, and type generation for the frontend |
| [research/arch-validation.md](research/arch-validation.md) | Research report: is this architecture standard, Realtime API facts, OAuth facts |
| [research/turn-taking.md](research/turn-taking.md) | Research report: how texting agents handle bursts and turn-taking |

## One-paragraph summary

A per-user append-only event log is the single source of truth. Two handlers, text and voice, are pure functions of that log plus a slots object written only by tools. Exactly one handler holds "the floor" at a time: voice while a call is connected, text otherwise. Inbound deltas (user texts, typing, call events, Gmail events) are appended, then routed to the floor holder through a small policy function that chooses one of three verbs, interrupt, absorb, or defer, consulting a decider (rule-based now, Jev later) only when the answer is not fixed. Voice runs on OpenAI Realtime over WebRTC in the browser, with our backend attached by a sideband socket; audio never touches our server. Storage is SQLite via SQLModel, in-memory cache with write-through, one long-running FastAPI process.
