import { Fragment } from "react";
import { AnimatePresence, motion } from "motion/react";

export interface TranscriptLine {
  id: string;
  speaker: "agent" | "user";
  text: string;
  /** Still being spoken. */
  partial?: boolean;
}

interface Props {
  lines: TranscriptLine[];
  /** How many recent lines to show. */
  limit?: number;
  className?: string;
}

/** The last few lines of a call, under the orb. Lines rise in and fade out; the agent reads brighter. */
export function Transcript({ lines, limit = 4, className }: Props) {
  const recent = lines.slice(-limit);
  return (
    <ol className={`flex w-[340px] flex-col gap-2 text-center text-[13px] leading-snug ${className ?? ""}`}>
      <AnimatePresence initial={false}>
        {recent.map((l) => (
          <motion.li
            key={l.id}
            layout
            initial={{ opacity: 0, y: 8, filter: "blur(3px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            exit={{ opacity: 0, y: -6, transition: { duration: 0.25 } }}
            transition={{ type: "spring", duration: 0.6, bounce: 0.1 }}
            className={l.speaker === "agent" ? "text-neutral-300" : "text-neutral-500"}
          >
            {l.text.split(" ").map((word, i) => (
              // Keyed by position, so only newly arrived words animate in. The space sits outside
              // the inline-block word so it survives layout and copy-paste.
              <Fragment key={i}>
                {i > 0 && " "}
                <span className="transcript-word">{word}</span>
              </Fragment>
            ))}
            {l.partial && <span className="animate-pulse text-neutral-400">▍</span>}
          </motion.li>
        ))}
      </AnimatePresence>
    </ol>
  );
}
