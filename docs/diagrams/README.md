# Diagrams

- `routing`: every event becomes a Delta, is saved to the Context Store, and the Router sends it to the Text Handler or the Voice Handler, each behind a Jev filtration head. Dashed lines are passive store.
- `call-architecture`: a call. The GPT-Live Session talks; the Back Office acts (two keys: their yes, then the voice's "on it", caught early by the Jev Commit head). The Brief, the Back Office and Timers reach the session as instructions, context and commentary.

Render (from this folder): `d2 routing.d2 routing.svg`, `d2 call-architecture.d2 call-architecture.svg` (or `.png`).

Styling copies cado's `backend-2/tools/depgraph/arch.d2` classes: ELK layout, theme 0, white leaves on tinted trays, orange for routing, blue for handlers and models, purple for Jev, slate edges.
