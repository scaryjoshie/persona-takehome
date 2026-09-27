# Diagrams

- `call-architecture`: a call. GPT-Live talks; the back office acts (two keys: their yes, then the voice's "on it"); the brief, facts and timers reach the voice as instructions, context and commentary.
- `routing`: how any event moves: saved to the log, then routed by floor to the text or voice side, each with a Jev check first.

Render (from this folder): `d2 call-architecture.d2 call-architecture.svg` and `d2 routing.d2 routing.svg` (or `.png`).

Styling copies cado's `backend-2/tools/depgraph/arch.d2`: ELK layout, theme 0, white leaves on tinted trays, orange for the pipeline, blue for models, purple for Jev, slate edges.
