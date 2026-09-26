# How GPT-Live runs our agent, and how we keep it from stalling

Written 2026-09-25 from the OpenAI Live guides and the pydantic-ai `OpenAILiveModel` docs (see [07b](07b-gpt-live-option.md) for the API facts). This note is the operating model: what each model does, how a delegation runs, and the design principles that follow. Draft; the slice-3 spike decides whether it holds.

## Two models, one agent definition

- **Live** (`gpt-live-1`) is a speech model: audio in, audio out, trained for conversation dynamics (when to talk, when to yield, acknowledgments, paraphrasing text it is handed). It can greet, banter, repeat, and answer from what is already in its conversation. It has **no tools and cannot reason through a task**. Its prompt is the *speaking instructions* (`openai_live_instructions`).
- **The backend** is an ordinary text reasoning model called through the Responses API with our agent's *work instructions* and tools. It never hears audio. It is the model that knows what onboarding is and which tool to call.
- From our code's point of view there is **one pydantic-ai `Agent`**: its instructions and tools become the backend config. The same agent, on the same model, serves the text channel. With the agent on an OpenAI model, `agent.realtime('openai:gpt-live-1')` delegates to that very model, so voice is literally an extension of the text agent: same brain, same tools, same store, plus ears and a mouth.

This is not "one model with different return instructions." The speech model cannot do the work even if asked.

## How a delegation runs, on the wire

1. Session start: one config message with the Live model, voice, speaking instructions, seed history (text, ≤128 messages / 8,192 tokens), and a delegation block naming the backend model, its instructions, and its tools. pydantic-ai builds the block from the agent.
2. Audio flows. Live talks and listens on its own. Transcript fragments stream to us for both speakers.
3. Live judges that a turn needs the backend and emits a delegation-created event: an id and a timestamp, **no task text**. Live itself calls the Responses API with the backend config plus the conversation as Live holds it (seed, everything said, every note we injected). We do not control that packaging.
4. The backend emits a function call. Live relays it to us tagged with the delegation id; pydantic-ai turns it into a normal tool call and runs our Python with our deps. We write the store and log the event.
5. We return the function output and a continue signal. The backend resumes, may call more tools, and finally produces text.
6. Live speaks that text in its own words, fitting it into whatever it was saying. During 4–5 Live keeps the conversation going if the speaking instructions told it to.
7. Once Live is quiet with no delegation outstanding, pydantic-ai infers turn-complete.

**Delegation is Live's only move.** It has no tools of its own and cannot name one; it emits one generic "I need help" signal and the backend picks the tool. The only knob we have on Live is *when* it hands off, via the speaking instructions. A delegation cannot be cancelled once started.

**What else goes in:** apart from audio, exactly one kind of thing: a text note ≤500 tokens, speakable (`send(text)`) or silent (`send(text, respond=False)`). Notes land in Live's conversation, so the backend sees them on its next delegation too. Silent notes are often spoken anyway unless the speaking instructions say what stays internal. Text only lands while audio is flowing.

## The stall problem, and why ours is different from Persona's

Persona's "let me check that" happened because its voice model was **uninformed**: it did not have the text thread, the reason for the call, or the state, so it had to go ask. Our Live session is seeded with the whole text history, told the state in the speaking instructions, and fed a note every time anything changes. If the answer is in its context, there is no handoff.

For onboarding, **the receptionist already knows the script.** Greet, ask the name, ask what they need, offer Gmail, react. Live carries that alone if the speaking instructions contain it. The backend's job is bookkeeping and judgment, not conversation, so delegation should almost never block the flow.

### Speaking-instruction principles

- Carry the conversation yourself; the back office records things. When the user gives a name or a need, keep going to the next thing; do not wait for confirmation.
- Never say "one sec" or "let me check." If you must wait, say specifically what you are doing, or ask the next question.
- State the known state up front: your name, what is still needed, how to explain Gmail in one sentence, what to do if they already know what they want, what to do if they decline.
- Notes from the back office are authoritative; do not hand off for anything already noted.
- What stays internal (e.g. "still need Gmail") and what may be said aloud.
- Pre-answer the common off-script questions (what the product does, why Gmail, that skipping is fine) so they need no delegation.

### Backend settings

Fast model, low `reasoning_effort`, low `verbosity`, short `max_output_tokens`, so a delegation behaves as close to a tool call as it can. Every tool is a millisecond slot write or a text send.

## Making delegation rare: the routing pass and prefires

Jev only, for routing. **No rule-based layer** until the spike shows what is actually needed. Everything in this section is gated on Jev access and is a stretch item; the core flow must work without it.

**Routing pass.** On each inferred user turn (a transcript row in the store), one Jev call with several parallel typed questions over the turn plus recent context: is this a name, a help need, a yes/no to Gmail, a concrete task (graduation), off-script. Runs concurrently with Live talking, so its latency never blocks speech.

