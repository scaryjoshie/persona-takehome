# The assignment

Source: take-home brief from Persona (https://yourpersona.com/band). Reproduced verbatim below.

> Persona has an onboarding process that you can try out today! The onboarding attempts to collect a name for the agent, a name for the user, a connected gmail, and something the user could use help with. It tries to do some of this over a call, but its also adaptive to text. Your job is to create an onboarding process that's able to fill these functions. It should attempt to collect all of the information listed above, and it should also attempt to do a phone call to collect everything besides the name of the agent. Some constraints:
>
> People won't play by the rules! You have to make a bot that is adaptive and conversational enough that it can withstand any sort of user error. This means things like call hangups, for example. I will be stress testing this.
>
> It shouldn't feel like a form. it should feel conversational, and you may even consider letting the user move through the process early. Think about what the onboarding is trying to do- show how we can provide value to the user. If they already have an idea of what they need, you might consider letting them "graduate" into the main experience early. At the same time, it should keep the user on track when it needs information, and gently steer them without being overbearing.
>
> It doesn't need to be on actual phone numbers, a web simulator with voice will suffice.

## Reading of the brief

Four slots across two channels:

| Slot | Channel |
|---|---|
| Agent name | Text, before the call |
| User name | Attempted on the call, text fallback |
| Connected Gmail | Attempted on the call (link goes by text), text fallback |
| Something they need help with | Attempted on the call, text fallback |

Three graded properties:

1. **Robust to rule-breaking.** Hangups are named explicitly. The reviewer will stress test.
2. **Conversational, not a form.** Early graduation for users who already know what they want.
3. **Steers gently.** Keeps the user on track without nagging.

The stated purpose: show how the product provides value. Data collection is the means, not the goal.

## What a single Realtime session with a good prompt does not cover

Most of what makes this feel good is prompt and conversation design. The parts that are not prompt work, and that the brief names, are:

- **Hangups.** When the user hangs up, the voice session ends without the model getting a turn. Something has to catch the disconnect and continue in text.
- **Text before and after the call.** The agent name is collected in text first, the call can be declined, and text must pick up after a drop. So the text conversation exists independently of the call and the two must share context.
- **Gmail.** A sign-in link plus a callback. The model needs a tool to send the link and an event when it succeeds.

Everything else (a deterministic "director", an adversarial harness, a debug panel) is optional and worth adding only where self-testing shows the simple version breaking. The debug panel is cheap enough to include from the start.
