import type { CallState, Event, PartialTranscript, PayloadOf } from "../types";

export type ThreadItem =
  | { kind: "message"; seq: number; ts: string; side: "sent" | "received"; text: string; via: "text" | "voice" }
  | {
      kind: "call";
      seq: number;
      ts: string;
      side: "sent" | "received";
      phase: "ended" | "declined" | "failed";
      reason: string | null;
      minutes: number | null;
    };

const GMAIL_PATH = "/api/auth/google/start";

export function gmailLinkIn(text: string): string | null {
  const m = text.match(/https?:\/\/\S*\/api\/auth\/google\/start\S*|\/api\/auth\/google\/start\S*/);
  if (m) return m[0].replace(/[.,)]+$/, "");
  return text.includes(GMAIL_PATH) ? GMAIL_PATH : null;
}

/** What a phone would show: texts in both directions and the call log, with durations. */
export function threadItems(events: Event[]): ThreadItem[] {
  const out: ThreadItem[] = [];
  let connectedAt: number | null = null;
  let initiatedBy: "agent" | "user" | null = null;
  for (const ev of events) {
    const p = ev.payload;
    if (p.kind === "user_message") {
      out.push({ kind: "message", seq: ev.seq, ts: ev.ts, side: "sent", text: p.text, via: "text" });
    } else if (p.kind === "agent_message") {
      out.push({
        kind: "message",
        seq: ev.seq,
        ts: ev.ts,
        side: "received",
        text: p.text,
        via: p.from_call ? "voice" : "text",
      });
    } else if (p.kind === "call") {
      const t = p.transition;
      if (t === "ringing" || t === "connecting") {
        initiatedBy = p.initiated_by ?? initiatedBy;
      } else if (t === "connected") {
        connectedAt = Date.parse(ev.ts);
        initiatedBy = p.initiated_by ?? initiatedBy;
      } else if (t === "ended" || t === "declined" || t === "failed") {
        const minutes =
          t === "ended" && connectedAt ? Math.max(0, Math.round((Date.parse(ev.ts) - connectedAt) / 60000)) : null;
        const by = p.initiated_by ?? initiatedBy;
        out.push({
          kind: "call",
          seq: ev.seq,
          ts: ev.ts,
          side: by === "user" ? "sent" : "received",
          phase: t,
          reason: p.reason ?? null,
          minutes,
        });
        connectedAt = null;
        initiatedBy = null;
      }
    }
  }
  return out;
}

export interface TranscriptLine {
  id: string;
  speaker: "user" | "agent";
  text: string;
  partial: boolean;
}

/** Utterances from the current or most recent call, with live partials appended. */
export function transcript(
  events: Event[],
  partials: Record<string, PartialTranscript>,
  call: CallState,
): TranscriptLine[] {
  // Find the start of the most recent call: the last connecting/connected call event.
  let start = 0;
  for (let i = events.length - 1; i >= 0; i--) {
    const p = events[i].payload;
    if (p.kind === "call" && (p.transition === "connecting" || p.transition === "ringing")) {
      start = i;
      break;
    }
  }
  const lines: TranscriptLine[] = [];
  for (let i = start; i < events.length; i++) {
    const p = events[i].payload;
    if (p.kind === "voice_utterance" && p.text)
      lines.push({ id: p.turn_id, speaker: p.speaker, text: p.text, partial: false });
  }
  if (call.phase === "connected" || call.phase === "connecting") {
    for (const p of Object.values(partials))
      lines.push({ id: p.turn_id, speaker: p.speaker, text: p.text, partial: !p.final });
  }
  return lines;
}

export function decisions(events: Event[]): Array<{ seq: number; ts: string } & PayloadOf<"decision">> {
  const out: Array<{ seq: number; ts: string } & PayloadOf<"decision">> = [];
  for (const ev of events) if (ev.payload.kind === "decision") out.push({ seq: ev.seq, ts: ev.ts, ...ev.payload });
  return out;
}

export function formatDuration(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function formatTime(ts: string): string {
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export function formatPhone(raw: string): string {
  const d = raw.replace(/\D/g, "");
  if (d.length === 11 && d.startsWith("1")) return `+1 (${d.slice(1, 4)}) ${d.slice(4, 7)}-${d.slice(7)}`;
  if (d.length === 10) return `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`;
  return raw;
}