**Prefire.** On a confident prediction, run the tool now and **inject the result into Live** as a note, so Live never needs the backend for it:

1. Inferred user turn lands in the store.
2. Routing pass predicts a tool with high confidence.
3. We run the tool immediately (a small fast extractor pulls slot values; Jev classifies but does not generate).
4. We inject the result: silent for bookkeeping ("Name recorded: Siobhan. Still need: help need, Gmail"), speakable for findings ("Inbox: one unanswered email from your landlord about the lease").
5. Live has the answer in its conversation; nothing to delegate. It keeps talking.

Delegation becomes the fallback for turns the routing pass could not classify with confidence. A speculation cache keyed by tool and arguments is the safety net when Live delegates anyway: the backend's tool call finds the slot already set or the result computed and returns instantly.

Two kinds of prefire, different value:
- **Speculative reads** (e.g. the inbox scan after Gmail connects, tied to the stated need) generalize to the real product: fetching while the user is still talking. Safe by construction. Only impressive if the read is slow, so the mock inbox scan should take a realistic second or two.
- **Speculative writes** (slot writes) save nothing on tool latency; their value is that Live never delegates for bookkeeping. Only for idempotent writes, which all of ours are. Same value, no-op; different value, last write wins and a note goes to Live.

Every speculation is a `decision` event, so the debug panel shows "predicted inbox scan, confidence 0.91, hit." Visible speculation is what makes it impressive.

**Routing rule exception.** The routing pass consumes transcript rows (normally record-only) and emits deltas with origin `system`, so the router's loop guard does not drop them. This is the one explicit exception to "transcripts are never routed."

## Implications from field research (2026-09-25)

Full report: [research/gpt-live-behavior.md](research/gpt-live-behavior.md). What changes:

- **Under-delegation is the dominant real-world failure**, not stalling. Live says "got it" and never hands off, up to ~50% of the time on bad days per one report; LiveKit measured 12 of 15 failures on confirmations. **We must not depend on Live delegating for bookkeeping.** This promotes the "routing pass + inject" path from stretch item to the primary bookkeeping mechanism, and adds a watchdog: if a user turn carried a required intent and no delegation appeared within ~6–8 s, push the work ourselves. Proposed shape, to decide: treat each inferred user voice turn as a delta to the **same agent run loop the text head uses** (same agent, same tools), with its output injected into Live as a note instead of sent as bubbles. Live's own delegation becomes a backup. Slot writes are idempotent, so double handling is harmless. This makes voice an extension of the text *handler*, not just the text agent's model.
- **Speaking instructions need a trigger-phrase delegation policy**, per OpenAI staff: enumerate the onboarding steps and the spoken cues for each, and add "do not confirm anything before the back office returns."
- **Delegation timeout.** Time each delegation; after ~5 s inject a speakable "still on it"; after ~15 s fail the step gracefully. Live will not do this itself. (Contradicts the earlier assumption that Live covers dead air; OpenAI's own eval treats silence-during-delegation as a metric.)
- **Verify the silent-note path** (`respond=False` must produce `thinking.append`), and put nothing secret in any note.
- **Interruption hygiene is ours.** Flush the browser playback buffer on user speech; gate late backend results with a generation counter so a retracted request's result is not narrated; browser echo cancellation is required. Expect ~0.5 s slower stop than Realtime.
- **Session death.** Five close reasons, no auto-reconnect, unknown max duration; keep the store-derived seed ready (≤8,192 tokens) and reseed on drop. Watch `context_window_used`.
- **Backend model.** OpenAI recommends starting on `gpt-6-luna` ($0.10/$0.50 per M) at low effort; `gpt-6-sol` is 20× the price. This reopens the one-model question: if the text channel wants `sol` quality and the voice backend wants `luna` latency, "one agent, two models" may be the right trade after all. Decide after the spike.
- **Known bug to code around:** an unanswered function call blocks all later backend turns; pydantic-ai batches results, but our tools must never raise without returning an output.

## What the slice-3 spike must measure

1. **Delegation frequency** on a scripted onboarding: how often Live hands off, and specifically how often it *fails to* on name / need / Gmail turns.
2. **Delegation latency** end to end (user turn → spoken result) with a fast backend at low effort.
3. **Turn-boundary quality**: whether the inferred `RealtimeTurnCompleteEvent` is good enough to drive the voice `Run` and the typing case, or whether voice should always lean absorb over interrupt.
4. **Note behavior**: whether silent notes get spoken, and how fast injected text is picked up.
5. **End-detection**: audio socket close, agent end-call, and session errors all produce one `call ended` event with the right reason.

If delegations are rare and under ~2 s, the design holds. If Live delegates constantly or stalls, `gpt-realtime-2.1` is one model that reasons inline with no handoff: no stall, worse interruptions, same router, same tools.
