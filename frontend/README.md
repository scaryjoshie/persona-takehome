# Frontend

The onboarding demo: an iPhone showing an iMessage thread with the assistant, and a voice orb beside it
that lights up during calls. Vite, React 19, TypeScript, Tailwind 4.

```
pnpm install
pnpm fetch-bezel   # downloads Apple's iPhone 17 Pro bezel into public/bezels/ (gitignored, see below)
pnpm dev           # http://localhost:5173; /api and /ws proxy to FastAPI on :8000
pnpm build         # dist/, served by FastAPI in production
pnpm typecheck
```

## What is on screen

- **Phone.** Apple's bezel PNG over a 402x874 screen. Inside: Framework7's iOS Messages, Messagebar and
  Navbar in dark mode, with an iOS status bar and home indicator. The phone icon in the header dials.
- **Dynamic Island.** Calls live there, as on a real iPhone: an incoming call expands it with accept and
  decline, an active call collapses to handset, timer and waveform, a tap unfolds speaker and end.
- **Orb.** Off (dim) with no call, on while connected, speaking while the agent has the floor, driven by
  an output level. Under it, the transcript writes in word by word, then mute and hang up.

Today all of it runs on a mock (`src/mock/`): scripted texts, an incoming call every so often, a
scripted call exchange. The real backend contract (`src/state/`, `src/transport/`, `src/audio/`,
`src/types.ts`) is implemented against `docs/proposed-design/14-frontend-contract.md` but not yet
connected to the UI; wiring it in replaces the mock hook and nothing else.

## Layout

```
src/
  App.tsx                    composes the stage from the components below
  components/
    phone/                   IPhone17Pro (bezel), MessagesScreen (Framework7), CallIsland, vendored status bar
    orb/                     VoiceOrb (orb-ui), Transcript
    call/                    CallControls (mute, hang up)
    stage/                   Stage: phone left, orb column right
    ui/dynamic-island.tsx    the island shell (vendored)
  mock/                      the demo choreography: useMockPhone, data
  state/ transport/ audio/   backend contract: reducer, WebSocket client, PCM worklets (not wired yet)
  index.css                  Tailwind + Framework7 in cascade layers
```

Framework7 is imported only under `components/phone/`. Its stylesheets load into a cascade layer that sits
above Tailwind's base reset and below its utilities (`src/index.css`), so neither library's globals leak
into the other's components.

## Third-party pieces

| Piece | Source | License |
|---|---|---|
| iPhone 17 Pro bezel | Apple Design Resources | Apple's license allows mockups but not redistribution, so the PNG is gitignored and fetched by `scripts/fetch-bezel.sh` |
| Messages thread, composer, navbar | [Framework7](https://framework7.io) 9, iOS theme | MIT |
| Icons inside the phone | [Framework7 Icons](https://framework7.io/icons/) | MIT |
| Status bar, keyboard | [zoewu-creator/texting-ui-templates](https://github.com/zoewu-creator/texting-ui-templates), vendored in `components/phone/vendor/` | MIT |
| Dynamic Island shell | [beUI](https://beui.dev/components/blocks/dynamic-island), vendored in `components/ui/` | MIT |
| Voice orb | [orb-ui](https://orb-ui.com), cloud theme | MIT |
| Motion, icons outside the phone | [motion](https://motion.dev), [lucide](https://lucide.dev) | MIT, ISC |
