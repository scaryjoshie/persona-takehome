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
