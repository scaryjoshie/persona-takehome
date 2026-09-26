# Frontend contract

The one page the frontend needs. Message shapes are in [09-protocol.md](09-protocol.md); this note covers wiring and the pieces that let the frontend be built before the backend exists.

## Layout and dev wiring

- `frontend/` is a Vite + React + TypeScript project with its own `package.json` (the frontend agent scaffolds it; only a README exists at the time of writing). `backend/` is a uv project. The Vite proxy needs `ws: true` for both `/ws` and `/ws/audio`.
- **Dev:** Vite dev server on `5173` proxies `/api` and `/ws` to FastAPI on `8000`. The frontend never hardcodes a host; all requests are relative.
- **Prod:** FastAPI serves `frontend/dist` as static files from the same origin.
- Every backend HTTP route lives under `/api`. The WebSocket is at `/ws`.

## Status (2026-09-26)

Built and tested: `POST /api/session`, `WS /ws` (snapshot on connect, then `event`, `slots`, `call`, `typing`; accepts `message`, `typing`, `call`, `reset`), static serving of `frontend/dist`, and `uv run python -m app.web.schema` for the JSON Schema. Built 2026-09-26: `/ws/audio` with GPT-Live, and `partial` messages. Not yet built: Google OAuth (slice 4). `/ws/audio` closes with 4400 if no call is connecting (send `accept` or `start` first) and 4409 if a call is already running for the user.

Two details: the user id is the phone's **digits only** (a `+` in a query string decodes to a space, so the server strips everything but digits; send whatever the user typed). Decision events use verb `start` in addition to interrupt/absorb/defer (logged when nothing was in progress).

Run: `cd backend && uv run uvicorn app.main:app --reload` (port 8000, which the Vite proxy expects).

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
{ "type": "call",     "action": "start" | "accept" | "decline" | "hangup" }
{ "type": "call",     "action": "failed", "reason": "mic_denied" | "audio_socket" }
{ "type": "reset" }
```

Server to client:

```
{ "type": "snapshot", "events": [...], "slots": {...}, "call": {...}, "floor": "text" }
{ "type": "event",    "event": {...} }       # every store append, in seq order
{ "type": "slots",    "slots": {...} }
{ "type": "call",     "call": { "phase", "reason", "started_at", "ended_at", "initiated_by", "call_id" } }
{ "type": "typing",   "active": true }       # agent typing indicator
{ "type": "partial",  "speaker": "user" | "agent", "turn_id": "...", "text": "...", "final": false }   # cumulative per turn
```

Event envelope and payload kinds are in [04-events-and-types.md](04-events-and-types.md). The UI is a pure function of snapshot plus event stream; no client-side state the server does not have.

## Types without drift

The backend exports JSON Schema for every HTTP and WebSocket message from its Pydantic models (`uv run python -m app.schema > ../frontend/src/schema.json`, once it exists). The frontend generates TypeScript types from that file (e.g. `json-schema-to-typescript`). One command on each side; neither hand-writes the other's types.

Until the backend exists, the frontend can build against a small fake WebSocket server that replays a scripted event stream (a happy path, a hang-up path, a declined-Gmail path). Keep those scripts; they become demo fixtures.

## What the UI shows

- **Phone frame** mimicking iMessage: thread of user and agent bubbles rendered from `user_message` / `agent_message` events; agent typing indicator; input with typing signals sent to the server.
- **Calls are not a card in the thread.** Ringing is the iOS compact call banner on the phone; a voice "orb" beside the phone lights up during a call and shows the live transcript from `partial` messages (cumulative per `turn_id`, replace on update, `final` closes the turn) and `voice_utterance` events; the thread gets an iOS-style centered call-log line ("Outgoing call, 3 min") from the `call` events.
- **In-call controls:** hang up, mute. A "call" button when no call is active (user-initiated; phase goes none → connecting → connected).
- **Call phases:** `accept`/`start` are intent. Show "connecting" until the server's `call` message says `connected` (audio socket open and voice session up). If the mic is denied or the audio socket cannot open, send `failed` with a reason; the server logs it and the text agent responds. The server also fails the call itself if no audio socket arrives within 10 s.
- **Debug panel** beside the phone: slots with "still missing", floor, call state, and a live list of `decision` events (trigger kind, verb, who decided, confidence, latency). A reset button.

## Call audio (no WebRTC)

Audio is relayed through the backend over a second WebSocket, `/ws/audio?phone=...`, because the voice model (GPT-Live via pydantic-ai) does not support browser WebRTC. Spec:

- **Format:** mono PCM16 little-endian at **24 000 Hz**, both directions, sent as binary WebSocket frames of ~20 ms (480 samples = 960 bytes). No JSON on this socket.
- **Capture:** `getUserMedia` → `AudioContext({ sampleRate: 24000 })` → an `AudioWorkletNode` that converts Float32 to Int16 and posts 20 ms chunks → `ws.send(buffer)`. If the context cannot open at 24 kHz, resample in the worklet.
- **Keep sending while muted.** When the user mutes, send frames of zeros at the same cadence. The voice model only accepts injected text context while audio is flowing.
- **Playback:** incoming binary frames go into a ring buffer feeding an `AudioWorkletNode` (or a scheduled `AudioBufferSourceNode` chain); target ~100 ms of buffered audio before starting to avoid underruns. On barge-in the server just stops sending; nothing to flush client-side beyond letting the buffer drain.
- **Lifecycle:** open the socket right after sending `accept` or `start`; the server reports `connected` once the socket is open and the voice session is up; close it on hang-up. A second audio socket for the same phone is rejected with close code 4409 (show "call in progress on another tab"). **The audio socket is the call**: its close is the hang-up signal for the backend, so tab close and network drop need no extra message. Send `{"type":"call","action":"hangup"}` on the main socket too, for the store record; `dropped` is no longer needed.
- **Interruption hygiene (required):** when the user starts speaking, discard the client playback buffer immediately (the server cannot stop the model, only stop sending; no output-done event exists). Use `getUserMedia` with `echoCancellation: true`, `noiseSuppression: true`; there is no server-side echo cancellation.
- **Permissions:** request the microphone only when the user accepts or starts a call, not on page load.
