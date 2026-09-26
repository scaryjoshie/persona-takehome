// Wire types are generated from the backend's schema (src/protocol.gen.ts, `pnpm gen-types`).
// Only what the schema does not cover lives here.
export type * from "./protocol.gen";
// The backend's model is named `Partial`, which would shadow TypeScript's built-in utility type.
export type { Partial as PartialMessage } from "./protocol.gen";

/** Why the browser could not bring call audio up; sent with the `failed` call action. */
export type CallFailReason = "mic_denied" | "audio_socket";
