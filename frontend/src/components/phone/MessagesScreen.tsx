import "./f7";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  App,
  Icon,
  Link,
  Message,
  Messagebar,
  Messages,
  MessagesTitle,
  NavLeft,
  NavRight,
  NavTitle,
  Navbar,
  Page,
} from "framework7-react";
import { IOSKeyboard, IOSStatusBar, KB_DARK, KB_H, STATUS_H, Scaled } from "./vendor/ios-chrome";
import { SCREEN } from "./IPhone17Pro";
import { LinkPreview } from "./LinkPreview";
import { firstLink, isOnlyLink, splitLinks } from "../../lib/links";
import "./messages-screen.css";

export interface ThreadMessage {
  id: string;
  side: "sent" | "received";
  text: string;
}

interface Props {
  contact: string;
  messages: ThreadMessage[];
  typing?: boolean;
  keyboard?: boolean;
  onSend?: (text: string) => void;
  /** The phone icon in the header. Hidden when absent. */
  onCall?: () => void;
  /** Text already in the composer when the screen appears. */
  initialDraft?: string;
  /** True while the user is composing: on the first keystroke, false on send, clear, or 3 s idle. */
  onTyping?: (active: boolean) => void;
}

/** iOS 26 Messages, dark: Framework7's Navbar, Messages and Messagebar inside a status bar and home indicator. */
export function MessagesScreen({
  contact,
  messages,
  typing = false,
  keyboard = false,
  onSend,
  onCall,
  initialDraft = "",
  onTyping,
}: Props) {
  const [draft, setDraft] = useState(initialDraft);
  const typingSignal = useTypingSignal(onTyping);
  const rootRef = useRef<HTMLDivElement>(null);
  useStickToBottom(rootRef, messages.length, typing);
  const send = () => {
    if (!draft.trim()) return;
    typingSignal.stop();
    onSend?.(draft.trim());
    setDraft("");
  };
  const edit = (text: string) => {
    setDraft(text);
    typingSignal.update(text);
  };
  useEnterToSend(rootRef, send);
  const lastSentId = messages.findLast((m) => m.side === "sent")?.id;
  const endsSent = messages.at(-1)?.side === "sent";

  return (
    <div ref={rootRef} className={`messages-screen${keyboard ? " has-keyboard" : ""}`}>
      <div className="status-bar">
        <Scaled width={SCREEN.width} height={STATUS_H}>
          <IOSStatusBar theme="dark" time="9:41" />
        </Scaled>
      </div>
      <App theme="ios" darkMode name="messages">
        <Page messagesContent>
          <Navbar>
            <NavLeft backLink />
            <NavTitle>
              <div className="contact-avatar">{contact.slice(0, 1)}</div>
              <div className="contact-name">
                {contact} <Icon f7="chevron_right" />
              </div>
            </NavTitle>
            <NavRight>{onCall && <Link iconF7="phone_fill" onClick={onCall} aria-label="Call" />}</NavRight>
          </Navbar>
          <Messagebar
            placeholder="iMessage"
            value={draft}
            onInput={(e) => edit((e.target as HTMLTextAreaElement).value)}
            onSubmit={send}
          >
            <Link slot="inner-start" iconF7="plus" />
            {draft ? (
              <Link slot="after-area" className="send-button" iconF7="arrow_up" onClick={send} aria-label="Send" />
            ) : (
              <Link slot="inner-end" iconF7="mic" aria-label="Audio message" />
            )}
          </Messagebar>
          <Messages scrollMessages={false}>
            <MessagesTitle>
              <b>iMessage</b>
              <br />
              Today 9:41 AM
            </MessagesTitle>
            {bubblesFor(messages).map((b, i, all) => {
              const first = all[i - 1]?.side !== b.side;
              const last = all[i + 1]?.side !== b.side;
              const footer = endsSent && b.messageId === lastSentId && last ? "Delivered" : undefined;
              return (
                <Message
                  key={b.key}
                  type={b.side}
                  first={first}
                  last={last}
                  tail={last && !b.link}
                  footer={footer}
                  className={b.link ? "message-link" : undefined}
                >
                  <span slot="text">{b.link ? <LinkPreview url={b.link} /> : <LinkedText text={b.text} />}</span>
                </Message>
              );
            })}
            {typing && <Message type="received" typing first last tail />}
          </Messages>
        </Page>
      </App>
      {keyboard && (
        <div className="keyboard">
          <Scaled width={SCREEN.width} height={KB_H}>
            <IOSKeyboard activeKey={null} theme={KB_DARK} />
          </Scaled>
        </div>
      )}
      <div className="home-indicator" />
    </div>
  );
}

