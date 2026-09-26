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
}: Props) {
  const [draft, setDraft] = useState(initialDraft);
  const rootRef = useRef<HTMLDivElement>(null);
  useStickToBottom(rootRef, messages.length, typing);
  const send = () => {
    if (!draft.trim()) return;
    onSend?.(draft.trim());
    setDraft("");
  };
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
            onInput={(e) => setDraft((e.target as HTMLTextAreaElement).value)}
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
            {messages.map((m, i) => {
              const first = messages[i - 1]?.side !== m.side;
              const last = messages[i + 1]?.side !== m.side;
              const footer = endsSent && m.id === lastSentId ? "Delivered" : undefined;
              return (
                <Message key={m.id} type={m.side} text={m.text} first={first} last={last} tail={last} footer={footer} />
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
