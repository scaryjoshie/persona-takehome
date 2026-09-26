# Events and types

## Event envelope

Everything in the store is an envelope with a typed payload, discriminated on `payload.kind`.

```python
Origin  = Literal["user", "text_agent", "voice_agent", "call", "google", "system"]
Channel = Literal["text", "voice", "system"]

class Event(BaseModel):
    seq: int                 # per-user monotonic, assigned by the store inside the consumer
    ts: datetime
    origin: Origin           # who produced it (used by the router's loop guard)
    channel: Channel         # where it happened (rendered into prompts for provenance)
    payload: Payload
```

## Payload union

```python
class UserMessage(BaseModel):     kind: Literal["user_message"];   text: str
class AgentMessage(BaseModel):    kind: Literal["agent_message"];  text: str; via: Literal["text", "voice"]   # a bubble that was sent
class VoiceUtterance(BaseModel):  kind: Literal["voice_utterance"]; speaker: Literal["user", "agent"]; text: str | None; item_id: str | None; inferred: bool
class ToolCall(BaseModel):        kind: Literal["tool_call"];      name: str; args: dict; result: dict | None
class SlotChanged(BaseModel):     kind: Literal["slot_changed"];   slot: str; old: Any; new: Any
class CallEvent(BaseModel):       kind: Literal["call"];           phase: Literal["ringing", "declined", "connected", "ended"]; reason: str | None; call_id: str | None
class GmailEvent(BaseModel):      kind: Literal["gmail"];          phase: Literal["link_sent", "connected", "failed", "skipped"]; email: str | None
class Decision(BaseModel):        kind: Literal["decision"];       trigger_kind: str; verb: str; by: Literal["rule", "jev", "model"]; confidence: float; ms: int
class Graduated(BaseModel):       kind: Literal["graduated"]

Payload = Annotated[Union[...all of the above...], Field(discriminator="kind")]
```

Deliberately **not** events:

- **Typing signals.** Ephemeral. They go to the router but are never appended. (Open question: persist them for the harness? See [13-open-questions.md](13-open-questions.md).)
- **Partial transcript deltas.** Only completed utterances are stored. Word-by-word display in the call card, if wanted, is a transient socket message.
- **Audio.** Never exists server-side.

## Slots

Separate from the log. Written only by tools. `None` means "not collected."

```python
class Slots(BaseModel):
    agent_name: str | None = None
    user_name: str | None = None
    gmail: Literal["not_asked", "link_sent", "connected", "skipped"] = "not_asked"
    gmail_email: str | None = None
    help_need: str | None = None
    graduated: bool = False

    def missing(self) -> list[str]: ...    # rendered as a "still need: ..." line in every prompt
```

The "still missing" line is the entire steering mechanism. No director logic beyond stating facts to the model.

## Storage unit

Store the atomic unit each source gives us:

- **Text:** one row per bubble, in either direction.
- **Voice (GPT-Live):** one row per *inferred* turn. Live sends transcript fragments with no item ids and no turn markers; pydantic-ai groups them into a `SpeechPart` per turn, inferring the boundary from silence (`openai_live_turn_silence_ms`). Store one row per `SpeechPart`, with `inferred: true`, and keep `item_id` nullable. A long dramatic pause can split one utterance into two rows; the render-time merge rule below repairs that.
- **Voice (Realtime fallback):** one row per completed conversation item keyed by `item_id`; append a placeholder on item-created (in order) and fill text on transcription-completed (may be out of order).

## Rendering and merging

Nothing is stored as "context." Context is rendered from rows at prompt time by view functions. Merging is a pure function of the rows:

- Consecutive rows from the same speaker on the same channel with nothing between them merge into one message. Three rapid texts render as one user turn with line breaks; two voice utterances in a row become one note.
- The merge rule can change without touching data.

Mapping to pydantic-ai message history for the text handler (the cado "note rows" pattern):

- User texts and agent bubbles become ordinary user and assistant messages, so the model sees its own prior turns as its own.
- Voice utterances and system events become bracketed note rows carried as user-role messages. That is where channel tagging lives.

```
user:      hey
assistant: hey! I'm your new assistant. what should I call you?
user:      [note: call started 14:03, reason: collect name and what they need help with]
           [note: on call, user said: it's Siobhan, S-I-O-B-H-A-N]
           [note: on call, agent said: got it, Siobhan. what's been eating your time lately?]
           [note: Gmail connected as siobhan@gmail.com]
           [note: call ended 14:05, reason: user hung up]
```

Seeding the voice session uses the same mapping onto Realtime conversation items: user and assistant items for text turns, notes as user items with the bracket prefix. Slots and the "still missing" line go into instructions, not history.

## Type safety

- Pydantic discriminated unions with a literal `kind` field.
- `match` statements with `assert_never` under pyright, so a new payload or delta kind fails type checking until it has a policy entry and a verb implementation.
- Runtime validation only at the two untrusted boundaries: Realtime events coming in, and decider (Jev) responses coming back.
- SQLModel rows store `kind` plus a JSON payload column, validated into the union on read via a `TypeAdapter`.
