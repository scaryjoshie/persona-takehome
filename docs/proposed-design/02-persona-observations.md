# Observations from Persona's real onboarding

Raw notes from using the product on 2026-09-25, followed by what each implies for our design. These are one person's experience on one day; some of the bugs may be transient.

## Problems observed

- **Cannot call and text at the same time in the first step.** It insists on moving to text to do Google sign-in instead of staying on the call so the user has constant reassurance.
- **Cannot access Canvas.** Not expected, but a self-building integrations idea might address this later. See [10-gmail-and-integrations.md](10-gmail-and-integrations.md).
- **Style drift on call-sent texts.** An em dash and a different writing style appeared in texts the agent sent while on a call. Likely the live voice endpoint makes the tool calls and was never given the text styling rules.
- **Texting during a call is not piped live.** The agent had to "check" a text sent during the call and said something like "what's up." Chat appears to be turned off or not injected while on a call. Unjustified "let me check that" is one of the most annoying things in voice.
- **Wrong provenance.** It falsely claimed it got the user's name from a chat message when it was said on the call. Likely call transcripts were not properly attributed.
- **Call context was empty.** When asked to hop on a call to help send an email, the agent had no idea what the call was for. The call context was not populated with the reason for calling.
- **Random silence and a crash-like hangup.** Possibly Realtime model behavior.
- **Email address loop.** After drafting, it asked for the email address repeatedly, ad infinitum. Sending an email by call was not possible in this session.
- **Sent two emails.** Via text, the agent sent one email without a subject, then "panicked" and sent a second with a subject. Its own explanation: a background agent produced a draft object, the user approved, and the send path apparently re-transcribed the draft rather than sending the persisted object.
- **A background-agent permission bug**, possibly just model behavior.

## Things that worked well

- Pre-filled contact info from the phone makes setup easier.
- **Waits while you type.** If you are mid-typing, instead of interrupting it predicts and asks a question that fits plausibly with waiting, like "what did you want the body to say."
- **Sends images of email drafts.** Genuinely nice.

## Should be able to

- Rename itself on the fly.
- Send texts in the same style regardless of whether it is on a call.

## What each problem implies for our design

| Observed | Design response | Where |
|---|---|---|
| Forced to text for Google | Link goes by text, call continues, "connected" event injected live into the call | 07, 10 |
| Style drift on call-sent texts | One prompt template shared by both handlers with a channel-specific tail; templated messages for structured content | 03 |
| "What's up" when texting during a call | Deltas injected into the live session under the floor rule | 05 |
| Wrong provenance for the name | Every event carries origin and channel; both prompts render the tags | 04 |
| Call did not know its task | Voice session instructions include the reason for the call; history seeded from the store | 07 |
| Endless "need the email address" | Slots written by tools and rendered into every prompt | 04 |
| Two emails sent | Tools operate on ids, never on re-typed content | 03 |
| Rename on the fly | Same set-name tool available in both channels; mid-call propagation | 07 |
