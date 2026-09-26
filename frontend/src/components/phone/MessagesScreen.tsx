import "./f7";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
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
import { VoiceNoteBubble, type VoiceNote } from "./VoiceNoteBubble";
import { ReactionBadges, TapbackMenu, type Reaction } from "./Tapback";
import { ContactBanner, ContactCardBubble } from "./ContactCard";
import { useLongPress } from "../../lib/useLongPress";
import { LiveWaveform } from "../ui/live-waveform";
import { firstLink, isOnlyLink, splitLinks } from "../../lib/links";
import { useVoiceRecorder, type Recording } from "../../audio/useVoiceRecorder";
import "./messages-screen.css";

export interface ThreadMessage {
  id: string;
  side: "sent" | "received";
  text: string;
  /** ISO time it was sent; the thread header shows the first one. */
  ts?: string;
  /** Set for an audio message; `text` is then unused. */
  voice?: VoiceNote;
  /** The server's id for an audio message, once uploaded. */
  audioId?: string;
  /** Tapbacks on this message, oldest first. */
  reactions?: Reaction[];
  /** The message this one replies to, shown faded above it. */
  replyTo?: Pick<ThreadMessage, "id" | "side" | "text">;
  /** Set for the agent's contact card attachment. */
  contact?: { name: string };
}

interface Props {
  contact: string;
  messages: ThreadMessage[];
  /** Shown under the user's latest text while it ends the thread. */
  receipt?: string;
  typing?: boolean;
  keyboard?: boolean;
  /** Sends a text; `replyTo` is the id of the message it answers, if any. */
  onSend?: (text: string, replyTo?: string) => void;
  /** The phone icon in the header. Hidden when absent. */
  onCall?: () => void;
  /** Text already in the composer when the screen appears. */
  initialDraft?: string;
  /** True while the user is composing: on the first keystroke, false on send, clear, or 3 s idle. */
  onTyping?: (active: boolean) => void;
  /** Sends a recorded audio message. The mic button is inert without it. */
  onSendVoiceNote?: (recording: Recording) => void;
  /** Sets (or with `null` removes) the user's tapback on a message. */
  onReact?: (messageId: string, emoji: string | null) => void;
  /** A contact name the user has not saved yet; shows the "updated their name" banner. */
  contactOffer?: string | null;
  onSaveContact?: () => void;
}

