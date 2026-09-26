import type { ThreadMessage } from "../components/phone/MessagesScreen";
import type { TranscriptLine } from "../components/orb/Transcript";
import type { TranscriptPartial, WireEvent } from "../types";

/** The texts in both directions, as the phone shows them. */
export function threadMessages(events: WireEvent[]): ThreadMessage[] {
  return events.flatMap((e): ThreadMessage[] => {
    const p = e.payload;
    if (p.kind === "user_message") return [{ id: String(e.seq), side: "sent", text: p.text }];
    if (p.kind === "agent_message") return [{ id: String(e.seq), side: "received", text: p.text }];
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
