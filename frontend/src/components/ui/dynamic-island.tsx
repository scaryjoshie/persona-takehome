// Dynamic Island shell, vendored from beUI (starc007/ui-components, MIT):
// https://beui.dev/components/blocks/dynamic-island
// Local changes: colors are explicit (black pill, white text) instead of theme tokens.

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { EASE_OUT } from "@/lib/ease";
import { cn } from "@/lib/utils";

type IslandContextValue = { view: string | null };
const IslandContext = createContext<IslandContextValue | null>(null);

// Shell physics: one long glide with barely-there bounce, identical in both directions.
// The shell animates real width/height (not transforms), so content is never scale-distorted.
const SHELL_SPRING = { type: "spring", duration: 0.8, bounce: 0.2 } as const;
const CONTENT_SPRING = { type: "spring", duration: 0.8, bounce: 0.35 } as const;

// Constant radius; the browser clamps it to half the shell height, so the pill-to-rounded-rect
// morph falls out of the resize for free.
const RADIUS = 32;

// iPhone pill dimensions, also the pre-measure animate target.
const PILL_WIDTH = 126;
const PILL_HEIGHT = 37;

/** Tracks the natural size of the content so the shell can spring to it. */
function useContentSize() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setSize({ width: el.offsetWidth, height: el.offsetHeight });
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setSize({ width: el.offsetWidth, height: el.offsetHeight }));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return [ref, size] as const;
}

function Slot({ keyId, children, className }: { keyId: string; children: ReactNode; className?: string }) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      key={keyId}
      initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.9, y: -8, filter: "blur(5px)" }}
      animate={reduce ? { opacity: 1 } : { opacity: 1, scale: 1, y: 0, filter: "blur(0px)" }}
      exit={reduce ? { opacity: 0, transition: { duration: 0.1 } } : { opacity: 0, scale: 0.9, y: -6, transition: { duration: 0.08, ease: EASE_OUT } }}
      transition={reduce ? { duration: 0.15 } : CONTENT_SPRING}
      style={{ transformOrigin: "top center" }}
      className={cn("flex items-center justify-center", className)}
    >
      {children}
    </motion.div>
  );
}

export interface DynamicIslandProps {
  /** Active view id. `null` shows the compact pill. */
  view: string | null;
  /** Compact pill content, shown when no view is active. */
  compact?: ReactNode;
  /** DynamicIslandView elements. */
  children?: ReactNode;
  className?: string;
  onClick?: () => void;
}

export function DynamicIsland({ view, compact, children, className, onClick }: DynamicIslandProps) {
  const reduce = useReducedMotion();
  const expanded = view !== null;
  const [sizerRef, size] = useContentSize();
  const contextValue = useMemo(() => ({ view }), [view]);

  return (
    <IslandContext.Provider value={contextValue}>
      <motion.div
        role="status"
        aria-live="polite"
        initial={false}
        animate={size ? { width: size.width, height: size.height } : { width: PILL_WIDTH, height: PILL_HEIGHT }}
        transition={reduce ? { duration: 0 } : SHELL_SPRING}
        style={{ borderRadius: RADIUS }}
        onClick={onClick}
        className={cn("relative inline-flex items-start justify-center overflow-hidden bg-black text-white", className)}
      >
        <div ref={sizerRef} className="w-max">
          <AnimatePresence mode="popLayout" initial={false}>
            {!expanded && compact ? (
              <Slot keyId="compact" className="min-h-[37px] min-w-[126px] gap-2 px-3 py-1.5 text-xs font-medium">
                {compact}
              </Slot>
            ) : null}
          </AnimatePresence>
          {children}
        </div>
      </motion.div>
    </IslandContext.Provider>
  );
}

export interface DynamicIslandViewProps {
  /** Matches the parent `view` prop when active. */
  id: string;
  children: ReactNode;
  className?: string;
}

export function DynamicIslandView({ id, children, className }: DynamicIslandViewProps) {
  const ctx = useContext(IslandContext);
  if (!ctx) throw new Error("DynamicIslandView must be used inside <DynamicIsland>");
  const active = ctx.view === id;
  return (
    <AnimatePresence mode="popLayout" initial={false}>
      {active ? (
        <Slot keyId={id} className={cn("px-5 py-3", className)}>
          {children}
        </Slot>
      ) : null}
    </AnimatePresence>
  );
}
