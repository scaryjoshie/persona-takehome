import { useEffect, useRef, useState } from "react";
import type { ThreadMessage } from "../components/phone/MessagesScreen";
import type { CallPhase } from "../components/phone/CallIsland";
import type { TranscriptLine } from "../components/orb/Transcript";
import {
  AGENT_NAME,
  INTRO,
  RANDOM_AGENT_LINES,
  RANDOM_USER_LINES,
  CALL_SCRIPT,
  nextId,
  pick,
  speechLevel,
} from "./data";
import type { Conversation } from "../conversation/types";

interface MockCall {
  phase: CallPhase;
  startedAt: number | null;
}

/**
 * An offline stand-in for the backend (`?mock`), for working on the UI without API keys. Every
 * number is new: the thread starts empty and stays quiet until the first message. Autoplay: every few seconds the agent types for a second and a random
 * line lands; every fifth tick the agent calls. During an active call a scripted exchange plays
 * as transcript lines, with `speaking` true while the agent has the floor.
 */
export function useMockConversation(): Conversation {
  const [messages, setMessages] = useState<ThreadMessage[]>([]);
  const autoplay = messages.length > 0;
  const [typing, setTyping] = useState(false);
  const [call, setCall] = useState<MockCall | null>(null);
  const [transcript, setTranscript] = useState<TranscriptLine[]>([]);
  const [speaking, setSpeaking] = useState(false);
  const [muted, setMuted] = useState(false);
  const speakingRef = useRef(speaking);
  speakingRef.current = speaking;
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
      if (tick.current % 5 === 0) return setCall({ phase: "incoming", startedAt: null });
      if (Math.random() < 0.7) agentSays();
      else userSays();
    }, 4000);
    return () => clearInterval(t);
  }, [autoplay]);

  // Outgoing calls connect after a moment; active calls keep a clock and play the scripted exchange.
  useEffect(() => {
    if (call?.phase === "calling") {
      const t = setTimeout(() => setCall({ phase: "active", startedAt: Date.now() }), 2500);
      return () => clearTimeout(t);
    }
    if (call?.phase !== "active") return;
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
        timers.push(
          setTimeout(
            () => {
              if (cancelled) return;
              const partial = n < words.length - 1;
              const text = words.slice(0, n + 1).join(" ");
              setTranscript((t) =>
                t.at(-1)?.id === id
                  ? [...t.slice(0, -1), { id, speaker: line.speaker, text, partial }]
                  : [...t, { id, speaker: line.speaker, text, partial }],
              );
              if (!partial) {
                setSpeaking(false);
                timers.push(setTimeout(next, 1400));
              }
            },
            95 * (n + 1),
          ),
        );
      });
    };
    timers.push(setTimeout(next, 800));
    return () => {
      cancelled = true;
      timers.forEach(clearTimeout);
      setSpeaking(false);
    };
  }, [call?.phase]);

  const startCall = () => {
    setTranscript([]);
    setCall({ phase: "calling", startedAt: null });
  };
  const accept = () => {
    setTranscript([]);
    setCall({ phase: "active", startedAt: Date.now() });
  };
  const decline = () => setCall(null);
  const end = () => {
    setCall(null);
    setMuted(false);
    setTimeout(() => agentSays("ok, that was fun."), 800);
  };
  const toggleMute = () => setMuted((m) => !m);
  const send = (text: string) => {
    const first = messages.length === 0;
    userSays(text);
    if (!first) return void setTimeout(() => agentSays(), 600);
    INTRO.forEach((line, i) => setTimeout(() => agentSays(line), 600 + i * 1600));
  };

  return {
    agentName: AGENT_NAME,
    messages,
    receipt: "Delivered",
    agentTyping: typing,
    callPhase: call?.phase ?? null,
    callStartedAt: call?.startedAt ?? null,
    transcript,
    muted,
    outputLevel: () => (speakingRef.current ? speechLevel(performance.now() / 1000) : 0),
    send,
    setTyping: () => {},
    startCall,
    accept,
    decline,
    hangUp: end,
    toggleMute,
  };
}
