# Voice option B: GPT-Live 1 (client delegation)

Added 2026-09-25 after noticing that OpenAI released **GPT-Live 1** (`gpt-live-1`) on 2026-09-10. [07-voice-realtime.md](07-voice-realtime.md) describes `gpt-realtime-2.1`; this note describes the alternative and how it changes the design. Facts from developers.openai.com guides (live, live-delegation, live-conversations, live-migration, voice-server-controls) and the model page, fetched 2026-09-25. Two weeks old; verify before building.

## What it is

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

## What gets worse

- **Transcripts are deltas with `start_ms`/`end_ms` only.** No item ids, no turn-complete markers, "delivery can be uneven." Our one-row-per-utterance storage unit needs a grouping rule (gap threshold on timestamps) instead of item ids. See 04.
- **No `response.done` equivalent.** "Is the agent mid-response?" and "was the last agent turn a question?" must be inferred from output transcript deltas. The `Run` object for voice becomes an inference, not a fact.
- **Maturity.** Two weeks old. Pydantic AI's realtime support (Aug 2026) targets gpt-realtime; assume no GPT-Live support and plan to write the sideband client on raw websockets. Verify.
- **Delegation is the model's decision.** In client mode we get no task text, only "the model thinks it needs help now." We should not depend on it; treat every completed user turn as a trigger for our agent and use delegation events as a hint.
- **Seeding** is via an `input` field at session start (max 128 messages / 8,192 tokens) or by forking a stored session (`store: true`, 30-day retention). Enough for onboarding; smaller than Realtime's item creation.
- **Lifetime** is unclear: the model page lists no maximum; one third-party source says about 30 minutes. `session.closed` carries `reason` in {close_requested, expired, content, remote_hangup, connection_lost}, which is a nicer end-detection story than Realtime's.

## Recommendation

Design for client delegation regardless, because it is what we are already building: our backend owns the brain and the voice layer sits behind a `VoiceLayer` interface with two implementations. Decide at build slice 3 with a one-day spike on `gpt-live-1`: create a browser call, attach the sideband, receive transcript deltas, push a `thinking.append` and an `instructions.append`, close cleanly. If it holds up, use it; the "same agent talks and texts" story is stronger and matches the design exactly. If it is flaky or the docs gaps bite, fall back to `gpt-realtime-2.1`, where 07 applies and Pydantic AI has support.

Either way, the voice-specific code should be confined to the `VoiceLayer` implementation and the transcript grouping rule.

Sources: https://developers.openai.com/api/docs/guides/live , https://developers.openai.com/api/docs/guides/live-delegation , https://developers.openai.com/api/docs/guides/live-conversations , https://developers.openai.com/api/docs/guides/live-migration , https://developers.openai.com/api/docs/guides/voice-server-controls , https://developers.openai.com/api/docs/models/gpt-live-1 , https://openai.com/index/introducing-gpt-live/
