import { Mic, MicOff, PhoneOff } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

interface Props {
  visible: boolean;
  muted: boolean;
  onToggleMute: () => void;
  onEnd: () => void;
}

/** Mute and hang up, under the orb while a call is live. */
export function CallControls({ visible, muted, onToggleMute, onEnd }: Props) {
  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 8, transition: { duration: 0.2 } }}
          transition={{ type: "spring", duration: 0.6, bounce: 0.1 }}
          className="flex items-center gap-5"
        >
          <button
            type="button"
            aria-label={muted ? "Unmute" : "Mute"}
            aria-pressed={muted}
            onClick={onToggleMute}
            className={`flex h-14 w-14 items-center justify-center rounded-full transition-colors ${muted ? "bg-white text-black" : "bg-white/12 text-white hover:bg-white/18"}`}
          >
            {muted ? <MicOff className="h-6 w-6" /> : <Mic className="h-6 w-6" />}
          </button>
          <button
            type="button"
            aria-label="End call"
            onClick={onEnd}
            className="flex h-14 w-14 items-center justify-center rounded-full bg-[#ff453a] text-white hover:bg-[#ff5f55]"
          >
            <PhoneOff className="h-6 w-6" fill="currentColor" />
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
