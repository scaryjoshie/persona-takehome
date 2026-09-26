import type { ClientMessage, ServerMessage, Snapshot } from "../types";

export type ConnectionStatus = "connecting" | "open" | "closed";

/** The audio socket. Binary PCM16 mono 24 kHz frames in both directions. Its close is the hang-up. */
export interface AudioLink {
  /** Resolves when the socket is open; rejects if it cannot open. */
  ready: Promise<void>;
  send(frame: ArrayBuffer): void;
  onFrame(cb: (frame: ArrayBuffer) => void): () => void;
  onClose(cb: (code: number) => void): () => void;
  close(): void;
  /** Demo links that carry no audio can still tell the UI how loud the agent "is". */
  levelHint?: () => number;
}

/** Everything the UI can do to the backend. One real implementation, one scripted one for demos. */
export interface Transport {
  readonly kind: "ws" | "scripted";
  connect(phone: string): Promise<Snapshot>;
  send(msg: ClientMessage): void;
  onMessage(cb: (msg: ServerMessage) => void): () => void;
  onStatus(cb: (status: ConnectionStatus) => void): () => void;
  openAudio(): AudioLink;
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
