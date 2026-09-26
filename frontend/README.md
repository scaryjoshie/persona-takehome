# Frontend

Vite + React + TypeScript. The handset, the voice orb, and the observer panel.

```
pnpm install
pnpm dev          # http://localhost:5173, proxies /api and /ws to FastAPI on :8000
pnpm build        # -> dist/, served by FastAPI in prod
pnpm typecheck
```

Open `http://localhost:5173/?mock=1` to run against the built-in scripted backend (no server needed).
The entry screen also has a checkbox for it.

Layout:

- `src/phone/` everything inside the glass. Framework7 (iOS theme) renders the thread and composer; nothing outside this folder imports it.
- `src/orb/` the voice orb (vendored from ElevenLabs UI, MIT) and the call panel around it.
- `src/observer/` the debug readout: slots, floor, call, decisions, event log.
- `src/transport/` the backend contract: `ws.ts` is real, `scripted.ts` is the in-browser demo backend.
- `src/audio/` mic capture and playback worklets (PCM16 mono 24 kHz over `/ws/audio`).
- `src/state/` reducer over snapshot + event stream, plus derived views.
- `src/types.ts` mirrors `backend/app/core/types.py` until the JSON-schema export exists.

Contract: `docs/proposed-design/14-frontend-contract.md`.
