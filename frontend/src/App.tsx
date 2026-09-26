import { useEffect, useState } from "react";
import { AnimatePresence } from "motion/react";
import { IPhone17Pro } from "./components/phone/IPhone17Pro";
import { MessagesScreen } from "./components/phone/MessagesScreen";
import { CallIsland } from "./components/phone/CallIsland";
import { VoiceOrb } from "./components/orb/VoiceOrb";
import { Transcript } from "./components/orb/Transcript";
import { CallControls } from "./components/call/CallControls";
import { PhoneEntry } from "./components/entry/PhoneEntry";
import { Stage } from "./components/stage/Stage";
import { useMockPhone } from "./mock/useMockPhone";
import { AGENT_NAME, FIRST_MESSAGE, speechLevel } from "./mock/data";
import { enter, lastNumber } from "./mock/identity";

// The island sits 14pt below the top of the screen, centered; measured from Apple's bezel PNG.
const ISLAND_TOP = 14;

interface Session {
  phone: string;
  isNew: boolean;
}

/** Scale that fits the 450x920 phone frame in the viewport, leaving room beside it for the orb. */
function fitScale(): number {
  const byHeight = (window.innerHeight - 40) / 920;
  const byWidth = (window.innerWidth * 0.42) / 450;
  return Math.max(0.4, Math.min(1, Math.round(Math.min(byHeight, byWidth) * 100) / 100));
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  return (
    <>
      {/* Keyed by number, so entering one starts that user's conversation fresh. */}
      <Demo key={session?.phone ?? "none"} session={session} />
      <AnimatePresence>
        {!session && <PhoneEntry initial={lastNumber()} onSubmit={(phone) => setSession({ phone, ...enter(phone) })} />}
      </AnimatePresence>
    </>
  );
}

function Demo({ session }: { session: Session | null }) {
  const isNew = session?.isNew ?? true;
  const mock = useMockPhone({ newUser: isNew, running: session !== null });
  const [scale, setScale] = useState(fitScale);
  const [level, setLevel] = useState(0);

  useEffect(() => {
    const onResize = () => setScale(fitScale());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // While the agent speaks, feed the orb a speech-shaped level. Real audio replaces this later.
  useEffect(() => {
    if (!mock.speaking) return setLevel(0);
    let raf = 0;
    const start = performance.now();
    const tick = (now: number) => {
      setLevel(speechLevel((now - start) / 1000));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [mock.speaking]);

  const { call } = mock;
  const active = call?.phase === "active";
  const orbMode = !active ? "off" : mock.speaking ? "speaking" : "on";

  return (
    <Stage
      phone={
        <IPhone17Pro
          scale={scale}
          overlay={
            <div style={{ display: "flex", justifyContent: "center", paddingTop: ISLAND_TOP, pointerEvents: "auto" }}>
              <CallIsland
                agentName={AGENT_NAME}
                phase={call?.phase ?? null}
                seconds={call?.seconds}
                expanded={call?.expanded}
                onAccept={mock.accept}
                onDecline={mock.decline}
                onEnd={mock.end}
                onToggleExpanded={mock.toggleExpanded}
              />
            </div>
          }
        >
          <MessagesScreen
            contact={AGENT_NAME}
            messages={mock.messages}
            typing={mock.typing}
            onSend={mock.send}
            onCall={mock.startCall}
            initialDraft={session?.isNew ? FIRST_MESSAGE : ""}
          />
        </IPhone17Pro>
      }
      orb={<VoiceOrb mode={orbMode} level={level} />}
      caption={active && <Transcript lines={mock.transcript} />}
      controls={<CallControls visible={active} muted={mock.muted} onToggleMute={mock.toggleMute} onEnd={mock.end} />}
    />
  );
}
