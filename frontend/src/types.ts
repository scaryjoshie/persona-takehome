// Wire types are generated from the backend's schema (src/protocol.gen.ts, `pnpm gen-types`).
// Only what the schema does not cover lives here.
export type * from "./protocol.gen";

/** Why the browser could not bring call audio up; sent with the `failed` call action. */
export type CallFailReason = "mic_denied" | "audio_socket";

/**
 * An audio message the user sent, once transcribed. The backend has announced it but not shipped it;
 * this goes away when the generated schema includes it.
 */
export interface VoiceNotePayload {
  kind: "voice_note";
  audio_id: string;
  duration_ms: number;
  transcript: string | null;
}

/** A tapback on a message. Announced by the backend, not in the schema yet. */
export interface ReactionPayload {
  kind: "reaction";
  target_seq: number;
  emoji: string;
  by: "user" | "agent";
  removed: boolean;
}

/** The user's tapback, sent on /ws. Announced by the backend, not in the schema yet. */
export interface ReactMessage {
  type: "react";
  target_seq: number;
  emoji: string;
  remove?: boolean;
}

/** A text sent as a reply. `reply_to` is announced by the backend, not in the schema yet. */
export interface ReplyMessage {
  type: "message";
  text: string;
  reply_to: number;
}

/** The agent's contact card, sent when its name is set or changed. Not in the schema yet. */
export interface ContactCardPayload {
  kind: "contact_card";
  name: string;
}

/** The user saved the agent's contact under `name`. Not in the schema yet. */
export interface ContactSavedPayload {
  kind: "contact_saved";
  name: string;
}

/** The user tapped Update on the contact banner or card. Not in the schema yet. */
export interface ContactMessage {
  type: "contact";
  action: "save";
}
