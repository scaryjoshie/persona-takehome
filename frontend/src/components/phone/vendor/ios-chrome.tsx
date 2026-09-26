/**
 * iOS status bar and system keyboard, vendored from zoewu-creator/texting-ui-templates (MIT),
 * src/lib.tsx: https://github.com/zoewu-creator/texting-ui-templates
 *
 * Local changes: Remotion's font loader replaced by the system stack; side padding widened to
 * center the glyphs in the ears beside the Dynamic Island; dark battery drawn full instead of low. Drawn on the upstream
 * 1080px-wide canvas; wrap in <Scaled> to fit a 402pt screen.
 */
import React from "react";

export const INTER = '-apple-system, "SF Pro Text", Inter, system-ui, sans-serif';
export const W = 1080;

/** Renders children laid out on the 1080px canvas, scaled to `width`. */
export const Scaled: React.FC<{ width: number; height: number; children: React.ReactNode }> = ({ width, height, children }) => (
  <div style={{ width, height: height * (width / W), overflow: "hidden" }}>
    <div style={{ width: W, height, transform: `scale(${width / W})`, transformOrigin: "0 0" }}>{children}</div>
  </div>
);

/* ---------------------------------------------------------------- status bar */

export const STATUS_H = 86;

type StatusTheme = "light" | "dark";

/**
 * iOS status bar. `light` is the WhatsApp look (dark glyphs on a pale bar);
 * `dark` matches a phone in dark mode — a silenced bell, signal + 5G, and a
 * low battery — exactly what the Discord / Telegram references show.
 */
