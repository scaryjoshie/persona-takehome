# Voice option B: GPT-Live 1 (client delegation)

Added 2026-09-25 after noticing that OpenAI released **GPT-Live 1** (`gpt-live-1`) on 2026-09-10. [07-voice-realtime.md](07-voice-realtime.md) describes `gpt-realtime-2.1`; this note describes the alternative and how it changes the design. Facts from developers.openai.com guides (live, live-delegation, live-conversations, live-migration, voice-server-controls) and the model page, fetched 2026-09-25. Two weeks old; verify before building.

## What it is

> **Update 2026-09-25 evening:** pydantic-ai support landed the same day; see "Pydantic AI support" below. The "Why it fits" section assumed client delegation; the adapter uses Responses delegation instead, which keeps the tools-run-locally property but not the choose-any-backend property.

A full-duplex voice layer that listens and speaks at the same time and **does not run tools or reasoning itself**. It "recognizes when a request needs backend help and hands off." Two delegation modes:

- **Responses delegation:** GPT-Live calls an OpenAI Responses model you configure (`delegation.responses.model`, `.instructions`, `.tools`); function calls arrive wrapped in `response.event`, you execute and return `response.item.create` (function_call_output) then `response.create`.
- **Client delegation:** your own backend is the brain. You receive `session.delegation.created` (metadata and `offset_ms` only, no task text), maintain context yourself from transcript deltas, and push results back with three append events, each a plain string capped at 500 tokens:
  - `session.commentary.append` — content the model should say aloud (it paraphrases)
  - `session.thinking.append` — facts for context, not spoken
  - `session.instructions.append` — directives; "can interrupt speech in progress"

Pricing: $0.05 per minute of voice session, billed per second, plus whatever the backend model costs. No image or video input. Endpoint is `v1/live/sessions`; browser WebRTC via `POST /v1/realtime/calls` with `model=gpt-live-1`, server sideband at `wss://api.openai.com/v1/realtime?call_id=...` as before.

## Why it fits this design unusually well

Client delegation is the architecture in [03-architecture.md](03-architecture.md) made into a product boundary: our store is the context, our backend runs the agent, the voice layer only talks. Consequences:

- **One brain, literally.** The same pydantic-ai agent (over OpenRouter) with the same tools serves text and voice. The style-drift and provenance bugs observed in Persona (see 02) cannot happen because there is no second model with a second prompt.
- **Slots written by our agent from transcripts,** not by the voice model calling tools. We can run our agent on every completed user turn regardless of delegation events.
- **Interruption is better and cheaper.** Barge-in is native and reportedly far more accurate; the typing case ("looks like you're typing, go ahead") is a single `session.instructions.append`.
- **Behavior during backend work, precisely.** Realtime does not block on tool calls either: the response ends when the function call is emitted, audio and turn detection continue, and the result can be returned whenever ready. The difference is default behavior: Realtime goes quiet while waiting unless prompted to say a filler first, and cannot weave a result into a sentence already in progress; Live is trained to keep talking and paraphrase results as they arrive. For onboarding, where every tool takes milliseconds, this is a minor difference.
- **Verb mapping:** interrupt → `instructions.append` or `commentary.append`; absorb → `thinking.append`; defer → hold the append until output transcript deltas go quiet (weaker, see below).

## Pydantic AI support (landed 2026-09-25)

pydantic/pydantic-ai PR #8390 ("Add OpenAI GPT-Live support with `OpenAILiveModel`", by the maintainer, closes #8300) merged 2026-09-25 19:31 UTC and is included in **v2.51.0**, the version installed in `backend/` (`pydantic_ai.realtime.openai_live` imports). Facts from the merged docs page `docs/realtime/openai-live.md`:

