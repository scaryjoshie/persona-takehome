# Research report: turn-taking and message handling for iMessage-style text agents (2026-09-25)

Produced by a research subagent on 2026-09-25 from web sources. Reproduced as received. Verify anything load-bearing before relying on it.

## 1. Message batching / debouncing

What's typical
- Every serious channel-agent framework ships a per-conversation "quiet window" (trailing-edge debounce): a timer restarts on each inbound text and the batch flushes when it expires. Windows cluster in two bands. Agent gateways use sub-second to a few seconds: Hermes Agent uses 0.6 s (Telegram/Discord) and 0.8 s (WhatsApp/Weixin, because delivery is "more spread out"), with a longer 2.0 s window when a message is near the platform size limit (a paste being split). OpenClaw defaults to a 300 ms Telegram window and documents per-channel examples of 1500 ms (Slack, Discord) and 5000 ms (WhatsApp); iMessage uses the generic policy. n8n/Redis community patterns for WhatsApp sit at 3 s, and one no-code blog recommends 60 s for support inboxes (too long for a conversational assistant, but shows the range).
- Media and attachments flush immediately; only text is debounced (Hermes, OpenClaw).
- Hard caps are standard in mature implementations: the n8n message-debounce node exposes Max Messages (flush after N) and Max Wait Time (flush after T seconds from the first buffered message, "whichever is met first"); OpenClaw's queue caps at 20 messages and summarizes drops. Two issue trackers document the failure when the cap is missing: a 2 s debounce with no ceiling meant "a learner who keeps sending is never answered."
- Batch joining: concatenate oldest-first with newline or blank-line separators, as one user turn (Mastra passes batched messages as context.skipped and joins with blank lines; Hermes and n8n use plain concatenation). Some frameworks treat the first message specially (respond immediately to the first, then debounce).
- Typing indicators as input: available on Apple Messages for Business (typing_start on each keystroke start, typing_end when the field is cleared, MSP may time out after 60 s), Slack (user_typing RTM event), and Twilio Conversations SDK (signal sent at most every 5 s by default, configurable). WhatsApp Cloud API has no inbound composing webhook; typing is outbound only (25 s max or until you reply). Consumer iMessage via a Mac bridge exposes typing state but no framework found uses it to extend the debounce; the Interconnected post lists "detection of human message sequences" as unsolved.
- Mastra: channel concurrency modes are queue (default), debounce, batch/burst, and skip. Botpress has no built-in aggregation; users hand-roll a 10 s inactivity wait. Chatwoot, Intercom Fin, Voiceflow and Sendbird publish nothing on inbound batching; Intercom only shows a typing indicator after the first agent reply.

Sources
- https://docs.openclaw.ai/concepts/messages
- https://github.com/NousResearch/hermes-agent/issues/22602
- https://hermes-agent.nousresearch.com/docs/user-guide/messaging/discord
- https://github.com/uaiautomacao/n8n-nodes-message-debounce
- https://github.com/lengocanh2005it/wispace-bot/issues/1111
- https://n8n.io/workflows/19116-debounce-and-buffer-whatsapp-ai-replies-with-redis-and-google-gemini/
- https://growwstacks.com/blog/n8n-debounce-pattern-for-natural-ai-replies
- https://github.com/mastra-ai/mastra/issues/22496 and https://mastra.ai/docs/channels
- https://discord.botpress.com/t/14144100/allowing-multiple-user-chats-before-processing-with-ai
- https://register.apple.com/resources/messages/msp-rest-api/common-specs
- https://developers.facebook.com/docs/whatsapp/cloud-api/typing-indicators/
- https://docs.slack.dev/reference/events/user_typing/
- https://www.twilio.com/docs/conversations/typing-indicator
- https://www.intercom.com/help/en/articles/258-real-time-messaging-explained

## 2. Cancel vs finish

