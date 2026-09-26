import type { ThreadMessage } from "../components/phone/MessagesScreen";
import type { TranscriptLine } from "../components/orb/Transcript";
import type { TranscriptPartial, WireEvent } from "../types";
import type { Reaction } from "../components/phone/Tapback";
import type { Draft } from "../components/phone/EmailDraftCard";

/** The texts in both directions, as the phone shows them, with their tapbacks and quoted replies. */
export function threadMessages(events: WireEvent[]): ThreadMessage[] {
  const reactions = reactionsBySeq(events);
  const messages = messagesOf(events);
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
function messagesOf(events: WireEvent[]): Unresolved[] {
  // An email draft is re-posted on every edit under the same ref: show one card per ref, where the
  // first version appeared, with the latest version's contents.
  const latestDraft = new Map<string, Draft>();
  for (const e of events) if (e.payload.kind === "email_draft") latestDraft.set(e.payload.ref, e.payload);
  const placed = new Set<string>();
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
      case "email_draft": {
        if (placed.has(p.ref)) return [];
        placed.add(p.ref);
        const draft = latestDraft.get(p.ref)!;
        return [{ ...base, side: "received", text: draft.subject || "Email draft", draft }];
      }
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
 * me Sam"); a turn that starts mid-sentence joins the previous line from the same speaker.
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
  for (const turn of turns) {
    const text = turn.text.trim();
    const prev = lines.at(-1);
    if (prev && prev.speaker === turn.speaker && /^[,.;:!?]|^[a-z]/.test(text)) {
      lines[lines.length - 1] = {
        ...prev,
        text: `${prev.text}${/^[a-z]/.test(text) ? " " : ""}${text}`,
        partial: turn.partial,
      };
    } else if (text) {
      lines.push({ ...turn, text });
    }
  }
  return lines;
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
