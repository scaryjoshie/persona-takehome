import type { ThreadMessage } from "../components/phone/MessagesScreen";
import type { TranscriptLine } from "../components/orb/Transcript";
import type { PartialMessage, WireEvent } from "../types";

/** The texts in both directions, as the phone shows them. */
export function threadMessages(events: WireEvent[]): ThreadMessage[] {
  return events.flatMap((e): ThreadMessage[] => {
    const p = e.payload;
    if (p.kind === "user_message") return [{ id: String(e.seq), side: "sent", text: p.text }];
    if (p.kind === "agent_message") return [{ id: String(e.seq), side: "received", text: p.text }];
    return [];
  });
}

/** Utterances since the current call started, then any turn still being spoken. */
export function transcriptLines(events: WireEvent[], partials: Record<string, PartialMessage>): TranscriptLine[] {
  const start = events.findLastIndex((e) => e.payload.kind === "call" && e.payload.transition === "connected");
  if (start < 0) return [];
  const lines = events.slice(start).flatMap((e): TranscriptLine[] => {
    const p = e.payload;
    return p.kind === "voice_utterance" && p.text ? [{ id: p.turn_id, speaker: p.speaker, text: p.text }] : [];
  });
  for (const p of Object.values(partials))
    lines.push({ id: p.turn_id, speaker: p.speaker, text: p.text, partial: !p.final });
  return lines;
}

/** Whether this number has never texted: it starts with an empty thread and a suggested first message. */
export function isNewUser(events: WireEvent[]): boolean {
  return !events.some((e) => e.payload.kind === "user_message");
}
