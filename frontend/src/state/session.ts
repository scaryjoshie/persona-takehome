import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import type {
  CallAction,
  CallFailReason,
  CallState,
  Event,
  Medium,
  PartialTranscript,
  ServerMessage,
  Slots,
  Snapshot,
} from "../types";
import { EMPTY_CALL, EMPTY_SLOTS } from "../types";
import type { ConnectionStatus, Transport } from "../transport/types";

export interface SessionState {
  status: ConnectionStatus;
  loaded: boolean;
  events: Event[];
  slots: Slots;
  call: CallState;
  floor: Medium;
  agentTyping: boolean;
  partials: Record<string, PartialTranscript>;
}

type Action =
  | { type: "status"; status: ConnectionStatus }
  | { type: "snapshot"; snapshot: Snapshot }
  | { type: "server"; msg: ServerMessage };

const initial: SessionState = {
  status: "connecting",
  loaded: false,
  events: [],
  slots: EMPTY_SLOTS,
  call: EMPTY_CALL,
  floor: "text",
  agentTyping: false,
  partials: {},
};

function applySnapshot(state: SessionState, s: Snapshot): SessionState {
  return {
    ...state,
    loaded: true,
    events: s.events,
    slots: s.slots,
    call: s.call,
    floor: s.floor,
    partials: {},
    agentTyping: false,
  };
}

function reduce(state: SessionState, action: Action): SessionState {
  switch (action.type) {
    case "status":
      return { ...state, status: action.status };
    case "snapshot":
      return applySnapshot(state, action.snapshot);
    case "server": {
      const m = action.msg;
      switch (m.type) {
        case "snapshot":
          return applySnapshot(state, m);
        case "event": {
          const ev = m.event;
          if (state.events.length && ev.seq <= state.events[state.events.length - 1].seq) return state; // duplicate after reconnect
          let partials = state.partials;
          if (ev.payload.kind === "voice_utterance" && partials[ev.payload.turn_id]) {
            partials = { ...partials };
            delete partials[ev.payload.turn_id];
          }
          let floor = state.floor;
          if (ev.payload.kind === "call") {
            const t = ev.payload.transition;
            if (t === "connected") floor = "voice";
            if (t === "ended" || t === "failed" || t === "declined") floor = "text";
          }
          return { ...state, events: [...state.events, ev], partials, floor };
        }
        case "slots":
          return { ...state, slots: m.slots };
        case "call": {
          const partials = m.call.phase === "connected" ? state.partials : {};
          return { ...state, call: m.call, partials, floor: m.call.phase === "connected" ? "voice" : "text" };
        }
        case "typing":
          return { ...state, agentTyping: m.active };
        case "partial": {
          const { type: _t, ...p } = m;
          void _t;
          return { ...state, partials: { ...state.partials, [p.turn_id]: p } };
        }
      }
    }
  }
  return state;
}

export interface SessionActions {
  sendMessage(text: string): void;
  setTyping(active: boolean): void;
  call(action: CallAction, reason?: CallFailReason): void;
  reset(): void;
}

export function useSession(transport: Transport, phone: string, firstMessage: string | null) {
  const [state, dispatch] = useReducer(reduce, initial);
  const sentFirst = useRef(false);

  useEffect(() => {
    const offMsg = transport.onMessage((msg) => dispatch({ type: "server", msg }));
    const offStatus = transport.onStatus((status) => dispatch({ type: "status", status }));
    let cancelled = false;
    transport
      .connect(phone)
      .then((snapshot) => {
        if (cancelled) return;
        dispatch({ type: "snapshot", snapshot });
      })
      .catch((err) => {
        console.error(err);
        dispatch({ type: "status", status: "closed" });
      });
    return () => {
      cancelled = true;
      offMsg();
      offStatus();
      transport.close();
    };
  }, [transport, phone]);

  // The entry screen collected a first message; send it once the socket is open.
  useEffect(() => {
    if (state.status === "open" && state.loaded && firstMessage && !sentFirst.current) {
      sentFirst.current = true;
      transport.send({ type: "message", text: firstMessage });
    }
  }, [state.status, state.loaded, firstMessage, transport]);

  const send = useCallback((text: string) => transport.send({ type: "message", text }), [transport]);
  const setTyping = useCallback((active: boolean) => transport.send({ type: "typing", active }), [transport]);
  const call = useCallback(
    (action: CallAction, reason?: CallFailReason) =>
      transport.send(reason ? { type: "call", action, reason } : { type: "call", action }),
    [transport],
  );
  const reset = useCallback(() => transport.send({ type: "reset" }), [transport]);

  const actions = useMemo<SessionActions>(
    () => ({ sendMessage: send, setTyping, call, reset }),
    [send, setTyping, call, reset],
  );
  return { state, actions };
}
