import type { ReactNode } from "react";

interface Props {
  phone: ReactNode;
  orb: ReactNode;
  /** Under the orb: transcript, then controls. The column keeps its width so nothing shifts. */
  caption?: ReactNode;
  controls?: ReactNode;
}

/** The layout: the handset and the orb as a centered pair, the gap scaling with the window. */
export function Stage({ phone, orb, caption, controls }: Props) {
  return (
    <div className="flex h-full w-full items-center justify-center gap-[clamp(3rem,10vw,14rem)] px-8">
      {phone}
      {/* Fixed-height slots below the orb, so the orb never moves when they fill. */}
      <div className="flex w-[340px] flex-col items-center gap-8">
        {orb}
        {/* Bottom-anchored: the newest line always shows; older ones rise and fade out at the top. */}
        <div className="flex h-32 w-full items-end justify-center overflow-hidden [mask-image:linear-gradient(to_bottom,transparent,black_35%)]">
          {caption}
        </div>
        <div className="flex h-14 items-center">{controls}</div>
      </div>
    </div>
  );
}
