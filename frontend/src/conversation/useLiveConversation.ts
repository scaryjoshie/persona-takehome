import { useMemo } from "react";
import type { Snapshot } from "../types";
import type { Transport } from "../transport/types";
import { useSession } from "../state/session";
import { threadMessages, transcriptLines } from "../state/derive";
import { useCallAudio } from "../audio/useCallAudio";
import type { CallPhase } from "../components/phone/CallIsland";
import type { Conversation } from "./types";

/** The conversation as the backend has it, over a connected transport. */
export function useLiveConversation(transport: Transport, snapshot: Snapshot): Conversation {
  const { state, actions } = useSession(transport, snapshot);
  const audio = useCallAudio(transport, state.call, actions);
  const { call, events, partials } = state;

  const messages = useMemo(() => threadMessages(events), [events]);
  const transcript = useMemo(() => transcriptLines(events, partials), [events, partials]);

  // The island's phases: the agent ringing us, us dialing (or audio coming up), and connected.
  let callPhase: CallPhase | null = null;
  if (call.phase === "ringing" && call.initiated_by === "agent") callPhase = "incoming";
  else if (call.phase === "connecting") callPhase = "calling";
  else if (call.phase === "connected") callPhase = "active";

  return {
    agentName: state.slots.agent_name,
    messages,
    agentTyping: state.agentTyping,
    callPhase,
    callStartedAt: call.started_at ? Date.parse(call.started_at) : null,
    transcript,
    muted: audio.muted,
    outputLevel: audio.getOutputLevel,
    send: actions.sendMessage,
    setTyping: actions.setTyping,
    startCall: audio.start,
    accept: audio.accept,
    decline: audio.decline,
    hangUp: audio.hangup,
    toggleMute: () => audio.setMuted(!audio.muted),
  };
}