What's typical
- The field has converged on four named policies. OpenClaw's messages.queue setting: steer (inject into the active run at the next step boundary, default), followup (run after the active turn), collect (batch into one later turn), interrupt (abort and restart with the newest prompt). Hermes exposes queue / steer / interrupt via /busy. OpenAI Agents SDK offers immediate vs after_turn cancellation. Claude Code uses boundary-aware queueing (messages land after the current tool call).
- Steer is the coding-agent default because in-flight tool work has side effects. For a pure chat reply with no side effects, interrupt-and-regenerate is cheap and the newest context wins; the Zylos analysis stresses that "stopping computation and repairing effects are separate obligations," so cancel only when nothing external has been committed.
- The third option (generate, hold delivery, discard if superseded) is what the n8n/Redis flows do implicitly: after the wait, re-check the latest message id and abort if a newer one arrived, so only the freshest execution delivers. A per-sender lock (30 s TTL) prevents parallel replies.
- Not stopping is a documented bug: without cancellation "users get two responses racing instead of the old one stopping."

Sources
- https://docs.openclaw.ai/concepts/messages
- https://zylos.ai/research/2026-08-25-agent-message-preemption-queueing-single-loop/
- https://github.com/anomalyco/opencode/issues/32157
- https://github.com/mrveiss/AutoBot-AI/issues/16805
- https://n8n.io/workflows/19116-debounce-and-buffer-whatsapp-ai-replies-with-redis-and-google-gemini/

## 3. Multi-bubble replies

What's typical
- Two mechanisms: (a) ask the model for a list of messages (structured output or a delimiter such as a blank line or a sentinel), or (b) split a single reply on paragraph boundaries post hoc. Framework chunking (OpenClaw block streaming) splits on channel limits and avoids breaking code fences but is not stylistic. Poke's rapid-fire short messages are described as a personality-layer behavior, not core architecture.
- Per-bubble delay scaled to length is standard in bot builders: Landbot defaults to 100 words per minute with a max-delay cap that overrides the formula; chatbot.com defaults to 2 s per message, 0.1 to 10 s range; HCI work uses roughly 50 ms per character (Holtgraves and Han) and a "type at 100 wpm but never more than 3 s" rule. Gnewuch et al. (ECIS 2018) found dynamic delays computed from response and prior-message complexity raise perceived humanness, social presence and satisfaction versus instant replies.
- Show typing between bubbles. Apple's guidance for bots: "use only a 1-second typing indicator before each message," send typing_start, wait for the 200, then send the text. WhatsApp typing auto-clears on send or after 25 s.

Sources
- https://help.landbot.io/article/bycwxj31p7-typing-emulation
- https://www.chatbot.com/help/bot-responses/set-up-delay-for-responses/
- https://aisel.aisnet.org/ecis2018_rp/113/
- https://imeugenia.medium.com/how-to-teach-chatbot-to-type-4ab74d3f0e8e
- https://register.apple.com/resources/messages/msp-rest-api/common-specs
- https://www.shloked.com/writing/openpoke
- https://medium.com/@incapablepolygon/writing-a-llm-based-telegram-bot-that-texts-like-a-person-would-944853849075

## 4. The hold / no-reply option

What's typical
- Explicit "do nothing" tool: OpenPoke's interaction agent has a wait tool that silently discards outputs judged redundant or irrelevant, and Poke's proactive notifications (email monitor, triggers) are routed through the same agent, which "weaves them into your conversation." Hermes stays silent by default in channels unless mentioned, and ignores messages mentioning other users.
- Scored participation instead of reply-per-message: Interconnected's multiplayer chat computes an "enthusiasm" score per bot per message (direct address = 9; self-selection 0 to 9; reply only above a threshold of about 5), with inhibitions for being mid-sequence from one human or when another pair is in an active exchange. The CHI 2025 Inner Thoughts framework runs a covert thought loop triggered on_new_message and on_pause (10 s of silence), rates intrinsic motivation 1 to 5 across eight heuristics (relevance, information gap, urgency, coherence, etc.), speaks only above imThreshold, uses a higher interruptThreshold to cut in, and refrains while its trigger queue is non-empty. CHI 2026 "Read the Room" studies ChatGPT group-chat Auto-Reply, which "does not respond to every message" but decides when to interject.
- Text overlap: OverlapBot (2025) lets the bot interject while the user is typing (backchannels, pre-emptive answers); users rated it more communicative and immersive. No production text-only end-of-turn classifier exists yet; practitioners fake it with the debounce plus a prompt that offers the model a no-reply option.

