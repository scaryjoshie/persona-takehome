import { useRef, useState } from "react";

export interface Recording {
  blob: Blob;
  durationMs: number;
}

// Chrome and Firefox record webm/opus; Safari only mp4/aac.
const FORMATS = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"];

/** Records a voice note from a stream the caller already opened (the live waveform's). */
export function useVoiceRecorder() {
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const [startedAt, setStartedAt] = useState<number | null>(null);

  const start = (stream: MediaStream) => {
    const mimeType = FORMATS.find((f) => MediaRecorder.isTypeSupported(f));
    const r = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    chunks.current = [];
    r.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
    r.start();
    recorder.current = r;
    setStartedAt(Date.now());
  };

  /** Stops and hands back the clip. Duration is measured here: webm from MediaRecorder carries none. */
  const stop = (): Promise<Recording | null> =>
    new Promise((resolve) => {
      const r = recorder.current;
      if (!r || r.state === "inactive" || startedAt === null) return resolve(null);
      const durationMs = Date.now() - startedAt;
      r.onstop = () => resolve({ blob: new Blob(chunks.current, { type: r.mimeType }), durationMs });
      r.stop();
      recorder.current = null;
      setStartedAt(null);
    });

  const cancel = () => {
    const r = recorder.current;
    if (r && r.state !== "inactive") {
      r.onstop = null;
      r.stop();
    }
    recorder.current = null;
    setStartedAt(null);
  };

  return { startedAt, start, stop, cancel };
}
