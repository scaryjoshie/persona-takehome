import type { ClientMessage, ServerMessage, Snapshot } from "../types";
import { Emitter, type AudioLink, type ConnectionStatus, type Transport } from "./types";

function wsUrl(path: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${path}`;
}

export class WsTransport implements Transport {
  private ws: WebSocket | null = null;
  private phone = "";
  private closed = false;
  private retries = 0;
  private messages = new Emitter<ServerMessage>();
  private status = new Emitter<ConnectionStatus>();

  async connect(phone: string): Promise<Snapshot> {
    this.phone = phone;
    const res = await fetch("/api/session", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ phone }),
    });
    if (!res.ok) throw new Error(`session failed: ${res.status}`);
    const snapshot = (await res.json()) as Snapshot;
    this.open();
    return snapshot;
  }

  private open(): void {
    if (this.closed) return;
    this.status.emit("connecting");
    const ws = new WebSocket(wsUrl(`/ws?phone=${encodeURIComponent(this.phone)}`));
    this.ws = ws;
    ws.onopen = () => {
      this.retries = 0;
      this.status.emit("open");
    };
    ws.onmessage = (e) => {
      try {
        this.messages.emit(JSON.parse(e.data as string) as ServerMessage);
      } catch (err) {
        console.warn("bad server message", err);
      }
    };
    ws.onclose = () => {
      this.status.emit("closed");
      if (this.closed) return;
      const delay = Math.min(8000, 500 * 2 ** this.retries++);
      setTimeout(() => this.open(), delay);
    };
  }

  send(msg: ClientMessage): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
    else console.warn("socket not open, dropped", msg);
  }

  onMessage(cb: (msg: ServerMessage) => void): () => void {
    return this.messages.on(cb);
  }

  onStatus(cb: (s: ConnectionStatus) => void): () => void {
    return this.status.on(cb);
  }

  openAudio(): AudioLink {
    return new WsAudioLink(wsUrl(`/ws/audio?phone=${encodeURIComponent(this.phone)}`));
  }

  async uploadVoiceNote(audio: Blob, durationMs: number): Promise<string> {
    const form = new FormData();
    form.append("audio", audio, audio.type.includes("mp4") ? "voice.m4a" : "voice.webm");
    form.append("duration_ms", String(Math.round(durationMs)));
    const res = await fetch(`/api/voice-note?phone=${encodeURIComponent(this.phone)}`, { method: "POST", body: form });
    if (!res.ok) throw new Error(`voice note upload failed: ${res.status}`);
    return ((await res.json()) as { audio_id: string }).audio_id;
  }

  draftImageUrl(ref: string, version: number): string {
    return `/api/drafts/${encodeURIComponent(this.phone)}/${encodeURIComponent(ref)}.svg?v=${version}`;
  }

  close(): void {
    this.closed = true;
    this.ws?.close();
  }
}

/** Close code the server sends when another tab already holds the audio socket. */
export const AUDIO_BUSY_CODE = 4409;

class WsAudioLink implements AudioLink {
  readonly ready: Promise<void>;
  private ws: WebSocket;
  private frames = new Emitter<ArrayBuffer>();
  private closes = new Emitter<number>();

  constructor(url: string) {
    this.ws = new WebSocket(url);
    this.ws.binaryType = "arraybuffer";
    this.ready = new Promise((resolve, reject) => {
      this.ws.onopen = () => resolve();
      this.ws.onerror = () => reject(new Error("audio socket error"));
      this.ws.onclose = (e) => {
        reject(new Error(`audio socket closed: ${e.code}`));
        this.closes.emit(e.code);
      };
    });
    this.ready.catch(() => undefined);
    this.ws.onmessage = (e) => {
      if (e.data instanceof ArrayBuffer) this.frames.emit(e.data);
    };
  }

  send(frame: ArrayBuffer): void {
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(frame);
  }
  onFrame(cb: (f: ArrayBuffer) => void): () => void {
    return this.frames.on(cb);
  }
  onClose(cb: (code: number) => void): () => void {
    return this.closes.on(cb);
  }
  close(): void {
    this.ws.close();
  }
}
