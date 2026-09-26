import { Phone, PhoneOff, Volume2 } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { DynamicIsland, DynamicIslandView } from "@/components/ui/dynamic-island";

export type CallPhase = "incoming" | "calling" | "active";

export interface CallIslandProps {
  agentName: string;
  /** `null` is the idle pill. */
  phase: CallPhase | null;
  seconds?: number;
  /** Active call, long-pressed (here: tapped): controls unfold. */
  expanded?: boolean;
  onAccept?: () => void;
  onDecline?: () => void;
  onEnd?: () => void;
  onToggleExpanded?: () => void;
}

const GREEN = "#30d158";
const RED = "#ff453a";

function clock(s: number) {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function Avatar({ name, size = 28 }: { name: string; size?: number }) {
  return (
    <span
      className="inline-flex items-center justify-center rounded-full bg-gradient-to-b from-[#a5abb8] to-[#858994] font-medium text-white"
      style={{ width: size, height: size, fontSize: size * 0.5 }}
    >
      {name.slice(0, 1)}
    </span>
  );
}

const BAR_DELAYS = [0, 0.18, 0.09, 0.27, 0.14];

/** The caller's voice: four green bars, as in iOS. */
function Waveform() {
  const reduce = useReducedMotion();
  return (
    <span className="flex h-3.5 items-center gap-[3px]" aria-hidden>
      {BAR_DELAYS.map((delay) => (
        <motion.span
          key={delay}
          animate={reduce ? undefined : { scaleY: [0.3, 1, 0.5, 0.85, 0.3] }}
          transition={{ duration: 1.1, repeat: Infinity, ease: "easeInOut", delay }}
          className="h-full w-[3px] rounded-full"
          style={{ background: GREEN, scaleY: 0.5 }}
        />
      ))}
    </span>
  );
}

function RoundButton({ color, label, onClick, children }: { color: string; label: string; onClick?: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={(e) => {
        e.stopPropagation();
        onClick?.();
      }}
      className="flex h-9 w-9 items-center justify-center rounded-full text-white"
      style={{ background: color }}
    >
      {children}
    </button>
  );
}

/**
 * The Dynamic Island as iOS uses it for calls: incoming call expands it with accept and decline,
 * an active call collapses to handset, timer and waveform, and a tap unfolds speaker and end.
 */
export function CallIsland({ agentName, phase, seconds = 0, expanded = false, onAccept, onDecline, onEnd, onToggleExpanded }: CallIslandProps) {
  const view = phase === "incoming" ? "incoming" : phase === "active" && expanded ? "controls" : null;

  const compact =
    phase === "calling" ? (
      <>
        <Avatar name={agentName} size={22} />
        <span className="flex-1" />
        <Phone className="h-3.5 w-3.5" style={{ color: GREEN }} fill={GREEN} />
      </>
    ) : phase === "active" ? (
      <>
        <Phone className="h-3.5 w-3.5" style={{ color: GREEN }} fill={GREEN} />
        <span className="text-[13px] font-medium tabular-nums" style={{ color: GREEN }}>
          {clock(seconds)}
        </span>
        <span className="flex-1" />
        <Waveform />
      </>
    ) : null;

  return (
    <DynamicIsland view={view} compact={compact} onClick={phase === "active" ? onToggleExpanded : undefined} className={phase === "active" ? "cursor-pointer" : ""}>
      <DynamicIslandView id="incoming" className="w-[300px] gap-3 py-2.5">
        <Avatar name={agentName} size={40} />
        <div className="flex flex-1 flex-col leading-tight">
          <span className="text-[11px] text-white/60">Persona Audio</span>
          <span className="text-[15px] font-semibold">{agentName}</span>
        </div>
        <RoundButton color={RED} label="Decline" onClick={onDecline}>
          <PhoneOff className="h-4 w-4" fill="currentColor" />
        </RoundButton>
        <RoundButton color={GREEN} label="Accept" onClick={onAccept}>
          <Phone className="h-4 w-4" fill="currentColor" />
        </RoundButton>
      </DynamicIslandView>

      <DynamicIslandView id="controls" className="w-[300px] gap-3 py-2.5">
        <Avatar name={agentName} size={40} />
        <div className="flex flex-1 flex-col leading-tight">
          <span className="text-[11px] tabular-nums text-white/60">{clock(seconds)}</span>
          <span className="text-[15px] font-semibold">{agentName}</span>
        </div>
        <RoundButton color="rgba(255,255,255,0.22)" label="Speaker">
          <Volume2 className="h-4 w-4" fill="currentColor" />
        </RoundButton>
        <RoundButton color={RED} label="End call" onClick={onEnd}>
          <PhoneOff className="h-4 w-4" fill="currentColor" />
        </RoundButton>
      </DynamicIslandView>
    </DynamicIsland>
  );
}
