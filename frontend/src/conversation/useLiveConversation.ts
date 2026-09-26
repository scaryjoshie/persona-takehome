import { useMemo, useState } from "react";
import type { Snapshot } from "../types";
import type { Transport } from "../transport/types";
import { useSession } from "../state/session";
import { receiptLabel, threadMessages, transcriptLines } from "../state/derive";
import { useCallAudio } from "../audio/useCallAudio";
import type { CallPhase } from "../components/phone/CallIsland";
import type { Conversation } from "./types";
import type { ThreadMessage } from "../components/phone/MessagesScreen";
import type { Recording } from "../audio/useVoiceRecorder";

/** The conversation as the backend has it, over a connected transport. */
export function useLiveConversation(transport: Transport, snapshot: Snapshot): Conversation {
  const { state, actions } = useSession(transport, snapshot);
  const audio = useCallAudio(transport, state.call, actions);
  const { call, events, partials } = state;

  const thread = useMemo(() => threadMessages(events), [events]);
  const voiceNotes = usePendingVoiceNotes(transport);
  // An uploaded note shows at once, then gives way to the server's copy (with its transcript).
  const messages = useMemo(() => {
    const recorded = new Set(thread.map((m) => m.audioId).filter(Boolean));
    return [...thread, ...voiceNotes.pending.filter((m) => !m.audioId || !recorded.has(m.audioId))];
  }, [thread, voiceNotes.pending]);
  const receipt = useMemo(() => receiptLabel(events), [events]);
  const transcript = useMemo(() => transcriptLines(events, partials), [events, partials]);

  // The island's phases: the agent ringing us, us dialing (or audio coming up), and connected.
  let callPhase: CallPhase | null = null;
  if (call.phase === "ringing" && call.initiated_by === "agent") callPhase = "incoming";
  else if (call.phase === "connecting") callPhase = "calling";
  else if (call.phase === "connected") callPhase = "active";

  return {
    agentName: state.slots.agent_name,
    messages,
    receipt,
    agentTyping: state.agentTyping,
    callPhase,
    callStartedAt: call.started_at ? Date.parse(call.started_at) : null,
    transcript,
    muted: audio.muted,
    outputLevel: audio.getOutputLevel,
    send: actions.sendMessage,
    sendVoiceNote: voiceNotes.send,
    react: (messageId, emoji) => {
      const target_seq = Number(messageId);
      if (!Number.isInteger(target_seq)) return; // not yet echoed back by the server
      const current = messages.find((m) => m.id === messageId)?.reactions?.find((r) => r.by === "user");
      if (emoji) transport.send({ type: "react", target_seq, emoji });
      else if (current) transport.send({ type: "react", target_seq, emoji: current.emoji, remove: true });
    },
    setTyping: actions.setTyping,
    startCall: audio.start,
    accept: audio.accept,
    decline: audio.decline,
    hangUp: audio.hangup,
    toggleMute: () => audio.setMuted(!audio.muted),
  };
}

/** Audio messages sent from this tab that the server has not echoed back yet. */
function usePendingVoiceNotes(transport: Transport) {
  const [pending, setPending] = useState<ThreadMessage[]>([]);
  const update = (id: string, patch: Partial<ThreadMessage>) =>
    setPending((all) => all.map((m) => (m.id === id ? { ...m, ...patch } : m)));

  const send = async ({ blob, durationMs }: Recording) => {
    const id = `pending-${crypto.randomUUID()}`;
    const voice = { src: URL.createObjectURL(blob), durationMs, transcript: null };
    setPending((all) => [...all, { id, side: "sent", text: "", ts: new Date().toISOString(), voice }]);
    try {
      update(id, { audioId: await transport.uploadVoiceNote(blob) });
    } catch {
      update(id, { voice: { ...voice, transcript: "Not delivered" } });
    }
  };
  return { pending, send };
}