export const IOSStatusBar: React.FC<{
  theme?: StatusTheme; time?: string; bg?: string;
}> = ({ theme = "light", time = "9:41", bg }) => {
  const ink = theme === "dark" ? "#FFFFFF" : "#111B21";
  const background = bg ?? (theme === "dark" ? "transparent" : "#F6F6F6");
  return (
    <div style={{
      height: STATUS_H, background, display: "flex", alignItems: "flex-end",
      justifyContent: "space-between", padding: "0 150px 12px 130px", fontFamily: INTER,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <div style={{ fontSize: 34, fontWeight: 600, color: ink, letterSpacing: -0.4 }}>{time}</div>
        {theme === "dark" && (
          <svg width="30" height="30" viewBox="0 0 24 24">
            <path d="M6.5 9.5a5.5 5.5 0 0 1 11 0c0 4 1.4 5.4 1.4 5.4H5.1s1.4-1.4 1.4-5.4z"
              stroke={ink} strokeWidth="1.7" fill="none" strokeLinejoin="round" />
            <path d="M10.4 18.4a2 2 0 0 0 3.2 0" stroke={ink} strokeWidth="1.7" fill="none" strokeLinecap="round" />
            <path d="M4 4 L20 20" stroke={ink} strokeWidth="1.7" strokeLinecap="round" />
          </svg>
        )}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        {/* signal bars */}
        <svg width="34" height="24" viewBox="0 0 34 24">
          {[0, 1, 2, 3].map((i) => (
            <rect key={i} x={i * 8.6} y={16 - i * 5} width="6" height={8 + i * 5} rx="1.6" fill={ink} />
          ))}
        </svg>
        {theme === "dark" ? (
          <div style={{ fontSize: 28, fontWeight: 600, color: ink, letterSpacing: -0.2 }}>5G</div>
        ) : (
          <svg width="30" height="22" viewBox="0 0 30 22">
            <path d="M2 8 A18 18 0 0 1 28 8" stroke={ink} strokeWidth="3.4" fill="none" strokeLinecap="round" />
            <path d="M7 13 A11 11 0 0 1 23 13" stroke={ink} strokeWidth="3.4" fill="none" strokeLinecap="round" />
            <circle cx="15" cy="18.5" r="2.6" fill={ink} />
          </svg>
        )}
        {/* battery — low + yellow on dark, to match the refs */}
        <svg width="42" height="22" viewBox="0 0 42 22">
          <rect x="1" y="2" width="34" height="18" rx="5" stroke={ink} strokeWidth="2.2" fill="none" opacity="0.4" />
          {theme === "dark" ? (
            <rect x="3.6" y="4.6" width="24" height="12.8" rx="3" fill={ink} />
          ) : (
            <rect x="3.6" y="4.6" width="24" height="12.8" rx="3" fill={ink} />
          )}
          <path d="M37.5 8 v6 a4 4 0 0 0 0-6z" fill={ink} opacity="0.4" />
        </svg>
      </div>
    </div>
  );
};

/* ------------------------------------------------------------------ keyboard */

export const KB_H = 690;

export type KBTheme = {
  bg: string; key: string; keyDark: string; ink: string;
  hot: string; hotInk: string; keyShadow: string;
};

export const KB_LIGHT: KBTheme = {
  bg: "#D1D4DA", key: "#FFFFFF", keyDark: "#ADB3BE", ink: "#111B21",
  hot: "#8E96A3", hotInk: "#FFFFFF", keyShadow: "rgba(0,0,0,0.28)",
};
export const KB_DARK: KBTheme = {
  bg: "#2C2C2E", key: "#6C6C70", keyDark: "#48484A", ink: "#FFFFFF",
  hot: "#8E8E93", hotInk: "#FFFFFF", keyShadow: "rgba(0,0,0,0.5)",
};

const ROWS = [
  ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"],
  ["a", "s", "d", "f", "g", "h", "j", "k", "l"],
  ["z", "x", "c", "v", "b", "n", "m"],
];

const ShiftGlyph: React.FC<{ c: string }> = ({ c }) => (
  <svg width="40" height="40" viewBox="0 0 24 24">
    <path d="M12 3.6 L20 12 h-4.6 v6.6 h-6.8 V12 H4 z" fill={c} />
  </svg>
);
const BackGlyph: React.FC<{ c: string; bg: string }> = ({ c, bg }) => (
  <svg width="46" height="40" viewBox="0 0 28 24">
    <path d="M9 3.4 h15.2 a2 2 0 0 1 2 2 v13.2 a2 2 0 0 1-2 2 H9 L1.4 12 z" fill={c} />
    <path d="M13.4 8.6 l7 6.8 M20.4 8.6 l-7 6.8" stroke={bg} strokeWidth="2.2" strokeLinecap="round" />
  </svg>
);
const EmojiGlyph: React.FC<{ c: string }> = ({ c }) => (
  <svg width="42" height="42" viewBox="0 0 24 24">
    <circle cx="12" cy="12" r="9.4" stroke={c} strokeWidth="1.8" fill="none" />
    <path d="M8 14.2c1 1.1 2.4 1.7 4 1.7s3-.6 4-1.7" stroke={c} strokeWidth="1.8" fill="none" strokeLinecap="round" />
    <circle cx="9.2" cy="9.6" r="1.3" fill={c} />
    <circle cx="14.8" cy="9.6" r="1.3" fill={c} />
  </svg>
);

/** The iOS system keyboard. Themed light or dark; the pressed key flashes. */
export const IOSKeyboard: React.FC<{ activeKey: string | null; theme?: KBTheme }> = ({
  activeKey, theme = KB_LIGHT,
}) => {
  const kw = (W - 2 * 12) / 10 - 10;
  const kh = 116;
  const key = (id: string, body: React.ReactNode, width: number, dark = false, big = false) => {
    const hot = activeKey === id;
    return (
      <div key={id + width} style={{
        width, height: kh, borderRadius: 14,
        background: hot ? theme.hot : dark ? theme.keyDark : theme.key,
        display: "flex", alignItems: "center", justifyContent: "center",
        fontFamily: INTER, fontSize: big ? 30 : 44, fontWeight: 400,
        color: hot ? theme.hotInk : theme.ink,
        boxShadow: hot ? "none" : `0 1.5px 0 ${theme.keyShadow}`,
        transform: hot ? "scale(0.96)" : "none",
      }}>{body}</div>
    );
  };
  return (
    <div style={{
      height: KB_H, background: theme.bg, paddingTop: 18,
      display: "flex", flexDirection: "column", gap: 20, alignItems: "center",
    }}>
      {ROWS.map((row, ri) => (
        <div key={ri} style={{ display: "flex", gap: 10, justifyContent: "center" }}>
          {ri === 2 && key("shift", <ShiftGlyph c={theme.ink} />, kw * 1.5, true)}
          {row.map((k) => key(k, k, kw))}
          {ri === 2 && key("back", <BackGlyph c={theme.ink} bg={theme.bg} />, kw * 1.5, true)}
        </div>
      ))}
      <div style={{ display: "flex", gap: 10, justifyContent: "center", marginTop: 4 }}>
        {key("123", "123", kw * 1.4, true, true)}
        {key("emoji", <EmojiGlyph c={theme.ink} />, kw, true)}
        {key("space", "space", kw * 4.6)}
        {key("return", "return", kw * 2.2, true, true)}
      </div>
    </div>
  );
};
