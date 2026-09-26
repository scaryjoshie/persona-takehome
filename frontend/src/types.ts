// Wire types are generated from the backend's schema (src/protocol.gen.ts, `pnpm gen-types`).
// Only what the schema does not cover yet lives here.
import type { ServerMessage } from "./protocol.gen";

export type * from "./protocol.gen";

/** A live transcript fragment, cumulative for its turn. The backend adds it with the voice layer. */
export interface PartialMessage {
  type: "partial";
  speaker: "user" | "agent";
  turn_id: string;
  text: string;
  final: boolean;
}

/** Everything the browser socket can receive. */
export type IncomingMessage = ServerMessage | PartialMessage;

/** Why the browser could not bring call audio up; sent with the `failed` call action. */
export type CallFailReason = "mic_denied" | "audio_socket";
