import type { ThreadMessage } from "../components/phone/MessagesScreen";
import type { CallPhase } from "../components/phone/CallIsland";
import type { TranscriptLine } from "../components/orb/Transcript";
import type { Recording } from "../audio/useVoiceRecorder";

/** What the stage renders, whichever source drives it: the live backend or the offline mock. */
export interface Conversation {
  /** What the user named the agent, once they have. */
  agentName: string | null;
  messages: ThreadMessage[];
  /** Under the user's latest text while it is the last in the thread: "Delivered" or "Read 9:41 AM". */
  receipt: string;
  agentTyping: boolean;
  /** `null` when there is no call. */
  callPhase: CallPhase | null;
  /** Epoch ms when the call connected. */
  callStartedAt: number | null;
  transcript: TranscriptLine[];
  muted: boolean;
  /** The agent's voice level, 0..1, read every frame for the orb. */
  outputLevel: () => number;
  /** Sends a text; `replyTo` is the id of the message it answers. */
  send: (text: string, replyTo?: string) => void;
  sendVoiceNote: (recording: Recording) => void;
  /** Sets, or with `null` removes, the user's tapback on a message. */
  react: (messageId: string, emoji: string | null) => void;
  setTyping: (active: boolean) => void;
  startCall: () => void;
  accept: () => void;
  decline: () => void;
  hangUp: () => void;
  toggleMute: () => void;
}

const noop = () => {};

/** The stage behind the phone-number screen: an empty phone, nothing happening. */
export const IDLE_CONVERSATION: Conversation = {
  agentName: null,
  messages: [],
  receipt: "Delivered",
  agentTyping: false,
  callPhase: null,
  callStartedAt: null,
  transcript: [],
  muted: false,
  outputLevel: () => 0,
  send: noop,
  sendVoiceNote: noop,
  react: noop,
  setTyping: noop,
  startCall: noop,
  accept: noop,
  decline: noop,
  hangUp: noop,
  toggleMute: noop,
};
