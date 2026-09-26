# Hosting

## Why a long-running process

The handlers are pure functions of the store, which is the stateless part and survives any hosting choice. Four things in the shell around them are inherently stateful:

- The sideband WebSocket to OpenAI, open for the whole call.
- The push connection to the browser.
- Debounce timers, which need something to wake up in 1.5 seconds.
- Per-user serialization, which needs one owner of the queue.

Serverless functions cannot hold an outbound socket open or wake on a timer. Bolting on Redis for the queue, a scheduler for timers, and an always-on voice bridge is the long-running process with extra steps.

FastAPI under uvicorn is already a long-running process: one asyncio event loop interleaving every user's I/O. Multi-user is a dict from user to a per-user actor (store cache, queue, worker task, timers, live realtime handle). One modest box handles hundreds of simultaneous onboardings. Run uvicorn with one worker, since state lives in the process.

## Options

| | One Node/Python process | Cloudflare Durable Objects | Serverless functions plus add-ons |
|---|---|---|---|
| Fits the design | Yes | Yes, almost exactly | Only with a separate voice bridge |
| Holds sideband socket | Yes | Yes | No |
| Timers | Native | Alarms API | External scheduler |
| Per-user isolation | A dict entry | One object per user, built in | Redis lock |
| Persistence | SQLite write-through | Built-in storage | Redis or database |
| Multi-user scale | Hundreds per box | Effectively unlimited | Unlimited |
| Learning curve | Low | Medium, non-Node runtime | High for this shape |
| Local dev | One command | Wrangler dev | Painful |

Durable Objects are the design made into a product (one addressable actor per user holding sockets, with alarms and storage). Toyo, the closest public comparable to Persona, runs on exactly this. Recommendation: build the actor boundary cleanly, run it as one process for the take-home, and note in the write-up that it maps directly onto Durable Objects for production.

## Costs (approximate, from memory, verify before committing)

| Provider | Smallest always-on option | Roughly per month |
|---|---|---|
| Fly.io | Shared CPU, 512 MB | $3 to $6 |
| Railway | Hobby plan | $5 including usage credit |
| Render | Starter instance | $7 |
| Hetzner VPS | Smallest cloud box | About $4 |
| Cloudflare Workers paid (for Durable Objects) | Flat | $5 |

Avoid free tiers that sleep on inactivity: a reviewer's first request would hit a cold start and the call demo dies. The real cost is the OpenAI bill: Realtime audio is priced per minute, so a few dozen five-minute test calls cost more than a month of hosting. Set a spend cap before the stress-testing phase.
