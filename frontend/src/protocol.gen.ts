// Generated from src/schema.json by scripts/gen-types.mjs. Do not edit.

export type ClientMessage = SendMessage | SetTyping | CallCommand | Reset;
export type CallAction = "start" | "accept" | "decline" | "hangup" | "failed";
export type ServerMessage = Snapshot | EventMessage | SlotsMessage | CallMessage | TypingMessage | TranscriptPartial;
export type Origin = "user" | "text_agent" | "voice_agent" | "call" | "google" | "system";
export type Channel = "text" | "voice" | "system";
export type Speaker = "user" | "agent";
export type CallTransition = "ringing" | "connecting" | "connected" | "declined" | "failed" | "ended";
export type Initiator = "agent" | "user";
export type GmailPhase = "link_sent" | "connected" | "failed" | "skipped";
export type CallPhase = "none" | "ringing" | "connecting" | "connected" | "ended";
/**
 * Which medium has the floor: voice while a call is connected, text otherwise.
 */
export type Medium = "text" | "voice";
export type Payload =
  | UserMessage
  | AgentMessage
  | Typing
  | ReplyDue
  | ReplyStarted
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
    | ReplyDue
    | ReplyStarted
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
 * Time to check whether to reply. Submitted by the text medium after a delay; never stored.
 */
export interface ReplyDue {
  kind: "reply_due";
}
/**
 * The agent started replying to everything up to and including event `through_seq`.
 * Stored, so "is anything waiting for a reply?" is a question about the log.
 */
export interface ReplyStarted {
  kind: "reply_started";
  through_seq: number;
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
export interface Decision {
  kind: "decision";
  trigger_kind: string;
  verb: string;
  by: string;
  confidence: number | null;
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
export interface TranscriptPartial {
  type: "partial";
  speaker: Speaker;
  turn_id: string;
  text: string;
  final: boolean;
}
export interface Preview {
  url: string;
  title: string | null;
  description: string | null;
  image: string | null;
  site_name: string | null;
  icon: string | null;
}
