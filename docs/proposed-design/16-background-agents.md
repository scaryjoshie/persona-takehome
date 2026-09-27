# Background agents (jobs)

Written 2026-09-27. What's built on the `background-agents` branch, and what comes next.

## What a job is

Once onboarding is done, the chat agent can hand something that takes research or time to a background job: "find a thai place for saturday", "find a dentist with Tuesday slots". A job is its own pydantic-ai agent (`app/jobs/agent.py`, `prompts/job.md`) with web search and read access to their Gmail and calendar. It runs on its own, asks them something when it has to, and ends with what it found. It never talks to them directly: everything goes through the chat agent, so there is still one voice.

## How it fits the pipeline

Nothing new in routing or delivery. A job's life is four events (`app/jobs/events.py`):

| Event | Routed? | What happens |
|---|---|---|
| `job_started` | no | the agent's own doing |
| `job_asked` | yes | by text, a reply asks them; on a call, a note to the voice (interrupt, absorb or defer, as for any event) |
| `job_told` | no | their answer or change of plan, passed on with `tell_job` |
| `job_ended` | yes (not a cancel) | the chat agent tells them what it found |

## State, pausing and resuming

- The `job` table holds the goal, the status (running, waiting, done, failed, cancelled), the question it's waiting on, and its model messages as JSON.
- A question is a deferred tool call (`ask_user` raises `CallDeferred`): the run ends, the messages are saved, the job waits. `tell_job` starts the next run from those messages with their answer (`DeferredToolResults`).
- Something they say while it's running goes straight into the running conversation (`AgentRun.enqueue`).
- A question with no answer for a day cancels the job quietly.
- On startup, jobs that were running go again from their last saved messages; waiting ones keep waiting.
- Limits per run: 30 model requests, 5 minutes.

## Keeping questions from being forgotten

Open jobs come from the job table, not the recent events, and are rendered into "What you know" on every text reply and into the call's state note: "Background task X is waiting on their answer: ... Ask them when it fits." A question stays in front of the agent until it's answered, the job is cancelled, or it expires.

## Graduation

Jobs exist only after graduation. `graduate(first_action)` starts the first job on the first action. `start_job`, `tell_job`, `cancel_job` appear once they've graduated (text, and the call agent; never the voice). On a call, starting and cancelling take the two keys like any other action; `tell_job` doesn't, since it records what they said.

## Measured (live, 2026-09-27, gpt-6-sol with native web search)

- Research job alone: about 25 to 30 s. After an answer, the resumed run finished in about 7 s.
- Full text loop (ask, job asks back, they answer, result): first reply in 6 s, the job's question relayed at 17 s, the result at 35 s.
- Found and fixed: web search leaves citation markers in private-use characters in the summary; stripped before the chat agent sees it. The chat agent used `tell_job` for its own notes and chained a second job unasked; the tool descriptions and `jobs.md` now rule both out.

## Races and restarts

Every status change is one conditional update (`UPDATE job ... WHERE status IN (...)`): whoever's update took acts and records the event, everyone else backs off. No locks, so nothing ever waits on a job, and it holds across restarts.

- Two answers at once resume the job once; the second goes into the running conversation.
- A job ends once: a cancel racing a finish records one ending, and a job cancelled mid-run doesn't reopen as waiting.
- The answer that resumes a job is saved with that update, so a restart before the next run saves its messages resumes with it.
- Something said as a job finishes gets one more run instead of being dropped.
- A reset mid-run ends quietly (the rows are gone, so nothing is recorded).
- An open question's one-day expiry is re-armed at startup from when it was asked.

Fly runs one always-on machine, so restarts only come from deploys and crashes anyway. Jobs share the OpenAI key with texts and calls; many heavy jobs at once could hit rate limits and slow replies. Not capped yet.

## Next

1. Browser: Kernel cloud browsers driven by browser-use as a `browse(task)` tool; per-user site notes (what login worked, quirks); logins through Kernel's hosted page or a code from their Gmail. Account changes only with a yes to that exact change.
2. Then acting, with a yes first: booking, ordering up to checkout.
3. Composio for apps with OAuth (Slack), when needed.
