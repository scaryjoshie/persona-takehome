# Routing and the decider

## Deltas

A delta is any routable input: something that arrived from outside the current floor holder.

```python
Delta = UserMessage | Typing | CallEvent | GmailEvent      # TaskResult later, when a background task exists
class Typing(BaseModel): kind: Literal["typing"]; seconds: float   # coalesced, never raw keystrokes
```

Voice audio is never a delta; the Realtime session handles listening and barge-in natively. Voice *transcripts* are events (record-only), not deltas.

## Runs

A run is one in-progress response by whichever handler holds the floor. It is the unit that can be interrupted, and it is transient, never stored.

- **Text:** starts when the debounce fires and the agent is invoked; ends when the last bubble is delivered.
- **Voice:** one response from the Realtime model, from response-created to response-done, including tool execution in the middle.

```python
class Run(BaseModel):
    medium: Literal["text", "voice"]
    started: datetime
    side_effect_in_flight: bool          # a tool with external effect is mid-call
    last_agent_turn_was_question: bool   # what makes the typing case work
```

When there is no run, there is nothing to interrupt, and the decision layer is skipped entirely.

## Verbs

```python
Verb = Literal["interrupt", "absorb", "defer"]
```

| Verb | Voice host (GPT-Live via pydantic-ai) | Voice host (Realtime fallback) | Text host |
|---|---|---|---|
| interrupt | `session.send(text)` as speakable commentary; Live works it in now (no cancel exists) | cancel response, inject item, request response | cancel generation task, drop unsent bubbles, re-run with full buffer |
| absorb | `session.send(text, respond=False)`, worded as an internal note | create conversation item, no response request | append to the debounce buffer / extend timer (non-message deltas only) |
| defer | wait for the inferred `RealtimeTurnCompleteEvent`, then `send` | queue until response-done, then inject and respond | let current bubbles finish, then start a new turn |

On Live, a voice `Run` is inferred: it starts at the first output transcript fragment and ends at the inferred turn-complete event; `side_effect_in_flight` is true while a delegation is outstanding. Audio barge-in is handled by Live itself and never reaches the router.

Absorb is a voice-heavy verb. On the text host a message arriving mid-generation should interrupt or defer, never be silently appended.

The turn-taking research found the field converged on four named policies (steer, followup, collect, interrupt). These are our three verbs plus the null-run case.

## Routing context and policy

The policy is a function, not a table. Each routing pass packages everything relevant into one object, which is also what gets logged as the decision event.

```python
class RoutingContext(BaseModel):
    user: User
    trigger: Delta                 # the thing that caused this pass
    run: Run | None
    floor: Literal["text", "voice"]
    call: CallState
    recent: list[Event]            # last N, for the decider
    now: datetime

async def policy(ctx: RoutingContext, decider: Decider) -> Verb:
    # early returns for fixed cases, e.g.
    #   user_message  -> interrupt   (a message always wins)
    #   call ended    -> interrupt   (text must respond to a hangup)
    # then fall through to the decider for judgment cases:
    #   typing during a voice run, gmail connected mid-sentence, ...
```

The whole routing step:

1. Store appends the delta and hands it to the router.
2. Router drops it if its origin is the floor holder; otherwise picks the head by call state.
3. No run: the handler starts one. Done.
4. Run exists: policy decides. Fixed cases return a verb directly; judgment cases call the decider.
5. Handler executes the verb in its medium's way.
6. A `Decision` event is appended (trigger kind, verb, who decided, confidence, latency) for the debug panel and the harness.

Interrupt is only safe at step boundaries. If a side-effecting tool is in flight, interrupt degrades to defer until the tool returns, then the policy is asked again. (The "steer" policy from the research.)

## Walkthrough

| Situation | Run | Policy | Result |
|---|---|---|---|
| User texts, nothing happening | none | skipped | text handler starts a run |
| User texts while bubbles are generating | text | fixed: interrupt | cancel task, re-run with the new message included |
| User types for 2 s during a call, agent just asked a question | voice, last turn was a question | ask | decider: interrupt; agent says "looks like you're typing, go ahead" |
| User types for 2 s during a call, agent mid-explanation | voice | ask | decider: absorb; agent finishes, knows they're typing |
| Gmail connects mid-sentence on the call | voice | ask | decider: defer; agent finishes the sentence then reacts |
| Gmail connects, agent silent | none | skipped | voice handler starts a run; agent reacts immediately |
| User hangs up | any | fixed: interrupt on the text head | text handler responds to the ended event |

## The decider

```python
class Decider(Protocol):
    async def decide(self, ctx: RoutingContext) -> Decision: ...
```

Two implementations behind the same interface:

- **Rule-based** (ships first). Handles the typing case with "typing for more than two seconds while the last agent turn was a question."
- **Jev** (TypeSafe AI's "System One" decision model; early access, waitlist). Non-autoregressive; takes state plus several typed questions in parallel (choice / score / yes-no) and returns calibrated probabilities. Reported 70 to 500 ms end to end, about $0.04 per million input tokens, output free. No public material mentions turn-taking, but it is exactly the "classifier in between" the turn-taking research says nobody has built for text.

The Jev wrapper owns context budgeting: it renders the routing context into a compact view under a token limit. The router does not know Jev exists. Pattern from cado: small-model task under a hard timeout with a deterministic fallback to the rule.

Where Jev would add the most value, in order:

1. End-of-thought on the text buffer, setting the wait between the 0.7 s floor and the 4 s ceiling.
2. Urgency of injected events during a call (react now vs absorb).
3. Early-graduation detection ("did the user just state a concrete task?").
4. Hold vs reply (optional; the main model can do this inline with its zero-message option).

Set a higher threshold for voice interrupting itself than for text: a cut-off sentence is jarring, a delayed text reply is not.

Sources: https://typesafe.ai/blog/introducing-system-one-models-and-jev , https://www.langchain.com/blog/building-a-harness-with-jev
