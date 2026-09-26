// Wire types are generated from the backend's schema (src/protocol.gen.ts, `pnpm gen-types`).
// Only what the schema does not cover lives here.
export type * from "./protocol.gen";

/** Why the browser could not bring call audio up; sent with the `failed` call action. */
export type CallFailReason = "mic_denied" | "audio_socket";