/**
 * Keeps the thread pinned to the newest message the way iMessage does: if the reader is at the
 * bottom, every change glides to the new bottom; if they scrolled up to read, it leaves them there.
 * Framework7's own auto-scroll is off because it only fires when the message count changes, so a
 * typing bubble turning into a message grew the thread without following it.
 */
function useStickToBottom(root: React.RefObject<HTMLDivElement | null>, count: number, typing: boolean) {
  const atBottom = useRef(true);

  useEffect(() => {
    const el = root.current?.querySelector<HTMLElement>(".page-content");
    if (!el) return;
    const onScroll = () => {
      atBottom.current = el.scrollHeight - el.clientHeight - el.scrollTop < 60;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    el.scrollTop = el.scrollHeight;
    return () => el.removeEventListener("scroll", onScroll);
  }, [root]);

  useLayoutEffect(() => {
    const el = root.current?.querySelector<HTMLElement>(".page-content");
    if (el && atBottom.current) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [root, count, typing]);
}

const TYPING_IDLE_MS = 3000;

/** Turns keystrokes into typing on/off signals: on at the first character, off when idle or cleared. */
function useTypingSignal(onTyping?: (active: boolean) => void) {
  const active = useRef(false);
  const idle = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const stop = () => {
    clearTimeout(idle.current);
    if (active.current) onTyping?.(false);
    active.current = false;
  };
  const update = (text: string) => {
    if (!text.trim()) return stop();
    if (!active.current) onTyping?.(true);
    active.current = true;
    clearTimeout(idle.current);
    idle.current = setTimeout(stop, TYPING_IDLE_MS);
  };
  useEffect(() => () => clearTimeout(idle.current), []);
  return { update, stop };
}

/** Enter sends, Shift+Enter breaks the line, and neither fires while an IME is composing. */
function useEnterToSend(root: React.RefObject<HTMLDivElement | null>, send: () => void) {
  const latest = useRef(send);
  latest.current = send;
  useEffect(() => {
    const textarea = root.current?.querySelector("textarea");
    if (!textarea) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
      e.preventDefault();
      latest.current();
    };
    textarea.addEventListener("keydown", onKeyDown);
    return () => textarea.removeEventListener("keydown", onKeyDown);
  }, [root]);
}

interface Bubble {
  key: string;
  messageId: string;
  side: ThreadMessage["side"];
  text: string;
  /** Set for a link card. */
  link?: string;
}

/** As in Messages: a lone link becomes a card; text with a link shows the text, then the card. */
function bubblesFor(messages: ThreadMessage[]): Bubble[] {
  return messages.flatMap((m): Bubble[] => {
    const link = firstLink(m.text);
    const base = { messageId: m.id, side: m.side, text: m.text };
    if (!link) return [{ ...base, key: m.id }];
    if (isOnlyLink(m.text)) return [{ ...base, key: m.id, link }];
    return [
      { ...base, key: m.id },
      { ...base, key: `${m.id}:link`, link },
    ];
  });
}

/** Message text with its links made tappable. */
function LinkedText({ text }: { text: string }) {
  return (
    <>
      {splitLinks(text).map((part, i) =>
        part.href ? (
          <a key={i} className="message-inline-link" href={part.href} target="_blank" rel="noreferrer">
            {part.text}
          </a>
        ) : (
          part.text
        ),
      )}
    </>
  );
}
