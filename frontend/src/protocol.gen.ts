// Generated from src/schema.json by scripts/gen-types.mjs. Do not edit.

export type ClientMessage = SendMessage | SetTyping | CallCommand | Reset;
export type CallAction = "start" | "accept" | "decline" | "hangup" | "failed";
export type ServerMessage = Snapshot | EventMessage | SlotsMessage | CallMessage | TypingMessage | Partial;
export type Origin = "user" | "text_agent" | "voice_agent" | "call" | "google" | "system";
export type Channel = "text" | "voice" | "system";
export type Speaker = "user" | "agent";
export type CallTransition = "ringing" | "connecting" | "connected" | "declined" | "failed" | "ended";
export type Initiator = "agent" | "user";
export type GmailPhase = "link_sent" | "connected" | "failed" | "skipped";
export type Verb = "start" | "interrupt" | "absorb" | "defer";
export type DecidedBy = "fixed" | "default" | "jev" | "model";
export type CallPhase = "none" | "ringing" | "connecting" | "connected" | "ended";
export type Medium = "text" | "voice";
export type Payload =
  | UserMessage
  | AgentMessage
  | Typing
  | VoiceUtterance
  | ToolCall
  | SlotChanged
  | Graduated
  | CallEvent
  | GmailEvent
  | Decision;

export interface SendMessage {
  type: "message";
  text: string;
}
export interface SetTyping {
  type: "typing";
  active: boolean;
}
export interface CallCommand {
  type: "call";
  action: CallAction;
  reason: string | null;
}
export interface Reset {
  type: "reset";
}
export interface Snapshot {
  type: "snapshot";
  events: WireEvent[];
  slots: Slots;
  call: CallState;
  floor: Medium;
}
/**
 * The event envelope as the browser sees it, with the payload typed as the full union.
 */
export interface WireEvent {
  seq: number;
  ts: string;
  origin: Origin;
  channel: Channel;
  payload:
    | UserMessage
    | AgentMessage
    | Typing
    | VoiceUtterance
    | ToolCall
    | SlotChanged
    | Graduated
    | CallEvent
    | GmailEvent
    | Decision;
}
export interface UserMessage {
  kind: "user_message";
  text: string;
}
/**
 * A bubble that was sent. Recorded, never routed.
 */
export interface AgentMessage {
  kind: "agent_message";
  text: string;
  from_call: boolean;
}
/**
 * Coalesced client-side. Routed, never stored.
 */
export interface Typing {
  kind: "typing";
  active: boolean;
  seconds: number;
}
/**
 * One turn of speech. Recorded, never routed. On GPT-Live the boundary is inferred.
 */
export interface VoiceUtterance {
  kind: "voice_utterance";
  speaker: Speaker;
  text: string | null;
  turn_id: string;
  inferred: boolean;
}
export interface ToolCall {
  kind: "tool_call";
  name: string;
  args: {
    [k: string]: unknown;
  };
  result: {
    [k: string]: unknown;
  } | null;
}
/**
 * The agent recorded a name or the help need. The pipeline fills in `old` and drops the
 * event if the value did not change.
 */
export interface SlotChanged {
  kind: "slot_changed";
  slot: "agent_name" | "user_name" | "help_need";
  new: string;
  old: string | null;
}
export interface Graduated {
  kind: "graduated";
}
export interface CallEvent {
  kind: "call";
  transition: CallTransition;
  reason: string | null;
  call_id: string | null;
  initiated_by: Initiator | null;
}
export interface GmailEvent {
  kind: "gmail";
  phase: GmailPhase;
  email: string | null;
}
/**
 * Logged for every routed event. The debug panel and the harness read these.
 */
export interface Decision {
  kind: "decision";
  trigger_kind: string;
  verb: Verb;
  by: DecidedBy;
  confidence: number;
  ms: number;
  note: string | null;
}
export interface Slots {
  agent_name: string | null;
  user_name: string | null;
  help_need: string | null;
  gmail: GmailPhase | null;
  gmail_email: string | null;
  graduated: boolean;
}
export interface CallState {
  phase: CallPhase;
  reason: string | null;
  call_id: string | null;
  initiated_by: Initiator | null;
  started_at: string | null;
  ended_at: string | null;
}
export interface EventMessage {
  type: "event";
  event: WireEvent;
}
export interface SlotsMessage {
  type: "slots";
  slots: Slots;
}
export interface CallMessage {
  type: "call";
  call: CallState;
}
export interface TypingMessage {
  type: "typing";
  active: boolean;
}
/**
 * A live caption: `text` is the turn's full transcript so far (replace, don't append).
 * `final` closes the turn; the VoiceUtterance event with the same turn_id follows.
 */
export interface Partial {
  type: "partial";
  speaker: Speaker;
  turn_id: string;
  text: string;
  final: boolean;
}
