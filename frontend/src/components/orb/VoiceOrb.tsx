import { Orb } from "orb-ui";

export type OrbMode = "off" | "on" | "speaking";

interface Props {
  mode: OrbMode;
  /** Agent output level, 0..1. Only read while speaking. */
  level?: number;
  size?: number;
  className?: string;
}

/**
 * The voice orb: orb-ui's cloud theme (MIT) in controlled mode.
 * "off" is a still sphere, dimmed (orb-ui's own idle state hides the cloud entirely);
 * "on" is listening; "speaking" breathes with `level`.
 */
export function VoiceOrb({ mode, level = 0, size = 220, className }: Props) {
  const signal =
    mode === "speaking"
      ? { state: "speaking" as const, inputVolume: 0, outputVolume: level }
      : mode === "on"
        ? { state: "listening" as const, inputVolume: 0.15, outputVolume: 0 }
        : { state: "listening" as const, inputVolume: 0, outputVolume: 0 };

  return (
    <div
      className={className}
      style={{
        width: size,
        height: size,
        opacity: mode === "off" ? 0.4 : 1,
        transform: mode === "off" ? "scale(0.85)" : "scale(1)",
        transition: "opacity 600ms ease, transform 600ms ease",
      }}
    >
      <Orb theme="cloud" size={size} signal={signal} interactive={false} />
    </div>
  );
}
