import { useState } from "react";

// Apple's iPhone 17 Pro bezel (Apple Design Resources) laid over the screen. The PNG is gitignored
// because Apple's license does not allow redistributing it; scripts/fetch-bezel.sh downloads it
// (macOS only). Without it, a plain CSS frame stands in. The PNG is 1350x2760 @3x with a transparent
// 1206x2622 screen hole at (72, 69): 402x874 pt at 1x.
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
  const [bezel, setBezel] = useState<"loading" | "loaded" | "missing">("loading");
  return (
    <div style={{ width: FRAME.width * scale, height: FRAME.height * scale }}>
      <div style={{ position: "relative", ...FRAME, transform: `scale(${scale})`, transformOrigin: "top left" }}>
        {bezel === "missing" && <FallbackFrame />}
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
          onLoad={() => setBezel("loaded")}
          onError={() => setBezel("missing")}
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            pointerEvents: "none",
            zIndex: 1,
            display: bezel === "missing" ? "none" : undefined,
          }}
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

/** A plain titanium frame for when Apple's bezel artwork is not installed (fresh clones, deploys). */
function FallbackFrame() {
  return (
    <div
      aria-hidden
      style={{
        position: "absolute",
        inset: 0,
        borderRadius: 86,
        background: "linear-gradient(145deg, #d9dade, #8e9096 45%, #c9cacf)",
        boxShadow: "inset 0 0 0 2px rgba(255,255,255,0.35), inset 0 0 0 14px #111, 0 30px 60px rgba(0,0,0,0.5)",
      }}
    />
  );
}
