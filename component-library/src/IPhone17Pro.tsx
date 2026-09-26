// Apple's iPhone 17 Pro bezel (Apple Design Resources) laid over the screen.
// The PNG is 1350x2760 @3x with a transparent 1206x2622 screen hole at (72, 69): 402x874 pt at 1x.
export const SCREEN = { width: 402, height: 874 };

type Finish = "silver" | "deep-blue";

export function IPhone17Pro({ finish = "silver", children }: { finish?: Finish; children: React.ReactNode }) {
  return (
    <div style={{ position: "relative", width: 450, height: 920 }}>
      <div style={{ position: "absolute", left: 24, top: 23, ...SCREEN, borderRadius: 62, overflow: "hidden", background: "#000" }}>
        {children}
      </div>
      <img src={`/bezels/iphone-17-pro-${finish}.png`} alt="" style={{ position: "absolute", inset: 0, width: "100%", pointerEvents: "none" }} />
    </div>
  );
}