/** iOS 26 Messages, dark: Framework7's Navbar, Messages and Messagebar inside a status bar and home indicator. */
export function MessagesScreen({
  contact,
  messages,
  receipt = "Delivered",
  typing = false,
  keyboard = false,
  onSend,
  onCall,
  initialDraft = "",
  onTyping,
  onSendVoiceNote,
  onReact,
  contactOffer = null,
  onSaveContact,
}: Props) {
  const [draft, setDraft] = useState(initialDraft);
  const typingSignal = useTypingSignal(onTyping);
  const clock = useClock();
  const rootRef = useRef<HTMLDivElement>(null);
  useStickToBottom(rootRef, messages.length, typing);
  const [replyingTo, setReplyingTo] = useState<ThreadMessage | null>(null);
  const send = () => {
    if (!draft.trim()) return;
    typingSignal.stop();
    onSend?.(draft.trim(), replyingTo?.id);
    setDraft("");
    setReplyingTo(null);
  };
  const edit = (text: string) => {
    setDraft(text);
    typingSignal.update(text);
  };
  useEnterToSend(rootRef, send);

  // Audio messages: the mic opens a recording bar; send stops and hands the clip up, ✕ discards it.
  const recorder = useVoiceRecorder();
  const [recording, setRecording] = useState(false);
  const stopRecording = async (keep: boolean) => {
    if (keep) {
      const clip = await recorder.stop();
      if (clip && clip.durationMs > 300) onSendVoiceNote?.(clip);
    } else recorder.cancel();
    setRecording(false);
  };
  // Stable, like recorder.start: the live waveform reopens the mic whenever these change.
  const { cancel: cancelRecorder } = recorder;
  const abandonRecording = useCallback(() => {
    cancelRecorder();
    setRecording(false);
  }, [cancelRecorder]);
  // Press-and-hold on a bubble opens tapbacks and actions for its message.
  const [pressed, setPressed] = useState<{ message: ThreadMessage; target: HTMLElement } | null>(null);
  const press = (message: ThreadMessage) => (el: HTMLElement) => {
    const target = el.closest<HTMLElement>(".message");
    if (target) setPressed({ message, target });
  };
  const lastSentId = messages.findLast((m) => m.side === "sent")?.id;
  const endsSent = messages.at(-1)?.side === "sent";

  return (
    <div ref={rootRef} className={`messages-screen${keyboard ? " has-keyboard" : ""}`}>
      <div className="status-bar">
        <Scaled width={SCREEN.width} height={STATUS_H}>
          <IOSStatusBar theme="dark" time={clock} />
        </Scaled>
      </div>
      <App theme="ios" darkMode name="messages">
        <Page messagesContent>
          <Navbar>
            <NavLeft backLink />
            <NavTitle>
              <div className="contact-avatar">
                {/^[a-z]/i.test(contact) ? contact.slice(0, 1).toUpperCase() : <Icon f7="person_fill" />}
              </div>
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
            {replyingTo && (
              <div slot="before-inner" className="reply-preview">
                <div className="reply-preview-text">
                  <span className="reply-preview-label">
                    Replying to {replyingTo.side === "sent" ? "yourself" : contact}
                  </span>
                  <span className="reply-preview-quote">{quoteOf(replyingTo)}</span>
                </div>
                <button type="button" aria-label="Cancel reply" onClick={() => setReplyingTo(null)}>
                  <Icon f7="xmark_circle_fill" />
                </button>
              </div>
            )}
            <Link slot="inner-start" iconF7="plus" />
            {draft ? (
              <Link slot="after-area" className="send-button" iconF7="arrow_up" onClick={send} aria-label="Send" />
            ) : (
              <Link
                slot="inner-end"
                iconF7="mic"
                aria-label="Record audio message"
                onClick={onSendVoiceNote ? () => setRecording(true) : undefined}
              />
            )}
            {recording && (
              <div slot="after-inner" className="recording-bar">
                <button
                  type="button"
                  className="recording-cancel"
                  aria-label="Discard"
                  onClick={() => stopRecording(false)}
                >
                  <Icon f7="xmark" />
                </button>
                <div className="recording-pill">
                  <span className="recording-dot" />
                  <LiveWaveform
                    active
                    mode="scrolling"
                    height={24}
                    barWidth={2.5}
                    barGap={2}
                    barColor="#fff"
                    className="recording-wave"
                    onStreamReady={recorder.start}
                    onError={abandonRecording}
                  />
                  <RecordingClock since={recorder.startedAt} />
                </div>
                <button
                  type="button"
                  className="recording-send"
                  aria-label="Send audio message"
                  onClick={() => stopRecording(true)}
                >
                  <Icon f7="arrow_up" />
                </button>
              </div>
            )}
          </Messagebar>
          <Messages scrollMessages={false}>
            <MessagesTitle>
              <b>iMessage</b>
              <br />
              {threadDate(messages[0]?.ts)}
            </MessagesTitle>
            {bubblesFor(messages).map((b, i, all) => {
              const message = messages.find((m) => m.id === b.messageId)!;
              const first = all[i - 1]?.side !== b.side;
              const last = all[i + 1]?.side !== b.side;
              const footer = endsSent && b.messageId === lastSentId && last ? receipt : undefined;
              return (
                <Message
                  key={b.key}
                  type={b.side}
                  first={first}
                  last={last}
                  tail={last && !b.link}
                  footer={footer}
                  className={
                    b.link ? "message-link" : b.voice ? "message-voice" : b.contact ? "message-contact" : undefined
                  }
                >
                  {b.key === b.messageId && message.replyTo && (
                    <span slot="header" className={`reply-quote reply-quote-${message.replyTo.side}`}>
                      <span className="reply-quote-bubble">{quoteOf(message.replyTo)}</span>
                    </span>
                  )}
                  {b.key === b.messageId && message.reactions && (
                    <ReactionBadges slot="content-start" reactions={message.reactions} side={b.side} />
                  )}
                  <PressableText slot="text" onPress={press(message)}>
                    {b.contact ? (
                      <ContactCardBubble
                        name={b.contact.name}
                        saved={contactOffer !== b.contact.name}
                        onSave={onSaveContact}
                      />
                    ) : b.voice ? (
                      <VoiceNoteBubble note={b.voice} />
                    ) : b.link ? (
                      <LinkPreview url={b.link} />
                    ) : (
                      <LinkedText text={b.text} />
                    )}
                  </PressableText>
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
      {contactOffer && onSaveContact && <ContactBanner name={contactOffer} onUpdate={onSaveContact} />}
      {pressed && rootRef.current && (
        <TapbackMenu
          target={pressed.target}
          root={rootRef.current}
          side={pressed.message.side}
          mine={pressed.message.reactions?.findLast((r) => r.by === "user")?.emoji ?? null}
          onReact={(emoji) => {
            onReact?.(pressed.message.id, emoji);
            setPressed(null);
          }}
          onReply={
            onSend
              ? () => {
                  setReplyingTo(pressed.message);
                  setPressed(null);
                  rootRef.current?.querySelector("textarea")?.focus();
                }
              : undefined
          }
          onCopy={() => {
            void navigator.clipboard?.writeText(pressed.message.text);
            setPressed(null);
          }}
          onClose={() => setPressed(null)}
        />
      )}
      <div className="home-indicator" />
    </div>
  );
}

/**
 * A bubble's content, with press-and-hold (or right-click, double-click) wired to open tapbacks.
 * `slot` is taken as a prop because Framework7 reads it from the element at the call site.
 */
function PressableText({
  slot,
  onPress,
  children,
}: {
  slot: string;
  onPress: (el: HTMLElement) => void;
  children: React.ReactNode;
}) {
  return (
    <span slot={slot} className="pressable" {...useLongPress(onPress)}>
      {children}
    </span>
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
  voice?: VoiceNote;
  contact?: { name: string };
}

/** As in Messages: a lone link becomes a card; text with a link shows the text, then the card. */
function bubblesFor(messages: ThreadMessage[]): Bubble[] {
  return messages.flatMap((m): Bubble[] => {
    const base = { messageId: m.id, side: m.side, text: m.text };
    if (m.voice) return [{ ...base, key: m.id, voice: m.voice }];
    if (m.contact) return [{ ...base, key: m.id, contact: m.contact }];
    const link = firstLink(m.text);
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
          <a key={i} className="message-inline-link external" href={part.href} target="_blank" rel="noreferrer">
            {part.text}
          </a>
        ) : (
          part.text
        ),
      )}
    </>
  );
}

/** The status bar clock, h:mm like iOS, refreshed on the minute. */
function useClock(): string {
  const format = () =>
    new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }).replace(/\s?[AP]M$/i, "");
  const [time, setTime] = useState(format);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      setTime(format());
      timer = setTimeout(tick, 60_000 - (Date.now() % 60_000));
    };
    timer = setTimeout(tick, 60_000 - (Date.now() % 60_000));
    return () => clearTimeout(timer);
  }, []);
  return time;
}

/** "Today 4:51 PM", "Yesterday 9:02 AM", or "Mon, Sep 22 at 9:02 AM", as Messages labels a thread. */
function threadDate(iso?: string): string {
  const date = iso ? new Date(iso) : new Date();
  const time = date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const days = Math.round((startOfDay(new Date()) - startOfDay(date)) / 86_400_000);
  if (days === 0) return `Today ${time}`;
  if (days === 1) return `Yesterday ${time}`;
  const day = date.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  return `${day} at ${time}`;
}

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/** Elapsed recording time, m:ss, ticking while recording. */
function RecordingClock({ since }: { since: number | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, []);
  // `since` arrives once the mic is live, which can be just after this clock last ticked.
  const s = since === null ? 0 : Math.max(0, Math.floor((now - since) / 1000));
  return <span className="recording-time">{`${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`}</span>;
}

/** A message as quoted in a reply: its text, or what kind of message it was. */
function quoteOf(m: Pick<ThreadMessage, "text"> & { voice?: unknown }): string {
  return m.voice ? "Audio Message" : m.text;
}
