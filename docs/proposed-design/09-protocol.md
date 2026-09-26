# Frontend to backend protocol

HTTP for the few request/response things, one WebSocket per browser tab for everything live, and a second WebSocket for call audio that exists only while a call is connected. The UI is a pure function of the snapshot plus the event stream; nothing on the client has state the server does not. That is what makes refresh, second tab, and come-back-later free.

## HTTP

- `POST /session` with the phone number: creates or resumes the user. Returns a full snapshot (events, slots, call state, floor). The browser keeps the phone in local storage.
- `GET /auth/google/start` and `GET /auth/google/callback`: OAuth. The callback writes the Integration row and the `gmail connected` event, then redirects to a "you can close this" page.
- Static files for the Vite app.

## WebSocket, client to server

```
{ "type": "message",  "text": "..." }
{ "type": "typing",   "active": true }
{ "type": "call",     "action": "start" | "accept" | "decline" | "hangup" | "dropped" }
{ "type": "reset" }                          # debug panel only
```

## WebSocket, server to client

```
{ "type": "snapshot", "events": [...], "slots": {...}, "call": {...}, "floor": "text" }
{ "type": "event",    "event": {...} }       # every store append, in seq order
{ "type": "slots",    "slots": {...} }
{ "type": "call",     "call": {...} }
{ "type": "typing",   "active": true }       # agent typing indicator between bubbles
{ "type": "partial",  "speaker": "user"|"agent", "text": "..." }   # optional live transcript, transient
```

The thread renders from user and agent message events plus a call card (expands with the live transcript during a call, collapses to "call, 3 min" after). The debug panel renders from decision, slot, and call events.

## Channel port

The handlers use this and never look past it. Web implementation now (the WebSocket above); iMessage or Twilio later is a second class with the same methods, typing best-effort where the transport supports it.

```python
class Channel(Protocol):
    async def send(self, user: str, text: str) -> None
    async def set_typing(self, user: str, active: bool) -> None
    # inbound: on_message and on_typing callbacks registered at startup
```

## Audio WebSocket (`/ws/audio?phone=...`)

Opened by the browser when a call connects (accept, or the user's own call button); closed on hang-up. Binary frames only: mono PCM16 at 24 kHz, both directions, in ~20 ms chunks. The browser captures with an AudioWorklet and plays back through a ring buffer; when the mic is muted it still sends silence, because GPT-Live only takes text context while audio is flowing.

**The audio socket is the call.** Open means connected; close means ended. Hang-up button, tab close, and network drop all end the same way, so call-end detection has three sources instead of six: this socket closing, the agent's end-call tool (we close the session and the socket), and the session ending on its own (`live_session_expired`, `live_session_content`, `live_session_connection_lost`). WebRTC is not used (pydantic-ai does not support it for GPT-Live); it would only matter as a latency optimization on the Realtime fallback.

## Call state machine

Every transition is a call event in the store.

- **none.** Text has the floor. The text agent's start-call tool (with a reason) moves to ringing. The user's own call button moves straight to connected.
- **ringing.** Text still has the floor. Accept moves to connected. Decline, or a 30 s timeout, logs a declined event and returns to none; the text agent responds to that event.
- **connected.** Voice has the floor. The audio socket is open and the voice handler's session is created and seeded here.
- **ended.** From connected by any of the three sources above. One ended event with a reason, tear down the voice handler, floor back to text, text agent responds. Collapses immediately to none.

The ended event and the floor flip happen in the same queue step, before anything async.
