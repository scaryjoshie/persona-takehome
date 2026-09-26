// Apple's iPhone 17 Pro bezel (Apple Design Resources) laid over the screen. The PNG is gitignored;
// scripts/fetch-bezel.sh downloads it. 1350x2760 @3x with a transparent 1206x2622 screen hole at
// (72, 69): 402x874 pt at 1x.
export const SCREEN = { width: 402, height: 874 };
const FRAME = { width: 450, height: 920 };
const SCREEN_OFFSET = { left: 24, top: 23 };

type Finish = "silver" | "deep-blue";

interface Props {
  finish?: Finish;
  /** 1 = 402pt screen at 1px per pt. */
  scale?: number;
  /** Drawn above the bezel, in screen coordinates (the Dynamic Island content). */
  overlay?: React.ReactNode;
  children: React.ReactNode;
}

export function IPhone17Pro({ finish = "silver", scale = 1, overlay, children }: Props) {
  return (
    <div style={{ width: FRAME.width * scale, height: FRAME.height * scale }}>
      <div style={{ position: "relative", ...FRAME, transform: `scale(${scale})`, transformOrigin: "top left" }}>
        {/* zIndex: 0 keeps Framework7's own z-indices inside the screen. */}
        <div
          style={{
            position: "absolute",
            ...SCREEN_OFFSET,
            ...SCREEN,
            borderRadius: 62,
            overflow: "hidden",
            background: "#000",
            zIndex: 0,
          }}
        >
          {children}
        </div>
        <img
          src={`/bezels/iphone-17-pro-${finish}.png`}
          alt=""
          draggable={false}
          style={{ position: "absolute", inset: 0, width: "100%", pointerEvents: "none", zIndex: 1 }}
        />
        {overlay && (
          <div style={{ position: "absolute", ...SCREEN_OFFSET, ...SCREEN, pointerEvents: "none", zIndex: 2 }}>
            {overlay}
          </div>
        )}
      </div>
    </div>
  );
}
