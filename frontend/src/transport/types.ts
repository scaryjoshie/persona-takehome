import type { ClientMessage, ContactMessage, ReactMessage, ReplyMessage, ServerMessage, Snapshot } from "../types";

export type ConnectionStatus = "connecting" | "open" | "closed";

/** The audio socket. Binary PCM16 mono 24 kHz frames in both directions. Its close is the hang-up. */
export interface AudioLink {
  /** Resolves when the socket is open; rejects if it cannot open. */
  ready: Promise<void>;
  send(frame: ArrayBuffer): void;
  onFrame(cb: (frame: ArrayBuffer) => void): () => void;
  onClose(cb: (code: number) => void): () => void;
  close(): void;
}

/** Everything the UI can do to the backend. */
export interface Transport {
  /** Creates or resumes the user for `phone`, opens the socket, and returns the thread so far. */
  connect(phone: string): Promise<Snapshot>;
  send(msg: ClientMessage | ReactMessage | ReplyMessage | ContactMessage): void;
  onMessage(cb: (msg: ServerMessage) => void): () => void;
  onStatus(cb: (status: ConnectionStatus) => void): () => void;
  openAudio(): AudioLink;
  /** Uploads a recorded audio message; resolves with its id. Transcription follows as an event. */
  uploadVoiceNote(audio: Blob): Promise<string>;
  close(): void;
}

export class Emitter<T> {
  private listeners = new Set<(v: T) => void>();
  on(cb: (v: T) => void): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }
  emit(v: T): void {
    for (const cb of this.listeners) cb(v);
  }
}
