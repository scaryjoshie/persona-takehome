import type { ThreadMessage } from "../components/phone/MessagesScreen";
import type { TranscriptLine } from "../components/orb/Transcript";
import type { TranscriptPartial, WireEvent } from "../types";
import type { Reaction } from "../components/phone/Tapback";

/**
 * The texts in both directions, as the phone shows them, with their tapbacks and quoted replies.
 * `draftImage` gives the picture of an email draft version, since a phone receives drafts as images.
 */
export function threadMessages(
  events: WireEvent[],
  draftImage: (ref: string, version: number) => string,
): ThreadMessage[] {
  const reactions = reactionsBySeq(events);
  const messages = messagesOf(events, draftImage);
  const byId = new Map(messages.map((m) => [m.id, m]));
  return messages.map(({ replyToId, replyToText, ...m }) => {
    const original = replyToId ? byId.get(replyToId) : undefined;
    const replyTo = original
      ? { id: original.id, side: original.side, text: original.text }
      : replyToId && replyToText
        ? { id: replyToId, side: "received" as const, text: replyToText }
        : undefined;
    return { ...m, ...(reactions.has(m.id) && { reactions: reactions.get(m.id) }), ...(replyTo && { replyTo }) };
  });
}

/** Each message's current tapbacks: one per reactor, the latest winning, removals applied. */
function reactionsBySeq(events: WireEvent[]): Map<string, Reaction[]> {
  const out = new Map<string, Reaction[]>();
  for (const e of events) {
    const p = e.payload;
    if (p.kind !== "reaction") continue;
    const key = String(p.target_seq);
    const others = (out.get(key) ?? []).filter((r) => r.by !== p.by);
    out.set(key, p.removed ? others : [...others, { emoji: p.emoji, by: p.by }]);
  }
  return out;
}

type Unresolved = ThreadMessage & { replyToId?: string; replyToText?: string | null };

/** Each thread message, with the id (and text) of the message it replies to, resolved afterwards. */
function messagesOf(events: WireEvent[], draftImage: (ref: string, version: number) => string): Unresolved[] {
  return events.flatMap((e): Unresolved[] => {
    const p = e.payload;
    const base = { id: String(e.seq), ts: e.ts };
    switch (p.kind) {
      case "user_message":
        return [{ ...base, side: "sent", text: p.text, replyToId: idOf(p.reply_to), replyToText: p.reply_to_text }];
      case "agent_message":
        return [{ ...base, side: "received", text: p.text, replyToId: idOf(p.reply_to) }];
      case "voice_note": {
        const voice = { src: voiceNoteUrl(p.audio_id), durationMs: p.duration_ms ?? 0, transcript: p.transcript };
        return [{ ...base, side: "sent", text: "", voice, audioId: p.audio_id }];
      }
      case "email_draft":
        // Every version arrives as its own picture, like an updated screenshot; texts can't be
        // edited. The agent confirms a sent email by text, so sent versions show nothing.
        if (p.status !== "draft") return [];
        return [
          {
            ...base,
            side: "received",
            text: "Email draft",
            image: { src: draftImage(p.ref, e.seq), alt: `Email draft: ${p.subject || "no subject"}` },
          },
        ];
      case "contact_card":
        return [{ ...base, side: "received", text: p.name, contact: { name: p.name } }];
      default:
        return [];
    }
  });
}

function idOf(seq: number | null): string | undefined {
  return seq === null ? undefined : String(seq);
}

/**
 * Utterances since the current call connected, then any turn still being spoken. GPT-Live infers
 * turn boundaries from silence, so it can cut one sentence into two turns ("Hi" / ", you can call
 * me Sam"). A turn joins the previous line from the same speaker when it starts with punctuation or
 * that line hadn't ended a sentence ("get my" / "taxes done"). Case says nothing: the voice's own
 * transcripts are all lowercase.
 *
 * Live also records a turn when its speaker finishes, so a long agent line lands after the "yeah"
 * said halfway through it. Turn ids ("agent-3", "user-4") number both speakers in spoken order.
 */
export function transcriptLines(
  events: WireEvent[],
  partials: Partial<Record<string, TranscriptPartial>>,
): TranscriptLine[] {
  const start = events.findLastIndex((e) => e.payload.kind === "call" && e.payload.transition === "connected");
  if (start < 0) return [];

  const turns: TranscriptLine[] = [];
  const recorded = new Set<string>();
  for (const e of events.slice(start)) {
    const p = e.payload;
    if (p.kind !== "voice_utterance" || !p.text) continue;
    recorded.add(p.turn_id);
    turns.push({ id: p.turn_id, speaker: p.speaker, text: p.text });
  }
  // A final fragment can arrive after its utterance was recorded; the recorded line wins.
  for (const p of Object.values(partials)) {
    if (p && !recorded.has(p.turn_id))
      turns.push({ id: p.turn_id, speaker: p.speaker, text: p.text, partial: !p.final });
  }

  const lines: TranscriptLine[] = [];
  for (const turn of spokenOrder(turns)) {
    const text = turn.text.trim();
    const prev = lines.at(-1);
    const punctuated = /^[,.;:!?]/.test(text);
    if (text && prev && prev.speaker === turn.speaker && (punctuated || !/[.?!]$/.test(prev.text))) {
      lines[lines.length - 1] = {
        ...prev,
        text: `${prev.text}${punctuated ? "" : " "}${text}`,
        partial: turn.partial,
      };
    } else if (text) {
      lines.push({ ...turn, text });
    }
  }
  return lines;
}

/** Sorts numbered turns into the slots they hold; a turn without a number keeps its place. */
function spokenOrder(turns: TranscriptLine[]): TranscriptLine[] {
  const numbered = turns.filter((t) => turnIndex(t.id) !== null);
  numbered.sort((a, b) => turnIndex(a.id)! - turnIndex(b.id)!);
  let next = 0;
  return turns.map((t) => (turnIndex(t.id) === null ? t : numbered[next++]));
}

/** The spoken-order number in a Live turn id ("agent-3" → 3), or null for ids without one. */
function turnIndex(id: string): number | null {
  const m = /^(?:agent|user)-(\d+)$/.exec(id);
  return m ? Number(m[1]) : null;
}

/** Whether this number has never texted: it starts with an empty thread and a suggested first message. */
export function isNewUser(events: WireEvent[]): boolean {
  return !events.some((e) => e.payload.kind === "user_message");
}

/**
 * The label under the user's latest text: "Read 9:41 AM" once the agent has started a reply that
 * covers it (a reply_started event through that message), otherwise "Delivered".
 */
export function receiptLabel(events: WireEvent[]): string {
  const lastSent = events.findLast((e) => e.payload.kind === "user_message");
  if (!lastSent) return "Delivered";
  const read = events.find(
    (e) => e.seq > lastSent.seq && e.payload.kind === "reply_started" && e.payload.through_seq >= lastSent.seq,
  );
  if (!read) return "Delivered";
  const time = new Date(read.ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return `Read ${time}`;
}

export function voiceNoteUrl(audioId: string): string {
  return `/api/voice-note/${encodeURIComponent(audioId)}`;
}

/** The name on the agent's latest contact card, if the user has not saved that name yet. */
export function contactOffer(events: WireEvent[], savedName: string | null): string | null {
  const card = events.findLast((e) => e.payload.kind === "contact_card")?.payload;
  const name = card?.kind === "contact_card" ? card.name : null;
  return name && name !== savedName ? name : null;
}
