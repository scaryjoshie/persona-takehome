// Generated from src/schema.json by scripts/gen-types.mjs. Do not edit.

export type ClientMessage = SendMessage | SetTyping | CallCommand | ReactCommand | SaveContact | Reset;
export type CallAction = "start" | "accept" | "decline" | "hangup" | "failed";
export type ServerMessage = Snapshot | EventMessage | SlotsMessage | CallMessage | TypingMessage | TranscriptPartial;
export type Origin = "user" | "text_agent" | "voice_agent" | "call" | "google" | "job" | "system";
export type Channel = "text" | "voice" | "system";
/**
 * How we know their timezone. Only what a texting assistant could really know: their
 * Google Calendar's setting, or what they told us (which wins; they may be travelling).
 */
export type TzSource = "calendar" | "said";
export type Speaker = "user" | "agent";
export type CallTransition = "ringing" | "connecting" | "connected" | "declined" | "failed" | "ended";
export type Initiator = "agent" | "user";
export type GmailPhase = "link_sent" | "connected" | "failed" | "skipped" | "disconnected";
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
  | VoiceNote
  | Reaction
  | ContactCard
  | ContactSaved
  | DeviceTimezone
  | TimezoneLearned
  | CallOptOut
  | VoiceUtterance
  | ToolCall
  | SlotChanged
  | StepSetAside
  | Graduated
  | CallEvent
  | GmailEvent
  | EmailDraft
  | Remembered
  | Forgot
  | JobStarted
  | JobAsked
  | JobTold
  | JobEnded
  | Decision;

export interface SendMessage {
  type: "message";
  text: string;
  reply_to: number | null;
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
/**
 * A tapback on a bubble; `remove` takes it back.
 */
export interface ReactCommand {
  type: "react";
  target_seq: number;
  emoji: string;
  remove: boolean;
}
/**
 * The user tapped the agent's contact card to save it.
 */
export interface SaveContact {
  type: "contact";
  action: "save";
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
    | VoiceNote
    | Reaction
    | ContactCard
    | ContactSaved
    | DeviceTimezone
    | TimezoneLearned
    | CallOptOut
    | VoiceUtterance
    | ToolCall
    | SlotChanged
    | StepSetAside
    | Graduated
    | CallEvent
    | GmailEvent
    | EmailDraft
    | Remembered
    | Forgot
    | JobStarted
    | JobAsked
    | JobTold
    | JobEnded
    | Decision;
}
export interface UserMessage {
  kind: "user_message";
  text: string;
  reply_to: number | null;
  reply_to_text: string | null;
}
/**
 * A bubble that was sent. Recorded, never routed.
 */
export interface AgentMessage {
  kind: "agent_message";
  text: string;
  from_call: boolean;
  reply_to: number | null;
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
 * A voice message the user sent. Handled like a text: `transcript` is what they said.
 */
export interface VoiceNote {
  kind: "voice_note";
  audio_id: string;
  duration_ms: number | null;
  transcript: string | null;
}
/**
 * A tapback (❤️ 👍 😂 …) on a bubble, by either side. The user's route: a 👍 on
 * "want me to call?" is an answer. The agent's are recorded only.
 */
export interface Reaction {
  kind: "reaction";
  target_seq: number;
  target_text: string | null;
  emoji: string;
  by: "user" | "agent";
  removed: boolean;
}
/**
 * The agent's contact card (a .vcf), sent whenever its name is set or changed. The
 * user's phone only shows the new name once they tap to save it (ContactSaved).
 */
export interface ContactCard {
  kind: "contact_card";
  name: string;
}
/**
 * The user saved the agent's contact card. Sets what their phone calls the agent.
 *
 * It happens on their phone, so the agent never learns of it: not routed, not in any
 * prompt. Only the phone's own display uses it.
 */
export interface ContactSaved {
  kind: "contact_saved";
  name: string;
}
/**
 * Their browser's timezone. No longer sent or used: a texting assistant couldn't know it.
 * Kept so older rows still load.
 */
export interface DeviceTimezone {
  kind: "device_timezone";
  tz: string;
}
/**
 * Where they are, in time: from their Google Calendar's setting, or because they said. What
 * they said wins over the calendar; the pipeline drops a calendar zone after one they said.
 */
export interface TimezoneLearned {
  kind: "timezone_learned";
  tz: string;
  source: TzSource;
}
/**
 * They'd rather not talk on the phone. The agent stops offering a call.
 */
export interface CallOptOut {
  kind: "call_opt_out";
}
/**
 * One turn of speech. Recorded, never routed. On GPT-Live the boundary is inferred.
 */
export interface VoiceUtterance {
  kind: "voice_utterance";
  speaker: Speaker;
  text: string | null;
  turn_id: string;
}
/**
 * A tool the agent (or a background job) used, kept compact: no raw payloads, no secrets.
 */
export interface ToolCall {
  kind: "tool_call";
  name: string;
  args: {
    [k: string]: unknown;
  };
  result: {
    [k: string]: unknown;
  } | null;
  shown: string | null;
  app: string | null;
  ok: boolean;
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
/**
 * They'd rather not do a setup step (a name for you, theirs, what they need, Google). It
 * stops being asked for; it comes back only if they bring it up.
 */
export interface StepSetAside {
  kind: "step_set_aside";
  step: "agent_name" | "user_name" | "help_need";
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
  inbox: InboxItem[];
}
/**
 * One inbox message as seen at connect time: headers and Gmail's snippet, no body.
 */
export interface InboxItem {
  id: string;
  sender: string;
  subject: string;
  snippet: string;
}
export interface EmailDraft {
  kind: "email_draft";
  ref: string;
  to: string;
  subject: string;
  body: string;
  gmail_id: string;
  status: "draft" | "sent";
}
/**
 * The agent remembered a fact. The pipeline fills in `fact_id` and drops a repeat.
 */
export interface Remembered {
  kind: "remembered";
  fact: string;
  app: string | null;
  fact_id: number | null;
}
/**
 * The agent forgot a fact, by its number. The pipeline fills in `fact` and drops the
 * event if there is no such fact.
 */
export interface Forgot {
  kind: "forgot";
  fact_id: number;
  fact: string | null;
}
export interface JobStarted {
  kind: "job_started";
  job: string;
  goal: string;
}
/**
 * The job needs something from them. It waits until it's told or cancelled.
 */
export interface JobAsked {
  kind: "job_asked";
  job: string;
  question: string;
}
/**
 * Their answer, or anything else they said the job should know, passed on to it.
 */
export interface JobTold {
  kind: "job_told";
  job: string;
  text: string;
}
export interface JobEnded {
  kind: "job_ended";
  job: string;
  outcome: "done" | "failed" | "cancelled";
  text: string;
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
  no_calls: boolean;
  set_aside: string[];
  contact_name: string | null;
  timezone: string | null;
  timezone_source: TzSource | null;
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
