import { useLayoutEffect, useRef, useState } from "react";
import { Icon } from "framework7-react";

export interface Draft {
  ref: string;
  to: string;
  subject: string;
  body: string;
  status: "draft" | "sent";
}

const FIELD_NAMES = { to: "recipient", subject: "subject", body: "message" } as const;

/** What a draft still needs, in plain words. It is ready to send once this is empty. */
export function missingFields(d: Draft): string[] {
  return (["to", "subject", "body"] as const).filter((f) => !d[f].trim()).map((f) => FIELD_NAMES[f]);
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="draft-field">
      <span className="draft-label">{label}</span>
      {value.trim() ? <span className="draft-value">{value}</span> : <span className="draft-missing">missing</span>}
    </div>
  );
}

/**
 * An email the agent drafted, as a preview card: To, Subject and body, with empty fields marked
 * "missing" so it is clear what is left. Send works once nothing is missing; then it reads "Sent".
 */
export function EmailDraftCard({ draft, onSend }: { draft: Draft; onSend?: (ref: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  const sent = draft.status === "sent";
  const missing = missingFields(draft);
  const body = useRef<HTMLParagraphElement>(null);
  const clamped = useIsClamped(body, draft.body, expanded);

  return (
    <div className="draft-card">
      <div className="draft-header">
        <Icon f7={sent ? "envelope_fill" : "envelope"} />
        {sent ? "Email sent" : "Email draft"}
      </div>
      <Field label="To" value={draft.to} />
      <Field label="Subject" value={draft.subject} />
      <div className="draft-body">
        {draft.body.trim() ? (
          <p ref={body} className={expanded ? "" : "is-clamped"}>
            {draft.body}
          </p>
        ) : (
          <span className="draft-missing">missing</span>
        )}
        {(clamped || expanded) && (
          <button type="button" className="draft-more" onClick={() => setExpanded((e) => !e)}>
            {expanded ? "Less" : "More"}
          </button>
        )}
      </div>
      <div className="draft-footer">
        {sent ? (
          <span className="draft-sent">
            Sent <Icon f7="checkmark_alt" />
          </span>
        ) : (
          <>
            <span className="draft-hint">{missing.length ? `Missing ${missing.join(", ")}` : "Ready to send"}</span>
            <button
              type="button"
              className="draft-send"
              disabled={missing.length > 0 || !onSend}
              onClick={() => onSend?.(draft.ref)}
            >
              Send
            </button>
          </>
        )}
      </div>
    </div>
  );
}

/** Whether the clamped body is actually cut off, measured after layout rather than guessed from length. */
function useIsClamped(el: React.RefObject<HTMLElement | null>, text: string, expanded: boolean): boolean {
  const [clamped, setClamped] = useState(false);
  useLayoutEffect(() => {
    if (!expanded && el.current) setClamped(el.current.scrollHeight > el.current.clientHeight + 1);
  }, [el, text, expanded]);
  return clamped;
}
