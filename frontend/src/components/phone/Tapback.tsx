import { useLayoutEffect, useRef } from "react";
import { Icon } from "framework7-react";

export interface Reaction {
  emoji: string;
  by: "user" | "agent";
}

/** The six classic tapbacks, drawn as Messages draws them rather than as emoji. */
const CLASSIC: Array<{ emoji: string; icon?: string; text?: string; label: string }> = [
  { emoji: "❤️", icon: "heart_fill", label: "Love" },
  { emoji: "👍", icon: "hand_thumbsup_fill", label: "Like" },
  { emoji: "👎", icon: "hand_thumbsdown_fill", label: "Dislike" },
  { emoji: "😂", text: "HA\nHA", label: "Laugh" },
  { emoji: "‼️", text: "!!", label: "Emphasize" },
  { emoji: "❓", text: "?", label: "Question" },
];

function Glyph({ emoji, size }: { emoji: string; size: number }) {
  const classic = CLASSIC.find((c) => c.emoji === emoji);
  if (classic?.icon) return <Icon f7={classic.icon} style={{ fontSize: size }} />;
  if (classic?.text)
    return (
      <span className="tapback-text" style={{ fontSize: size * (classic.text.length > 2 ? 0.42 : 0.8) }}>
        {classic.text}
      </span>
    );
  return <span style={{ fontSize: size * 0.85, lineHeight: 1 }}>{emoji}</span>;
}

/** The small bubble on a message's top corner, one per reaction (the newest on top). */
export function ReactionBadges({
  reactions,
  side,
  slot,
}: {
  reactions: Reaction[];
  side: "sent" | "received";
  slot?: string;
}) {
  if (!reactions.length) return null;
  return (
    <span
      slot={slot}
      className={`tapback-badges tapback-badges-${side}`}
      aria-label={reactions.map((r) => r.emoji).join(" ")}
    >
      {reactions.map((r, i) => (
        <span
          key={`${r.by}${r.emoji}`}
          className={`tapback-badge${r.by === "user" ? " is-mine" : ""}`}
          style={{ zIndex: i }}
        >
          <Glyph emoji={r.emoji} size={17} />
        </span>
      ))}
    </span>
  );
}

interface MenuProps {
  /** The pressed message element, cloned above the dimmed thread. */
  target: HTMLElement;
  /** The screen element the menu is positioned in. */
  root: HTMLElement;
  side: "sent" | "received";
  /** The user's current tapback on this message, if any. */
  mine: string | null;
  onReact: (emoji: string | null) => void;
  onReply?: () => void;
  onCopy: () => void;
  onClose: () => void;
}

/** Press-and-hold: the thread dims, the message lifts, tapbacks above and actions below. */
export function TapbackMenu({ target, root, side, mine, onReact, onReply, onCopy, onClose }: MenuProps) {
  const lifted = useRef<HTMLDivElement>(null);
  // The phone is scaled with a transform; convert page pixels into the screen's own pixels.
  const rootRect = root.getBoundingClientRect();
  const scale = rootRect.width / root.offsetWidth;
  const r = target.getBoundingClientRect();
  const box = {
    top: (r.top - rootRect.top) / scale,
    left: (r.left - rootRect.left) / scale,
    width: r.width / scale,
    height: r.height / scale,
  };

  useLayoutEffect(() => {
    const copy = target.cloneNode(true) as HTMLElement;
    copy.style.maxWidth = "none";
    copy.style.margin = "0";
    lifted.current?.replaceChildren(copy);
  }, [target]);

  const edge = side === "sent" ? { right: 16 } : { left: 16 };
  return (
    <div className="tapback-overlay" onClick={onClose}>
      <div
        className="tapback-bar"
        style={{ top: Math.max(60, box.top - 56), ...edge }}
        onClick={(e) => e.stopPropagation()}
      >
        {CLASSIC.map((c) => (
          <button
            key={c.emoji}
            type="button"
            aria-label={c.label}
            aria-pressed={mine === c.emoji}
            className={`tapback-option${mine === c.emoji ? " is-selected" : ""}`}
            onClick={() => onReact(mine === c.emoji ? null : c.emoji)}
          >
            <Glyph emoji={c.emoji} size={20} />
          </button>
        ))}
      </div>
      <div ref={lifted} className="tapback-lifted" style={box} />
      <div
        className="tapback-actions"
        style={{ top: box.top + box.height + 10, ...edge }}
        onClick={(e) => e.stopPropagation()}
      >
        {onReply && (
          <button type="button" onClick={onReply}>
            Reply <Icon f7="arrowshape_turn_up_left_fill" />
          </button>
        )}
        <button type="button" onClick={onCopy}>
          Copy <Icon f7="doc_on_doc" />
        </button>
      </div>
    </div>
  );
}