- `agent.realtime('openai:gpt-live-1')` routes to `OpenAILiveModel`. Any `gpt-live*` name does; other `openai:` realtime names route to `OpenAIRealtimeModel`.
- **It implements Responses delegation, not client delegation.** The agent's instructions and tools are handed to a Responses backend model; the backend's tool calls arrive as ordinary pydantic-ai `ToolCall`s and run **locally** through the normal tool loop (validation, retries, deps). A client delegation from the model is reported as an error event. So "our tools run against our store" holds, but the reasoning model during a call is an OpenAI Responses model, not OpenRouter.
- Backend model, first match: `openai_live_delegation={'model': ...}`; a `+` in the name (`'openai:gpt-live-1+gpt-6-luna'`); the agent's own model if it is an OpenAI model at the same base URL; else `'auto'` (`gpt-6-sol`). With an OpenRouter agent model, pin it explicitly.
- Two prompts by design: the agent's `instructions` describe the *work* (go to the backend); `openai_live_instructions` describes the *speech* (pacing, style, when to delegate).
- **Browser WebRTC is unsupported for Live** in pydantic-ai ("bridge media through your backend"). Audio goes browser → our WebSocket → pydantic-ai session → OpenAI over WebSocket, and back. Our server becomes a media relay (mono PCM16, 24 kHz default, 16 kHz optional). This contradicts the "audio never touches our server" line in 03 and 07 and changes the frontend transport (see 14).
- Turn boundary is inferred: `RealtimeTurnCompleteEvent` after `openai_live_turn_silence_ms` (default 2000) of model silence with no delegation outstanding; profile flag `synthesizes_turn_boundary=True`. Delegated work suspends the clock.
- Text is context, not a user turn: `session.send(text)` is speakable commentary; `send(text, respond=False)` is thinking, which the model often says anyway (word it "Internal note: ..."). 500-token cap per send, raises above it. **Text only lands while audio is flowing**, so keep `send_audio()` streaming silence when the user is quiet.
- `interrupt()` raises; Live handles barge-in itself and reports nothing, so a cut-off reply is recorded as complete. Manual turns, text output, thinking, input speech events, and native tools are unsupported.
- No reconnection: a dropped connection ends the session; open a new one seeded with history. Seeding: up to 128 messages / 8,192 tokens, text only (tool rounds rendered as text). Live compacts its own context near the limit.
- Instructions, voice, and audio format are fixed for the session (the API can append instructions mid-session; pydantic-ai does not expose it yet). A mid-call rename therefore goes in as context text.
- A delegation cannot be cancelled: "never mind" after the backend has the work still runs it, and the model may speak the result. Put state-changing tools behind approval if that matters.
- Session end reasons surface as `RealtimeError` codes `live_session_expired`, `live_session_content`, `live_session_connection_lost`; a failed backend is a recoverable `RealtimeSessionErrorEvent` (`live_delegation_failed`) and the call continues.
- Usage is billable seconds on the session plus backend tokens; the last seconds of a call *we* close may go unrecorded. The Pydantic AI Gateway does not route Live yet; use `provider='openai'`.

## What gets worse (updated)

- **Media relay through our backend.** The largest change. Frontend captures mic PCM with an AudioWorklet and streams it over our WebSocket; we forward to the session and stream output audio back for playback. More frontend work than WebRTC, a little more latency, and our process now carries audio. A WebSocket relay also works for `gpt-realtime` in pydantic-ai, so choosing it keeps the fallback cheap.
- **Voice-time reasoning must be an OpenAI Responses model.** The shared `Agent` (instructions, tools, deps) is the same object for text and voice, but the model differs per channel: OpenRouter for text, `gpt-6-sol`-class for voice via `openai_live_delegation`. "One agent, two models" rather than "one model."
- **No cancel, no interrupt control, no mid-session instruction change** through the adapter. Verb mapping becomes: interrupt → `send(text)` speakable; absorb → `send(text, respond=False)`; defer → wait for the inferred `RealtimeTurnCompleteEvent`, then send.
- **Transcripts** come as fragments with no item ids; pydantic-ai groups them into `SpeechPart`s per inferred turn. Our storage unit becomes one row per inferred turn, flagged as inferred.
- **Zero days old.** Verified against the live API by the maintainer with recorded cassettes, but no one else has run it yet.

## Recommendation (updated)

Primary: GPT-Live via `OpenAILiveModel`, since the adapter now exists and the "same agent, same tools, full-duplex voice" story is the strongest available. Fallback: `gpt-realtime-2.1` via `OpenAIRealtimeModel` behind the same `VoiceLayer` interface, which pydantic-ai makes nearly free because both are `agent.realtime(model)`.

Decide **now**, before the frontend starts: audio transport is a WebSocket relay through our backend (works for both models) rather than browser WebRTC (Realtime only). Slice 3 still opens with a one-day spike on Live: connect, stream mic audio, see transcripts, `send()` a context note, close, and confirm the inferred turn boundary is usable for `Run`.

Sources: https://developers.openai.com/api/docs/guides/live , https://developers.openai.com/api/docs/guides/live-delegation , https://developers.openai.com/api/docs/guides/live-conversations , https://developers.openai.com/api/docs/guides/live-migration , https://developers.openai.com/api/docs/guides/voice-server-controls , https://developers.openai.com/api/docs/models/gpt-live-1 , https://openai.com/index/introducing-gpt-live/
