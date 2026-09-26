import type { ThreadMessage } from "../components/phone/MessagesScreen";
import type { TranscriptLine } from "../components/orb/Transcript";
import type { ReactionPayload, TranscriptPartial, VoiceNotePayload, WireEvent } from "../types";
import type { Reaction } from "../components/phone/Tapback";

/** The texts in both directions, as the phone shows them, with their tapbacks. */
export function threadMessages(events: WireEvent[]): ThreadMessage[] {
  const reactions = reactionsBySeq(events);
  const messages = messagesOf(events);
  const byId = new Map(messages.map((m) => [m.id, m]));
  return messages.map(({ replyToId, ...m }) => {
    const original = replyToId ? byId.get(replyToId) : undefined;
    return {
      ...m,
      ...(reactions.has(m.id) && { reactions: reactions.get(m.id) }),
      ...(original && { replyTo: { id: original.id, side: original.side, text: original.text } }),
    };
  });
}

/** Each message's current tapbacks: one per reactor, the latest winning, removals applied. */
function reactionsBySeq(events: WireEvent[]): Map<string, Reaction[]> {
  const out = new Map<string, Reaction[]>();
  for (const e of events) {
    const p = e.payload as WireEvent["payload"] | ReactionPayload;
    if (p.kind !== "reaction") continue;
    const key = String(p.target_seq);
    const others = (out.get(key) ?? []).filter((r) => r.by !== p.by);
    out.set(key, p.removed ? others : [...others, { emoji: p.emoji, by: p.by }]);
  }
  return out;
}

/** Each thread message, with the id of the message it replies to (reply_to is not in the schema yet). */
function messagesOf(events: WireEvent[]): Array<ThreadMessage & { replyToId?: string }> {
  return events.flatMap((e): Array<ThreadMessage & { replyToId?: string }> => {
    const p = e.payload as WireEvent["payload"] | VoiceNotePayload;
    if (p.kind === "voice_note") {
      const voice = { src: voiceNoteUrl(p.audio_id), durationMs: p.duration_ms, transcript: p.transcript };
      return [{ id: String(e.seq), side: "sent", text: "", ts: e.ts, voice, audioId: p.audio_id }];
    }
    const reply = (p as { reply_to?: number | null }).reply_to;
    const replyToId = reply == null ? undefined : String(reply);
    if (p.kind === "user_message") return [{ id: String(e.seq), side: "sent", text: p.text, ts: e.ts, replyToId }];
    if (p.kind === "agent_message") return [{ id: String(e.seq), side: "received", text: p.text, ts: e.ts, replyToId }];
    return [];
  });
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
