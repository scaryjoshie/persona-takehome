import { useRef } from "react";

const HOLD_MS = 450;
const SLOP_PX = 8;

/**
 * Press-and-hold for mouse and touch, plus the desktop equivalents Messages accepts (right-click,
 * double-click). Framework7's own `taphold` only fires for touch, so it would miss a desktop reviewer.
 */
export function useLongPress(onPress: (target: HTMLElement) => void) {
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const origin = useRef<{ x: number; y: number } | null>(null);

  const cancel = () => {
    clearTimeout(timer.current);
    origin.current = null;
  };

  return {
    onPointerDown: (e: React.PointerEvent<HTMLElement>) => {
      if (e.button !== 0) return;
      const target = e.currentTarget;
      origin.current = { x: e.clientX, y: e.clientY };
      timer.current = setTimeout(() => onPress(target), HOLD_MS);
    },
    onPointerMove: (e: React.PointerEvent) => {
      const o = origin.current;
      if (o && Math.hypot(e.clientX - o.x, e.clientY - o.y) > SLOP_PX) cancel();
    },
    onPointerUp: cancel,
    onPointerLeave: cancel,
    onContextMenu: (e: React.MouseEvent<HTMLElement>) => {
      e.preventDefault();
      cancel();
      onPress(e.currentTarget);
    },
    onDoubleClick: (e: React.MouseEvent<HTMLElement>) => onPress(e.currentTarget),
  };
}
