# Research report: GPT-Live 1 in practice (2026-09-25)

Produced by a research subagent on 2026-09-25 from OpenAI docs, community threads, GitHub issues in openai/*, pydantic/pydantic-ai, livekit/*, and third-party measurements. Reproduced as received. Verify anything load-bearing before relying on it.

## 1. Delegation behavior
- **Under-delegation is the dominant reported failure.** Live verbally commits ("Sure, I'll end the call now") but emits no `session.delegation.created`, no `response.event`, no error. One reporter saw ~50% failures some mornings, correlated with backend changes. LiveKit measured a confirmation spoken over the voice's own read-back failing to delegate 12 of 15 times; their fix hands accumulated undelegated caller text to the backend after 8 s of mutual silence (11/11 stuck calls recovered).
- **Over-delegation** shows up mostly in coding-agent uses (HN: "passes off work even for 'change this line'", 30 s to 2 min round trips). The prompting guide's template says clarify first when "you cannot tell what they are asking for".
- **Controlling it from the speaking prompt:** OpenAI staff advise a delegation policy of concrete trigger phrases ("end the call", "book it") rather than expecting Live to pick tools. Mid-session `session.instructions.append` (delegation_id null) steers only Live, not running work.
- **Latency:** UNKNOWN for API-level delegation end to end; no primary measurement exists. Agora measured the ChatGPT app at ~1.1 s to acknowledgment and ~1.4 s to substantive answer. Docs say latency is dominated by your tool speed; Live "prepares request configuration in advance" and "can reuse prior response state", so the backend is chained, not cold.
- **Backend models:** OpenAI's guide says start with `gpt-6-luna`, move to `gpt-6-sol` for harder reasoning; pydantic-ai's `AUTO_BACKEND_MODEL` is `gpt-6-sol`; LiveKit defaults to `gpt-5.6-luna`. Knobs: `reasoning.effort`, `text.verbosity`, `service_tier` (priority doubles token price), `max_output_tokens`.
- Sources: https://community.openai.com/t/gpt-live-1-responses-delegation-voice-model-sometimes-never-delegates-acknowledges-verbally-sends-nothing/1398585 · https://github.com/livekit/agents/pull/7455 · https://developers.openai.com/api/docs/guides/live-delegation · https://developers.openai.com/api/docs/guides/live-prompting · https://news.ycombinator.com/item?id=49653985 · https://www.agora.io/en/blog/openai-didnt-publish-gpt-lives-latency-so-we-measured-it/

## 2. Filler and stalling
- The prompting guide permits "acknowledge a request while delegated work runs", requires "Do not guess the result while waiting", and recommends "moderate backchannels".
- No "infinite filler" bug is reported. The reported pattern is the opposite: one acknowledgment, then silence, because nothing was delegated or a delegation was never answered.
- OpenAI's eval cookbook defines "silence during delegation" (cumulative and max uninterrupted) as a first-class metric, which implies OpenAI expects dead air.
- HN consensus: "conversation quality is a real leap, the intelligence is uneven"; Live's own reasoning "feels weaker".
- Sources: https://developers.openai.com/api/docs/guides/live-prompting · https://developers.openai.com/cookbook/examples/audio/voice_agent_evaluation · https://www.eesel.ai/blog/gpt-live-1

## 3. Injected text
- `commentary.append` is "trained to paraphrase"; it is not TTS. `thinking.append` is quiet context that "can still influence later speech, so it isn't a place for secrets". Both 500 tokens, plain string.
- The `*.appended` ack means "accepted for injection at the estimated timeline point", explicitly not consumed, spoken, or played.
- Confirmed: appends sit on the audio timeline and are picked up only while input audio flows. pydantic-ai's docstring: a session whose microphone isn't streaming "silently defers everything sent to it". No warning is raised.
- One production report says `instructions.append` and `commentary.append` "lack reliability... particularly for model-initiated actions". No quantified pickup latency (UNKNOWN).
- pydantic-ai mapping: plain `str` goes to `commentary.append`; `TextContext` goes to `thinking.append`. Verify that the `respond=False` path actually produces the thinking event.
- Sources: https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/gpt-live · https://developers.openai.com/api/docs/guides/live-conversations · https://raw.githubusercontent.com/pydantic/pydantic-ai/main/pydantic_ai_slim/pydantic_ai/realtime/openai_live.py

## 4. Turn boundary and transcripts
- Fragment boundaries "reflect audio cadence, not semantic turn boundaries"; user and assistant fragments interleave; "transcripts can contain mistakes"; no authoritative turn-completed event. Match with contains, not equals.
- Accuracy complaint: "responses to something it thinks I said but I didn't say".
- Turn inference is client-side heuristics everywhere: pydantic-ai default 2000 ms after the model goes quiet; voice-delegate uses 800 ms gaps between user fragments; LiveKit says pauses under ~0.5 s don't split turns; OpenAI's harness keeps a 400 ms playback reserve. The model itself handles pauses well (Speak reported ~80% fewer interruptions).
- Sources: https://learn.microsoft.com/en-us/azure/foundry/openai/gpt-live-reference · https://www.promptfoo.dev/docs/providers/openai-live/ · https://github.com/savbiz/voice-delegate · https://docs.livekit.io/agents/models/realtime/plugins/gpt-live/

## 5. Interruptions
- Live owns barge-in; no client-side turn control exists. You cannot stop the model, only stop playing its audio.
- Agora (ChatGPT app): 30/30 deliberate barge-ins succeeded but Live went silent 498 ms slower than Advanced Voice; rejected 30/30 background-speech probes and 1/60 false interruptions; it answered background voices in 4/30 windows.
- Interrupting speech does not cancel backend work; late results can be narrated unless gated (generation counter).
- Client flush: no output-done event and no flush guidance. Practitioners discard buffered playback themselves on user speech; pydantic-ai does not record barge-in or flush audio.
- Sources: https://www.agora.io/en/blog/openai-didnt-publish-gpt-lives-latency-so-we-measured-it/ · https://docs.livekit.io/agents/models/realtime/plugins/gpt-live/ · https://github.com/pydantic/pydantic-ai/pull/8390

## 6. Session limits and failures
- **Max duration: UNKNOWN.** `session.started` carries `expires_at`. LiveKit offers `max_session_duration` for periodic reconnection with history resent.
- Context: 128,000 tokens including audio. Above 90% a replacement engine starts inside the same session with original instructions plus up to 8,192 tokens of recent history/summary; no event announces it. `usage_ratio` arrives in `session.usage.updated`; pydantic-ai exposes it as `context_window_used`.
- Close reasons: `close_requested`, `expired`, `content`, `remote_hangup`, `connection_lost`. Frequencies UNKNOWN.
- Reconnection: none built in. New session seeded with text only, or forked from a stored session (30 days).
- Rate limits are concurrent sessions: 25 (Tier 1) to 500 (Tier 5); no free tier.
- Sources: https://developers.openai.com/api/docs/guides/live-conversations · https://developers.openai.com/api/docs/models/gpt-live-1 · https://github.com/pydantic/pydantic-ai/pull/8803

## 7. Audio
- Formats: PCM 24 kHz (default), PCM 16 kHz, G.711 8 kHz. One format both directions, fixed at start; even byte counts required. pydantic-ai accepts only 16k/24k PCM.
- Chunking: OpenAI's example sends 4,800 bytes (100 ms at 24k); Vercel's sends 960 bytes every 20 ms. Pace at real time.
- Relay vs WebRTC: no Live-specific measurement. An extra hop adds ~10–100 ms plus TCP head-of-line blocking on lossy links. Agora saw Live degrade only +314 ms median under 10% loss.
- No echo cancellation on the server; browser AEC is your job. Voice timbre varies between calls.
- Sources: https://developers.openai.com/api/docs/guides/voice-websockets?api=live · https://vercel.com/docs/ai-gateway/modalities/realtime/gpt-live

## 8. Pricing surprises
- $0.05/min, per second, running during silence, mute, and backend waits. WebRTC session creation bills 15 s upfront; WebSocket has no such charge documented.
- Backend tokens at normal rates; Live reuses prior response state, so not a full resend per delegation. Tokens per delegation UNKNOWN. OpenAI's example: 90 s call = $0.075 voice + $0.02 backend.
- Backend rates: gpt-6-luna $0.10/$0.50, gpt-6-sol $2/$10, gpt-5.6-sol $4/$20, gpt-6-astra $10/$50 per M. Priority tier doubles them.
- Sources: https://developers.openai.com/api/docs/guides/voice-latency-cost · https://developers.openai.com/api/docs/pricing

## 9. pydantic-ai adapter
- `AUTO_BACKEND_MODEL = 'gpt-6-sol'`; default `openai_live_turn_silence_ms = 2000`. Default live instructions: "You are a voice assistant. Keep replies short and conversational. When the user asks for something you cannot answer from this conversation alone, delegate the task and tell them you are looking it up."
- Documented gaps: no client delegation, no gateway routing, no auto reconnect, barge-in not recorded, no mid-session instruction/tool updates, text-only seeding, `tool_choice='required'` raises. Tool results batch into one `response.create`, avoiding the function-call lock bug LiveKit hit.
- Open follow-ups: #8707 (mid-session system prompt lands as `<system>`-tagged user text; proposes `instructions.append`), #8420, #8810/#8813 (async tool mode), #8801 draft (overlapping responses corrupt state). Merged: #8803 context usage, #8747 rollback of refused sends, #8802 timing replay test.
- Sources: https://github.com/pydantic/pydantic-ai/pull/8390 · https://github.com/pydantic/pydantic-ai/issues/8707 · https://github.com/pydantic/pydantic-ai/pull/8765 · https://github.com/pydantic/pydantic-ai/pull/8801

## 10. Known bugs since launch
- Delegation 403 `model_not_found` for models that work directly (permission-cache staleness, Sept 22). `missing_scope api.responses.write` errors ~200 ms before `session.started`; treat as non-fatal.
- Live refuses `response.create` while any function call is unanswered; a stray continuation makes the backend "never answer again". An unanswered function call blocks all later backend turns.
- Unanswered client delegations have no provider timeout (>59 s silence observed).
- "GPT Live just stopped talking" thread (Sept 24) with no diagnosis; `response.done` output always empty and duplicate `response.completed` events reported.
- Sources: https://community.openai.com/t/gpt-live-1-responses-delegation-returns-403-for-a-model-that-works-fine-direct/1399936 · https://github.com/livekit/agents-js/pull/2495 · https://community.openai.com/t/gpt-live-stops-talking-and-no-idea-why/1400258

## Top 8 things to design for
1. **Silent non-delegation watchdog.** Track undelegated user speech; if no `session.delegation.created` within ~6–8 s of an intent you require, push the transcript to the backend yourself or re-prompt.
2. **Trigger-phrase delegation policy in `openai_live_instructions`.** Enumerate onboarding steps and their spoken triggers; keep business rules in the agent prompt. Add "do not confirm anything before the backend returns".
3. **Delegation timeout and dead-air fallback.** Time each delegation; after ~5 s inject commentary ("still checking"), after ~15 s fail the step gracefully.
4. **Never send text while audio is idle.** Keep the mic stream continuous; otherwise `send()` calls queue invisibly.
5. **Verify the thinking path and treat commentary as paraphrase.** Confirm `respond=False` emits `thinking.append`; put nothing secret in either; check outcomes from tool results, not transcripts.
6. **Own interruption hygiene.** Flush your playback buffer on user speech, gate late backend results with a generation counter, and use browser AEC. Expect ~0.5 s extra stop latency versus Realtime.
7. **Plan for session death.** Handle all five close reasons; keep a text summary ready to seed a new session; budget a 128k window with silent compaction above 90%; watch `context_window_used`.
8. **Cost and capacity levers.** Start on `gpt-6-luna` with low reasoning effort and verbosity; measure per-delegation tokens yourself; concurrency caps (25 at Tier 1) will bind before cost.
