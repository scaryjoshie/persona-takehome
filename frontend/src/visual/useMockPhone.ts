import { useEffect, useRef, useState } from "react";
import type { ThreadMessage } from "../components/phone/MessagesScreen";
import type { CallPhase } from "../components/phone/CallIsland";
import type { TranscriptLine } from "../components/orb/Transcript";
import { CONVERSATION, RANDOM_AGENT_LINES, RANDOM_USER_LINES, CALL_SCRIPT, nextId, pick } from "./mock";

export interface MockCall {
  phase: CallPhase;
  seconds: number;
  expanded: boolean;
}

/**
 * Mock conversation state. Autoplay: every few seconds the agent types for a second and a random
 * line lands; every fifth tick the agent calls. During an active call a scripted exchange plays
 * as transcript lines, with `speaking` true while the agent has the floor.
 */
export function useMockPhone(autoplay = true) {
  const [messages, setMessages] = useState<ThreadMessage[]>(CONVERSATION);
  const [typing, setTyping] = useState(false);
  const [call, setCall] = useState<MockCall | null>(null);
  const [transcript, setTranscript] = useState<TranscriptLine[]>([]);
  const [speaking, setSpeaking] = useState(false);
  const [muted, setMuted] = useState(false);
  const callRef = useRef(call);
  callRef.current = call;
  const tick = useRef(0);

  const agentSays = (text = pick(RANDOM_AGENT_LINES)) => {
    setTyping(true);
    setTimeout(() => {
      setTyping(false);
      setMessages((m) => [...m, { id: nextId(), side: "received", text }]);
    }, 1000);
  };
  const userSays = (text = pick(RANDOM_USER_LINES)) => setMessages((m) => [...m, { id: nextId(), side: "sent", text }]);

  useEffect(() => {
    if (!autoplay) return;
    const t = setInterval(() => {
      tick.current += 1;
      if (callRef.current) return;
      if (tick.current % 5 === 0) return setCall({ phase: "incoming", seconds: 0, expanded: false });
      Math.random() < 0.7 ? agentSays() : userSays();
    }, 4000);
    return () => clearInterval(t);
  }, [autoplay]);

  // Outgoing calls connect after a moment; active calls keep a clock and play the scripted exchange.
  useEffect(() => {
    if (call?.phase === "calling") {
      const t = setTimeout(() => setCall((c) => (c ? { ...c, phase: "active" } : c)), 2500);
      return () => clearTimeout(t);
    }
    if (call?.phase !== "active") return;
    const clock = setInterval(() => setCall((c) => (c ? { ...c, seconds: c.seconds + 1 } : c)), 1000);

    let i = 0;
    let cancelled = false;
    const timers: ReturnType<typeof setTimeout>[] = [];
    const next = () => {
      if (cancelled) return;
      const line = CALL_SCRIPT[i % CALL_SCRIPT.length];
      i += 1;
      const id = nextId();
      const words = line.text.split(" ");
      setSpeaking(line.speaker === "agent");
      words.forEach((_, n) => {
        timers.push(setTimeout(() => {
          if (cancelled) return;
          const partial = n < words.length - 1;
          const text = words.slice(0, n + 1).join(" ");
          setTranscript((t) => (t.at(-1)?.id === id ? [...t.slice(0, -1), { id, speaker: line.speaker, text, partial }] : [...t, { id, speaker: line.speaker, text, partial }]));
          if (!partial) {
            setSpeaking(false);
            timers.push(setTimeout(next, 1400));
          }
        }, 95 * (n + 1)));
      });
    };
    timers.push(setTimeout(next, 800));
    return () => {
      cancelled = true;
      clearInterval(clock);
      timers.forEach(clearTimeout);
      setSpeaking(false);
    };
  }, [call?.phase]);

  const startCall = () => {
    setTranscript([]);
    setCall({ phase: "calling", seconds: 0, expanded: false });
  };
  const accept = () => {
    setTranscript([]);
    setCall({ phase: "active", seconds: 0, expanded: false });
  };
  const decline = () => setCall(null);
  const end = () => {
    setCall(null);
    setMuted(false);
    setTimeout(() => agentSays("ok, that was fun."), 800);
  };
  const toggleMute = () => setMuted((m) => !m);
  const toggleExpanded = () => setCall((c) => (c ? { ...c, expanded: !c.expanded } : c));
  const send = (text: string) => {
    userSays(text);
    setTimeout(() => agentSays(), 600);
  };

  return { messages, typing, call, transcript, speaking, muted, startCall, accept, decline, end, toggleExpanded, toggleMute, send };
}
