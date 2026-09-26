# Frontend

The onboarding demo: an iPhone showing an iMessage thread with the assistant, and a voice orb beside it
that lights up during calls. Vite, React 19, TypeScript, Tailwind 4.

```
pnpm install
pnpm fetch-bezel   # downloads Apple's iPhone 17 Pro bezel into public/bezels/ (gitignored, see below)
pnpm dev           # http://localhost:5173; /api and /ws proxy to FastAPI on :8000
                   # (cd backend && uv run uvicorn app.main:app --reload)
pnpm gen-types     # after the backend's schema changes; see "Wire types" below
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

It runs against the backend: the phone-number screen creates or resumes the user, the thread
and typing come from the server's event stream, and the composer sends typing signals. A number
that has never texted starts with "Hey, what's a Persona?" in the composer. Add `?mock` to the URL
to run on an offline stand-in instead (`src/mock/`), which needs no API keys.

Calls reach the server but have no audio yet: the backend's audio socket is the next slice, so a
call attempt fails cleanly with `audio_socket` and the agent carries on by text.

## Wire types

`src/protocol.gen.ts` is generated from the backend's JSON Schema and never edited by hand:

```
cd backend && uv run python -m app.web.schema > ../frontend/src/schema.json
cd frontend && pnpm gen-types
```

`src/types.ts` re-exports it and adds only what the schema does not cover yet (live transcript
partials, which arrive with the voice layer).

## Layout

```
src/
  App.tsx                    entry, then the stage for the live or mock conversation
  components/
    phone/                   IPhone17Pro (bezel), MessagesScreen (Framework7), CallIsland, vendored status bar
    orb/                     VoiceOrb (orb-ui), Transcript
    call/                    CallControls (mute, hang up)
    stage/                   Stage: phone left, orb column right
    ui/dynamic-island.tsx    the island shell (vendored)
  conversation/              the Conversation interface the stage renders, and its live source
  state/                     reducer over snapshot + event stream, derived views (thread, transcript)
  transport/                 WebSocket client for /api/session, /ws and /ws/audio
  audio/                     mic capture and playback worklets, PCM16 mono 24 kHz
  mock/                      the offline stand-in behind ?mock
  protocol.gen.ts            generated wire types
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
| Audio message waveforms | [ElevenLabs UI](https://github.com/elevenlabs/ui) `Waveform` and `LiveWaveform`, vendored in `components/ui/` | MIT |
| Phone-number blanks | [input-otp](https://input-otp.rodz.dev) | MIT |
| Motion, icons outside the phone | [motion](https://motion.dev), [lucide](https://lucide.dev) | MIT, ISC |
