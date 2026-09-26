import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { IPhone17Pro } from "./components/phone/IPhone17Pro";
import { MessagesScreen } from "./components/phone/MessagesScreen";
import { CallIsland } from "./components/phone/CallIsland";
import { VoiceOrb } from "./components/orb/VoiceOrb";
import { Transcript } from "./components/orb/Transcript";
import { CallControls } from "./components/call/CallControls";
import { PhoneEntry } from "./components/entry/PhoneEntry";
import { Stage } from "./components/stage/Stage";
import { IDLE_CONVERSATION, type Conversation } from "./conversation/types";
import { useLiveConversation } from "./conversation/useLiveConversation";
import { useMockConversation } from "./mock/useMockConversation";
import { WsTransport } from "./transport/ws";
import type { Transport } from "./transport/types";
import type { Snapshot } from "./types";
import { isNewUser } from "./state/derive";
import { lastNumber, rememberNumber } from "./lib/lastNumber";
import { play } from "./lib/sounds";

/** `?mock` runs the UI against an offline stand-in instead of the backend. */
const MOCK = new URLSearchParams(location.search).has("mock");

/** What the header shows before the user saves the agent's contact. The agent has no real number. */
const AGENT_NUMBER = "(415) 555-0100";

/** What Persona suggests a new user send first; it waits in the composer. */
const FIRST_MESSAGE = "Hey, what's a Persona?";

type Session = { kind: "mock" } | { kind: "live"; transport: Transport; snapshot: Snapshot; isNew: boolean };

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const enter = async (digits: string) => {
    rememberNumber(digits);
    if (MOCK) return setSession({ kind: "mock" });
    setBusy(true);
    setError(null);
    const transport = new WsTransport();
    try {
      const snapshot = await transport.connect(digits);
      setSession({ kind: "live", transport, snapshot, isNew: isNewUser(snapshot.events) });
    } catch {
      transport.close();
      setError("Can't reach Persona right now. Is the backend running?");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {session?.kind === "live" && <LiveStage {...session} />}
      {session?.kind === "mock" && <MockStage />}
      {!session && <StageView conversation={IDLE_CONVERSATION} />}
      <AnimatePresence>
        {!session && <PhoneEntry initial={lastNumber()} busy={busy} error={error} onSubmit={enter} />}
      </AnimatePresence>
    </>
  );
}

function LiveStage({ transport, snapshot, isNew }: { transport: Transport; snapshot: Snapshot; isNew: boolean }) {
  return <StageView conversation={useLiveConversation(transport, snapshot)} isNew={isNew} entered />;
}

function MockStage() {
  return <StageView conversation={useMockConversation()} isNew entered />;
}

// The island sits 14pt below the top of the screen, centered; measured from Apple's bezel PNG.
const ISLAND_TOP = 14;

/** Scale that fits the 450x920 phone frame in the viewport, leaving room beside it for the orb. */
function fitScale(): number {
  const byHeight = (window.innerHeight - 40) / 920;
  const byWidth = (window.innerWidth * 0.42) / 450;
  return Math.max(0.4, Math.min(1, Math.round(Math.min(byHeight, byWidth) * 100) / 100));
}

interface StageViewProps {
  conversation: Conversation;
  /** Put the suggested first message in the composer. */
  isNew?: boolean;
  /** Settle in from the frosted entry screen. */
  entered?: boolean;
}

/** The phone and the orb, rendering one conversation. */
function StageView({ conversation: c, isNew = false, entered = false }: StageViewProps) {
  // Like a real phone, the thread shows the agent's number until the user saves its contact.
  const contact = c.contactName ?? AGENT_NUMBER;
  const [scale, setScale] = useState(fitScale);
  const [expanded, setExpanded] = useState(false);
  const active = c.callPhase === "active";
  const level = useLevel(c.outputLevel, active);
  const seconds = useElapsed(c.callStartedAt);

  useEffect(() => {
    const onResize = () => setScale(fitScale());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Collapse the island's controls whenever a call ends.
  useEffect(() => {
    if (!active) setExpanded(false);
  }, [active]);

  // A pluck up when the call connects, a pluck down when it ends.
  const wasActive = useRef(false);
  useEffect(() => {
    if (active !== wasActive.current) play(active ? "callJoin" : "callLeave");
    wasActive.current = active;
  }, [active]);

  const orbMode = !active ? "off" : level > 0.08 ? "speaking" : "on";

  return (
    // The stage settles in as the frost lifts: a slight rise to full size and brightness.
    <motion.div
      className="h-full"
      initial={entered ? { opacity: 0, scale: 0.96, filter: "blur(8px)" } : false}
      // Drop the filter once settled: a leftover blur(0px) still costs a compositing layer over the orb.
      animate={{ opacity: 1, scale: 1, filter: "blur(0px)", transitionEnd: { filter: "none" } }}
      transition={{ duration: 1.3, ease: [0.16, 1, 0.3, 1], delay: 0.1 }}
    >
      <Stage
        phone={
          <IPhone17Pro
            scale={scale}
            overlay={
              <div style={{ display: "flex", justifyContent: "center", paddingTop: ISLAND_TOP, pointerEvents: "auto" }}>
                <CallIsland
                  agentName={contact}
                  phase={c.callPhase}
                  seconds={seconds}
                  expanded={expanded}
                  onAccept={c.accept}
                  onDecline={c.decline}
                  onEnd={c.hangUp}
                  onToggleExpanded={() => setExpanded((e) => !e)}
                />
              </div>
            }
          >
            <MessagesScreen
              contact={contact}
              messages={c.messages}
              receipt={c.receipt}
              typing={c.agentTyping}
              onSend={c.send}
              onSendVoiceNote={c.sendVoiceNote}
              onReact={c.react}
              contactOffer={c.contactOffer}
              onSaveContact={c.saveContact}
              onTyping={c.setTyping}
              onCall={c.startCall}
              initialDraft={isNew ? FIRST_MESSAGE : ""}
            />
          </IPhone17Pro>
        }
        orb={<VoiceOrb mode={orbMode} level={level} />}
        caption={active && <Transcript lines={c.transcript} />}
        controls={<CallControls visible={active} muted={c.muted} onToggleMute={c.toggleMute} onEnd={c.hangUp} />}
      />
    </motion.div>
  );
}

/** Samples a level getter every frame while `running`. */
function useLevel(read: () => number, running: boolean): number {
  const [level, setLevel] = useState(0);
  useEffect(() => {
    if (!running) return setLevel(0);
    let raf = 0;
    const tick = () => {
      setLevel(read());
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [read, running]);
  return level;
}

/** Whole seconds since `since`, ticking once a second. */
function useElapsed(since: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (since === null) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [since]);
  return since === null ? 0 : Math.max(0, Math.floor((now - since) / 1000));
}
