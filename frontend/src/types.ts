// Protocol types, mirrored from backend/app/core/types.py and docs 09/14.
// To be replaced by generated types once the backend exports its JSON schema.

export type Origin = "user" | "text_agent" | "voice_agent" | "call" | "google" | "system";
export type Channel = "text" | "voice" | "system";
export type Medium = "text" | "voice";

export type CallPhase = "none" | "ringing" | "connecting" | "connected" | "ended";
export type CallEventPhase = "ringing" | "declined" | "connecting" | "connected" | "failed" | "ended";
export type GmailPhase = "link_sent" | "connected" | "failed" | "skipped";
export type GmailStatus = "not_asked" | "link_sent" | "connected" | "skipped";

export type Verb = "interrupt" | "absorb" | "defer";
export type DecidedBy = "fixed" | "default" | "jev" | "model";

export type Payload =
  | { kind: "user_message"; text: string }
  | { kind: "agent_message"; text: string; via: Medium }
  | {
      kind: "voice_utterance";
      speaker: "user" | "agent";
      text: string | null;
      turn_id: string;
      inferred?: boolean;
      item_id?: string | null;
    }
  | { kind: "tool_call"; name: string; args: Record<string, unknown>; result?: Record<string, unknown> | null }
  | { kind: "slot_changed"; slot: string; old: unknown; new: unknown }
  | {
      kind: "call";
      phase: CallEventPhase;
      reason?: string | null;
      call_id?: string | null;
      initiated_by?: "agent" | "user" | null;
    }
  | { kind: "gmail"; phase: GmailPhase; email?: string | null }
  | {
      kind: "decision";
      trigger_kind: string;
      verb: Verb | "start";
      by: DecidedBy;
      confidence: number;
      ms: number;
      note?: string | null;
    }
  | { kind: "graduated" };

export type PayloadKind = Payload["kind"];
export type PayloadOf<K extends PayloadKind> = Extract<Payload, { kind: K }>;

export interface Event {
  seq: number;
  ts: string;
  origin: Origin;
  channel: Channel;
  payload: Payload;
}

export interface Slots {
  agent_name: string | null;
  user_name: string | null;
  help_need: string | null;
  gmail: GmailStatus;
  gmail_email: string | null;
  graduated: boolean;
}

export interface CallState {
  phase: CallPhase;
  reason: string | null;
  call_id: string | null;
  initiated_by: "agent" | "user" | null;
  started_at: string | null;
  ended_at: string | null;
}

export interface Snapshot {
  events: Event[];
  slots: Slots;
  call: CallState;
  floor: Medium;
}

export type CallAction = "start" | "accept" | "decline" | "hangup" | "failed";
export type CallFailReason = "mic_denied" | "audio_socket";

export type ClientMessage =
  | { type: "message"; text: string }
  | { type: "typing"; active: boolean }
  | { type: "call"; action: CallAction; reason?: CallFailReason }
  | { type: "reset" };

export interface PartialTranscript {
  speaker: "user" | "agent";
  turn_id: string;
  text: string;
  final: boolean;
}

export type ServerMessage =
  | ({ type: "snapshot" } & Snapshot)
  | { type: "event"; event: Event }
  | { type: "slots"; slots: Slots }
  | { type: "call"; call: CallState }
  | { type: "typing"; active: boolean }
  | ({ type: "partial" } & PartialTranscript);

export const EMPTY_SLOTS: Slots = {
  agent_name: null,
  user_name: null,
  help_need: null,
  gmail: "not_asked",
  gmail_email: null,
  graduated: false,
};

export const EMPTY_CALL: CallState = {
  phase: "none",
  reason: null,
  call_id: null,
  initiated_by: null,
  started_at: null,
  ended_at: null,
};

export function missingSlots(s: Slots): string[] {
  const out: string[] = [];
  if (s.agent_name === null) out.push("agent_name");
  if (s.user_name === null) out.push("user_name");
  if (s.help_need === null) out.push("help_need");
  if (s.gmail === "not_asked" || s.gmail === "link_sent") out.push("gmail");
  return out;
}

export const SLOT_LABELS: Record<string, string> = {
  agent_name: "agent name",
  user_name: "your name",
  help_need: "help with",
  gmail: "gmail",
};
