# Architecture

## Principle

Text and voice are two interfaces to one conversation. Most failure modes in Persona's real onboarding come from the two drifting apart. So: one source of truth that both channels read and write, and disposable model sessions rebuilt from it.

This behaves like a single persistent Realtime session spanning both channels, except persistence lives in our store instead of in a socket. That is why it survives a hangup, a refresh, or a crash.

## Components

- **Context store.** A per-user append-only event log plus a slots object. Every user text, voice utterance, tool call, and system event (Gmail connected, call ended) is appended with an origin and a channel tag. Slots (agent name, user name, Gmail status, help need, call status, graduated) are written only by tools. See [04-events-and-types.md](04-events-and-types.md).
- **Router.** Deterministic code. Receives every appended delta, drops it if its origin is the current floor holder, otherwise sends it to the active head.
- **Heads.** One per medium. A head holds the policy function and a decider, and turns a delta into a verb (interrupt, absorb, defer) for its handler. See [05-routing-and-decider.md](05-routing-and-decider.md).
- **Text handler.** Stateless per turn: one pydantic-ai agent run built from slots plus the log. Output is zero to four bubbles plus tool calls.
- **Voice handler.** A server-side host attached to an OpenAI Realtime session by a sideband socket, existing only while a call is connected. Instructions built from the same prompt template plus a voice tail, history seeded from the store. See [07-voice-realtime.md](07-voice-realtime.md).
- **Context views.** Two thin renderers over the store, one per channel, differing only in their tail. Plus a bounded view for the decider.
- **Channel port.** The abstraction the handlers use to send bubbles and set the typing indicator. Web implementation now; iMessage or Twilio later. See [09-protocol.md](09-protocol.md).

## The floor

Exactly one handler has autonomous response rights at a time.

| Call state | Floor | Inbound deltas go to |
|---|---|---|
| none | text | text head |
| ringing (outbound call, accept/decline showing) | text | text head ("wait, don't call me" needs a text reply and a cancel) |
| connected | voice | voice head, injected into the live session as labeled items |
| ended | flips to text | text head responds to the ended event ("looks like we got cut off...") |

The handler without the floor is still callable as a service (the voice agent can send a text via a tool) but never responds on its own initiative.

## Orchestration is code, not an agent

The router, the policy function, the slot store, and the prompt builders are deterministic. The LLMs are responders that the router hands the floor to. Nothing in "who responds to this event" needs a model, which is what makes the behavior explainable in the debug panel.

## Data flow

```
User types ──▶ delta ──▶ store.append ──▶ router ──▶ text head ──▶ text handler ──▶ bubbles ──▶ store (passive) ──▶ browser
                                                  └─▶ voice head ─▶ voice handler ─▶ sideband ─▶ OpenAI Realtime ─▶ browser (audio)
User speaks ──WebRTC──▶ OpenAI Realtime ──sideband events──▶ voice handler ──▶ store (passive) ──▶ browser
```

Rules that keep it honest:

- **Store before route.** The append happens first, then routing with the event id, so the handler's context view always includes the delta it is responding to.
- **Two entry points, structurally distinct.** External inputs become deltas and go through the router. A handler reporting on its own activity (transcripts, its own bubbles, tool calls) writes straight to the store. The router's origin check is defense in depth against feedback loops, not the primary mechanism.
- **Per-user serialization.** One consumer task drains one queue per user; the store assigns the sequence number inside that consumer. Producers only enqueue.
- **Tools operate on ids, never on re-typed content.** The model never acts on the world directly. It requests a tool call; our handler executes it against the store and returns the result. (Persona's double-email bug is the failure mode this prevents.)
- **No summarization during onboarding.** Calls are a few minutes, transcripts fit, channel-tagged raw events fix provenance, and summarization is exactly the lossy async step that creates the hang-up race. Revisit trigger: multi-day memory or context length.

## The hang-up race

Scenario: user hangs up, an async job starts summarizing the call, user texts a second later, text handler runs before the summary exists.

- Raw facts are written synchronously; anything derived is enrichment. Transcript rows are already in the store as they arrive. The "call ended" event is appended in the same queue step that flips the floor, before anything async.
- The debounce window absorbs most enrichment latency anyway.
- Detection lag is the real hole: a closed tab can take seconds for the server to notice. If injection into the voice session fails because the socket is gone, that failure is itself the call-end signal: mark ended, flip the floor, re-route the same delta. No text is ever lost to the gap.
