# Open questions

Decisions still to make, with the current default where one exists.

| Question | Default | Notes |
|---|---|---|
| The value moment after collection: what does the agent do, and what is in the mock inbox? | none | Needs a decision before slice 4; determines the mock data. See 10. |
| Persist typing signals for the harness, or keep ephemeral? | ephemeral | Persisting is noise in the log; the harness can synthesize typing deltas. |
| Does a user-initiated call button exist in the demo? | yes | Cheap, and reviewers will try to call back after a drop. |
| Do decision records live in the store or only in a side channel to the debug panel? | store | Makes the debug panel just another view, and the harness can assert on them. |
| Copy cado's `libs/llm` or reuse the style only? | style only | User preference. |
| Jev access | apply now | Waitlist; rule-based decider ships regardless. |
| Real inbox for reviewers (testing-status project with their emails) or mock only? | mock only | Ask the company if the real inbox matters to them. |
| Deadline and hours budget | unknown | Shapes how much of slice 5 happens. |
| The user's own "Decisions: Everything will be..." note was cut off | unknown | Worth finishing. |
| Session refresh mid-call: end the call on WebSocket close immediately, or after a short grace? | immediate | The peer connection died with the tab anyway. |
| Voice layer: `gpt-live-1` (07b, 15) or `gpt-realtime-2.1` (07)? | gpt-live primary, spike at slice 3 | pydantic-ai adapter landed 2026-09-25 (v2.51.0). |
| Agent model: one OpenAI model for text and Live backend, or OpenRouter for text? | one OpenAI model | Makes voice an extension of the text agent (15). OpenRouter stays for auxiliary. |
| Exact OpenAI agent model id | `gpt-6-sol` placeholder | Confirm current best fast model; `AUTO_BACKEND_MODEL` in pydantic-ai names it. |
| Mid-call agent rename: context message vs raw session.update through the provider session? | context message | Simpler; upgrade if it reads badly. |
