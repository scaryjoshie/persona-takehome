# Turn-taking policy (text)

The request/response frame ("user acts, model responds once") is what makes bots feel like bots. The person-feel frame is event-driven: things arrive, and separately something decides whether now is a good time to respond and with what. Two layers:

- **Timing layer, in code.** Debounce, typing, caps. Answers "has the user probably finished a thought?" without a model.
- **Judgment layer, in the model.** When the timing layer fires, the model picks: reply, reply with a question that fits a partial thought, or hold and say nothing. Hold is a legitimate output, and only works if the prompt names it.

Full research in [research/turn-taking.md](research/turn-taking.md). The default policy adopted from it:

| Rule | Value |
|---|---|
| Quiet window after last message (restart on each message) | 1.5 s |
| Commit early if buffered text looks complete (question mark, closed clause) and no typing signal | 0.7 s |
| Extend if text ends in a fragment (trailing "and", "so", ellipsis) | up to 4 s |
| Extend while inbound typing indicator is active | up to 5 s past the last message, then ignore |
| Hard cap, whichever first | 8 s from first buffered message, or 6 messages |
| Media / attachments | flush immediately |
| New message mid-generation, no bubble sent yet | cancel and regenerate with the full buffer |
| New message after a bubble went out | finish that bubble, drop the rest, start a new turn |
| Run with external side effects in flight | never cancel; steer (defer until the boundary) |
| Deliver-time guard | before each bubble, re-check latest inbound id; if superseded, discard remainder |
| Model output | JSON list of 0 to 4 messages |
| Per-bubble typing delay | min(0.8 s + 40 ms per character, 3 s), 0.4 s gap between bubbles |
| Zero messages | legal; prompt says hold when user is clearly mid-thought, prefer a short question that fits either reading when intent is ambiguous |
| Held-thought fallback | if the model holds and nothing arrives for 20 s, run once more with a "the user went quiet" hint |
| Proactive messages | same agent, explicit send-or-wait decision; none within 60 s of user activity |

Why a pure timer reset is wrong: a continuously typing user starves the reply forever (a documented bug in two issue trackers), and a typed-then-deleted draft leaves a dangling wait. Hence debounce plus cap.

Why "generate, hold delivery, discard if superseded": cancelling throws away work, but delivering a reply that ignores the newest message is worse. The typing indicator covers the second or two of cost.

The "short floor, long ceiling, classifier decides in between" idea is borrowed from voice end-of-turn detectors (LiveKit, Pipecat Smart Turn, OpenAI semantic VAD). Nobody documents applying it to chat yet, so it is a small differentiator worth stating in the write-up.
