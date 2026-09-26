import type { ThreadMessage } from "../components/phone/MessagesScreen";
import type { CallPhase } from "../components/phone/CallIsland";
import type { TranscriptLine } from "../components/orb/Transcript";

/** What the stage renders, whichever source drives it: the live backend or the offline mock. */
export interface Conversation {
  /** What the user named the agent, once they have. */
  agentName: string | null;
  messages: ThreadMessage[];
  agentTyping: boolean;
  /** `null` when there is no call. */
  callPhase: CallPhase | null;
  /** Epoch ms when the call connected. */
  callStartedAt: number | null;
  transcript: TranscriptLine[];
  muted: boolean;
  /** The agent's voice level, 0..1, read every frame for the orb. */
  outputLevel: () => number;
  send: (text: string) => void;
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
  agentTyping: false,
  callPhase: null,
  callStartedAt: null,
  transcript: [],
  muted: false,
  outputLevel: () => 0,
  send: noop,
  setTyping: noop,
  startCall: noop,
  accept: noop,
  decline: noop,
  hangUp: noop,
  toggleMute: noop,
};
