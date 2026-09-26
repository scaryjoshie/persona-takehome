import type { AudioLink } from "../transport/types";

export class MicDeniedError extends Error {
  constructor() {
    super("microphone permission denied");
  }
}
export class AudioSocketError extends Error {
  constructor(public code: number | null = null) {
    super(`audio socket failed${code ? ` (${code})` : ""}`);
  }
}

export interface Level {
  current: number;
}

export interface AudioCall {
  /** 0..1, smoothed enough for a visualizer, measured on what the mic sends. */
  inputLevel: Level;
  /** 0..1, measured at playback time. */
  outputLevel: Level;
  setMuted(muted: boolean): void;
  stop(): void;
}

const RATE = 24000;

function rmsOf(buf: ArrayBuffer): number {
  const a = new Int16Array(buf);
  let acc = 0;
  for (let i = 0; i < a.length; i++) {
    const v = a[i] / 32768;
    acc += v * v;
  }
  return Math.sqrt(acc / a.length);
}

/** Speech RMS sits around 0.02..0.2; stretch that onto 0..1 for the orb. */
function toLevel(rms: number): number {
  return Math.min(1, Math.max(0, (rms - 0.005) * 5));
}

/**
 * Opens the mic and the audio link and wires them together through two worklets.
 * Order matters for the contract: the caller has already sent the call intent;
 * a mic refusal or a dead socket is reported back as a typed error.
 */
export async function startAudioCall(link: AudioLink): Promise<AudioCall> {
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch {
    link.close();
    throw new MicDeniedError();
  }

  const ctx = new AudioContext({ sampleRate: RATE });
  const cleanupMedia = () => {
    stream.getTracks().forEach((t) => t.stop());
    void ctx.close();
  };

  try {
    await ctx.resume();
    await Promise.all([ctx.audioWorklet.addModule("/worklets/capture.js"), ctx.audioWorklet.addModule("/worklets/playback.js")]);
  } catch (e) {
    cleanupMedia();
    link.close();
    throw e;
  }

  const inputLevel: Level = { current: 0 };
  const outputLevel: Level = { current: 0 };
  let muted = false;
  let stopped = false;

  const source = ctx.createMediaStreamSource(stream);
  const capture = new AudioWorkletNode(ctx, "pcm-capture", {
    numberOfInputs: 1,
    numberOfOutputs: 1,
    processorOptions: { targetRate: RATE },
  });
  const sink = ctx.createGain();
  sink.gain.value = 0;
  source.connect(capture);
  capture.connect(sink);
  sink.connect(ctx.destination);

  const playback = new AudioWorkletNode(ctx, "pcm-playback", {
    numberOfInputs: 0,
    numberOfOutputs: 1,
    outputChannelCount: [1],
    processorOptions: { sourceRate: RATE, prebufferMs: 100 },
  });
  playback.connect(ctx.destination);

  capture.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
    if (stopped) return;
    const buf = e.data;
    if (muted) {
      inputLevel.current = 0;
      link.send(new ArrayBuffer(buf.byteLength)); // zeros keep the model's context channel open
      return;
    }
    inputLevel.current = toLevel(rmsOf(buf));
    link.send(buf);
  };
  playback.port.onmessage = (e: MessageEvent<{ level?: number }>) => {
    if (typeof e.data?.level === "number") outputLevel.current = toLevel(e.data.level);
  };
  const offFrame = link.onFrame((f) => playback.port.postMessage(f, [f]));

  try {
    await link.ready;
  } catch {
    offFrame();
    cleanupMedia();
    throw new AudioSocketError();
  }

  const stop = () => {
    if (stopped) return;
    stopped = true;
    offFrame();
    cleanupMedia();
    link.close();
  };

  return {
    inputLevel,
    outputLevel,
    setMuted: (m) => {
      muted = m;
    },
    stop,
  };
}
