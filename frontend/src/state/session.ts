import { useEffect, useMemo, useReducer } from "react";
import type {
  CallAction,
  CallFailReason,
  CallState,
  ServerMessage,
  Medium,
  TranscriptPartial,
  Slots,
  Snapshot,
  Speaker,
  WireEvent,
} from "../types";
import type { ConnectionStatus, Transport } from "../transport/types";

/** What the browser knows: exactly the server's snapshot plus everything streamed since. */
export interface SessionState {
  status: ConnectionStatus;
  events: WireEvent[];
  slots: Slots;
  call: CallState;
  floor: Medium;
  agentTyping: boolean;
  /** The turn each speaker is saying right now; a speaker says one thing at a time. Dropped once that speaker's utterance lands. */
  partials: Partial<Record<Speaker, TranscriptPartial>>;
}

type Action = { type: "status"; status: ConnectionStatus } | { type: "message"; message: ServerMessage };

function fromSnapshot(s: Snapshot, status: ConnectionStatus): SessionState {
  return { status, events: s.events, slots: s.slots, call: s.call, floor: s.floor, agentTyping: false, partials: {} };
}

function reduce(state: SessionState, action: Action): SessionState {
  if (action.type === "status") return { ...state, status: action.status };
  const m = action.message;
  switch (m.type) {
    case "snapshot":
      return fromSnapshot(m, state.status);
    case "event": {
      const last = state.events.at(-1);
      if (last && m.event.seq <= last.seq) return state; // replayed after a reconnect
      const p = m.event.payload;
      let partials = state.partials;
      if (p.kind === "voice_utterance" && partials[p.speaker]) {
        partials = { ...partials };
        delete partials[p.speaker];
      }
      return { ...state, events: [...state.events, m.event], partials };
    }
    case "slots":
      return { ...state, slots: m.slots };
    case "call":
      return {
        ...state,
        call: m.call,
        floor: m.call.phase === "connected" ? "voice" : "text",
        partials: m.call.phase === "connected" ? state.partials : {},
      };
    case "typing":
      return { ...state, agentTyping: m.active };
    case "partial":
      return { ...state, partials: { ...state.partials, [m.speaker]: m } };
  }
}

export interface SessionActions {
  sendMessage(text: string): void;
  setTyping(active: boolean): void;
  call(action: CallAction, reason?: CallFailReason): void;
}

/** Follows a connected transport: starts from its snapshot and applies every message after. */
export function useSession(transport: Transport, snapshot: Snapshot) {
  const [state, dispatch] = useReducer(reduce, snapshot, (s) => fromSnapshot(s, "open"));

  useEffect(() => {
    const offMessage = transport.onMessage((message) => dispatch({ type: "message", message }));
    const offStatus = transport.onStatus((status) => dispatch({ type: "status", status }));
    return () => {
      offMessage();
      offStatus();
      transport.close();
    };
  }, [transport]);

  const actions = useMemo<SessionActions>(
    () => ({
      sendMessage: (text) => transport.send({ type: "message", text }),
      setTyping: (active) => transport.send({ type: "typing", active }),
      call: (action, reason) => transport.send({ type: "call", action, reason: reason ?? null }),
    }),
    [transport],
  );
  return { state, actions };
}
