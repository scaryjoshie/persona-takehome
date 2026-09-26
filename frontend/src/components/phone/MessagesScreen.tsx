import "./f7";
import { useState } from "react";
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
  const send = () => {
    if (!draft.trim()) return;
    onSend?.(draft.trim());
    setDraft("");
  };
  const lastSentId = messages.findLast((m) => m.side === "sent")?.id;
  const endsSent = messages.at(-1)?.side === "sent";

  return (
    <div className={`messages-screen${keyboard ? " has-keyboard" : ""}`}>
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
          <Messages>
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
