# Frontend contract

The one page the frontend needs. Message shapes are in [09-protocol.md](09-protocol.md); this note covers wiring and the pieces that let the frontend be built before the backend exists.

## Layout and dev wiring

- `frontend/` is a Vite + React + TypeScript project with its own `package.json`. `backend/` is a uv project.
- **Dev:** Vite dev server on `5173` proxies `/api` and `/ws` to FastAPI on `8000`. The frontend never hardcodes a host; all requests are relative.
- **Prod:** FastAPI serves `frontend/dist` as static files from the same origin.
- Every backend HTTP route lives under `/api`. The WebSocket is at `/ws`.

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/session` | `{ "phone": "..." }` | snapshot (events, slots, call, floor) |
| GET | `/api/auth/google/start?phone=...` | | redirect to Google |
| GET | `/api/auth/google/callback` | | redirect to a "you can close this" page |
| WS | `/ws?phone=...` | see below | see below |
| WS | `/ws/audio?phone=...` | binary PCM16 frames | binary PCM16 frames |

The browser keeps the phone number in local storage. It is the user id for the demo (see docs 08); a "phone" entry screen is the first thing shown when none is stored.

## WebSocket messages

Client to server:

```
{ "type": "message",  "text": "..." }
{ "type": "typing",   "active": true }
{ "type": "call",     "action": "start" | "accept" | "decline" | "hangup" | "dropped" }
{ "type": "reset" }
```

Server to client:

```
{ "type": "snapshot", "events": [...], "slots": {...}, "call": {...}, "floor": "text" }
{ "type": "event",    "event": {...} }       # every store append, in seq order
{ "type": "slots",    "slots": {...} }
{ "type": "call",     "call": {...} }
{ "type": "typing",   "active": true }       # agent typing indicator
{ "type": "partial",  "speaker": "user" | "agent", "text": "..." }   # optional live transcript, transient
```

Event envelope and payload kinds are in [04-events-and-types.md](04-events-and-types.md). The UI is a pure function of snapshot plus event stream; no client-side state the server does not have.

## Types without drift

The backend exports JSON Schema for every HTTP and WebSocket message from its Pydantic models (`uv run python -m app.schema > ../frontend/src/schema.json`, once it exists). The frontend generates TypeScript types from that file (e.g. `json-schema-to-typescript`). One command on each side; neither hand-writes the other's types.

Until the backend exists, the frontend can build against a small fake WebSocket server that replays a scripted event stream (a happy path, a hang-up path, a declined-Gmail path). Keep those scripts; they become demo fixtures.

## What the UI shows

- **Phone frame** mimicking iMessage: thread of user and agent bubbles rendered from `user_message` / `agent_message` events; agent typing indicator; input with typing signals sent to the server.
- **Call card** inline in the thread: appears on `call` phase `ringing` (incoming-call screen with accept/decline), shows the live transcript from `voice_utterance` events (and optional `partial` messages) while `connected`, collapses to "call, N min" after `ended` with an expand.
- **In-call controls:** hang up. A "call" button when no call is active (user-initiated call; goes straight to connected).
- **Debug panel** beside the phone: slots with "still missing", floor, call state, and a live list of `decision` events (trigger kind, verb, who decided, confidence, latency). A reset button.

## Call audio (no WebRTC)

Audio is relayed through the backend over a second WebSocket, `/ws/audio?phone=...`, because the voice model (GPT-Live via pydantic-ai) does not support browser WebRTC. Spec:

- **Format:** mono PCM16 little-endian at **24 000 Hz**, both directions, sent as binary WebSocket frames of ~20 ms (480 samples = 960 bytes). No JSON on this socket.
- **Capture:** `getUserMedia` → `AudioContext({ sampleRate: 24000 })` → an `AudioWorkletNode` that converts Float32 to Int16 and posts 20 ms chunks → `ws.send(buffer)`. If the context cannot open at 24 kHz, resample in the worklet.
- **Keep sending while muted.** When the user mutes, send frames of zeros at the same cadence. The voice model only accepts injected text context while audio is flowing.
- **Playback:** incoming binary frames go into a ring buffer feeding an `AudioWorkletNode` (or a scheduled `AudioBufferSourceNode` chain); target ~100 ms of buffered audio before starting to avoid underruns. On barge-in the server just stops sending; nothing to flush client-side beyond letting the buffer drain.
- **Lifecycle:** open the socket when the call connects (on `accept`, or on the user's own `start`); close it on hang-up. **The audio socket is the call**: its close is the hang-up signal for the backend, so tab close and network drop need no extra message. Send `{"type":"call","action":"hangup"}` on the main socket too, for the store record; `dropped` is no longer needed.
- **Permissions:** request the microphone only when the user accepts or starts a call, not on page load.
