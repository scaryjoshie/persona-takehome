# Diagrams

- `routing`: every event becomes a Delta, is saved to the Context Store, and the Router sends it to the Text Handler or the Voice Handler, each behind a Jev filtration head. The handlers write texts, transcripts and actions back as passive store (dashed).
- `call-architecture`: a call. The GPT-Live Session talks; the Call Agent (our text model with tools) acts, only after the user asked or agreed and the voice said it is on it, a moment the Jev Commitment head catches mid-sentence. The Call Brief, the Call Agent and the timers reach the session as instructions, context and commentary.

Render (from this folder): `d2 routing.d2 routing.svg`, `d2 call-architecture.d2 call-architecture.svg` (or `.png`).

Styling copies cado's `backend-2/tools/depgraph/arch.d2` classes: ELK layout, theme 0, white leaves on tinted trays, orange for routing, blue for handlers and models, purple for Jev, slate edges.
