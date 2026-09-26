# Voice: OpenAI Realtime over WebRTC with a server sideband

Facts verified 2026-09-25 against developers.openai.com (platform.openai.com/docs now redirects there). Full report in [research/arch-validation.md](research/arch-validation.md).

## Topology

- **Media:** browser to OpenAI directly over WebRTC, both directions. Our server never touches audio.
- **Signaling:** passes through our server. Browser posts its SDP offer; server calls `POST /v1/realtime/calls` with the API key and returns the SDP answer. The `Location` header carries the call id.
- **Control:** server attaches a sideband WebSocket at `wss://api.openai.com/v1/realtime?call_id={call_id}`. Over it we send `session.update`, `conversation.item.create`, `response.create`, `response.cancel`, and answer tool calls; we receive transcripts, tool call requests, errors, and socket close. `POST /v1/realtime/calls/{call_id}/hangup` ends a WebRTC call.
- **Credential rule:** the API key creates the call and the same API key opens the sideband. A bug in early September 2026 broke sideband attach for calls created with an ephemeral client secret.

Pydantic AI (Realtime support added August 2026) implements this pattern: an offer-relay helper returns the SDP answer and a session handle; the agent's realtime session attaches to it as the sideband. There is a runnable FastAPI plus browser example in their docs. Install: `pydantic-ai-slim[openai-realtime]`.

## What the model does and does not do

The model *decides* to call a tool: it emits a function-call item with a name and JSON arguments. It has no access to our store, the text thread, or call controls. Our voice handler receives the request over the sideband, executes it (write a slot, send a text bubble, hang up), and returns a function-call-output item, usually followed by a response request so the model continues. Same shape for the text model over HTTP.

Tools (both handlers share them): set agent name, set user name, record help need, send Gmail link, skip Gmail, start call (text only, with a reason), end call (voice only), graduate.

## Session lifecycle

1. Created on call connect. Instructions = shared persona + shared style block + slots and "still missing" line + reason for the call + voice tail. History seeded from the store by creating conversation items after connect (instructions alone do not carry the thread; the SDK starts with empty history).
2. During the call: transcript events appended to the store as they arrive (placeholder on item-created, filled on transcription-completed, keyed by item id). Injected deltas arrive as labeled items, e.g. `[User texted: ...]`, `[System: Gmail connected as x@gmail.com]`. Our own outbound texts are injected as assistant items so the model knows what it sent.
3. Ended by any of six sources, all funneling to one `call ended` event with a reason: hang-up button, agent end-call tool, browser peer-connection state failed/disconnected, browser WebSocket closing during a call, sideband socket close, 60-minute `session_expired`.

## Rules from the API's actual behavior

- **One response at a time.** A second `response.create` while one is active is rejected with `conversation_already_has_active_response` and dropped, not queued. Keep a one-slot response queue per session: urgent events cancel the active response first; non-urgent ones are injected as items without a response request; nothing fires blind.
- **Instructions can be updated mid-session** via `session.update` (everything except voice and model); changes do not apply to a response already in progress. Pydantic AI caveat: its dynamic instructions resolve once at connect, so a mid-call rename goes in as a context message or via the raw provider session.
- **Transcription completions may arrive out of order** across turns. Key by item id.
- **No call-ended webhook.** Disconnect detection is ours (see above). The JS SDK's connection-change event is reported unreliable; one more reason to use the raw event stream.
- **60-minute hard limit** per session. Optional `idle_timeout_ms` under server VAD triggers a model response after silence, a free "are you still there?".
- **Model:** `gpt-realtime-2.1` (quickstart default as of Sept 2026), with the 2.1 mini as the cost fallback. `output_modalities: ["text"]` still supported if ever needed.
- **Sideband drops after silence** if ping responses time out; re-attach on close while the call is still up.

## Native vs ours

Barge-in (user talks over the agent) is handled by the server's VAD and never touches our filter. Our interrupt verb exists for *non-audio* deltas: a text, a typing signal, a Gmail event. The voice host uses it far less than the text host.

## Why not a text-only Realtime session for the text channel

The Realtime API supports text-only output, which tempts a design where one session lives across text and voice by toggling audio. Rejected: sessions are tied to a live connection with a maximum lifetime, so state has to be rebuilt from our log anyway; hang-up handling wants the session to die cleanly; and per-token pricing plus tooling are worse than a normal chat call.