Sources
- https://www.shloked.com/writing/openpoke
- https://interconnected.org/home/2025/05/23/turntaking
- https://arxiv.org/abs/2501.00383
- https://dl.acm.org/doi/10.1145/3772363.3798392
- https://arxiv.org/abs/2501.18103
- https://hermes-agent.nousresearch.com/docs/user-guide/messaging/discord

## 5. Voice analogues

What's typical
- All three major stacks replaced fixed silence timeouts with a semantic classifier that scales the wait. LiveKit: min/max endpointing 0.5 s / 3.0 s by default, 0.3 s / 2.5 s with the audio turn detector; the older text-transcript model ran in 50 to 160 ms with ~99% true positive rate. Pipecat Smart Turn v3.2: 8 MB int8, 10 to 100 ms on CPU, runs only when VAD sees silence, on up to 8 s of context. OpenAI semantic_vad: a completion probability sets the timeout, with eagerness low/medium/high/auto tuning the maximum wait.
- The transferable idea is "short floor, long ceiling, classifier decides in between": commit fast when the text looks complete (question mark, closed clause), extend when it looks incomplete (trailing "and", "so", ellipsis, lowercase fragment). The text version of LiveKit's detector is exactly a transcript classifier, so it or a small prompt-based equivalent can be applied to a message buffer. No vendor documents doing this for chat yet.

Sources
- https://docs.livekit.io/agents/logic/turns/turn-detector/
- https://github.com/pipecat-ai/smart-turn
- https://developers.openai.com/api/docs/guides/realtime-vad
- https://www.assemblyai.com/blog/turn-detection-endpointing-voice-agent

## Recommended default policy (iMessage-style assistant)

- Debounce inbound text per conversation with a 1.5 s quiet window (restart on each message); flush media immediately.
- Semantic floor and ceiling: if the buffered text ends in a question mark or a complete clause and no typing signal is present, commit after 0.7 s; if it ends in a fragment, extend up to 4 s.
- Typing signal: while an inbound typing_start is active, extend the window by up to 5 s past the last message; ignore typing after that (Apple times out at 60 s, users abandon drafts).
- Hard caps: flush at 8 s after the first buffered message or 6 messages, whichever comes first, so a continuously typing user always gets a reply.
- New message mid-generation: cancel and regenerate with the full buffer if no bubble has been sent; if one or more bubbles are already delivered, finish the current bubble, drop the rest, and start a new turn with the new message. Never cancel a run with external side effects; steer it instead.
- Deliver-time guard: before each bubble send, re-check the latest inbound id; if superseded, discard the remainder.
- Model returns a JSON list of 0 to 4 messages; each bubble is delivered with typing shown for min(0.8 s + 40 ms per character, 3 s), and a 0.4 s gap between bubbles.
- Zero messages is a legal output: the prompt tells the model to hold when the user is clearly mid-thought (trailing conjunction, "hold on", "one sec") and to prefer a short question that fits either reading over a full answer when the intent is ambiguous.
- If the model holds, arm a 20 s fallback: if nothing else arrives, run once more with a "the user went quiet" hint so a held thought is never lost.
- Proactive messages go through the same agent with an explicit wait-or-send decision and a no-reply option; no proactive sends within 60 s of user activity to avoid colliding turns.
