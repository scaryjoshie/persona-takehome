import { useCallback, useRef, useState } from "react";

export interface Recording {
  blob: Blob;
  durationMs: number;
}

// Chrome and Firefox record webm/opus; Safari only mp4/aac.
const FORMATS = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"];

/**
 * Records a voice note from a stream the caller already opened (the live waveform's). The returned
 * functions are stable: the waveform reopens the mic whenever its callbacks change identity.
 */
export function useVoiceRecorder() {
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const started = useRef<number | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);

  const start = useCallback((stream: MediaStream) => {
    if (recorder.current) return;
    const mimeType = FORMATS.find((f) => MediaRecorder.isTypeSupported(f));
    const r = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    chunks.current = [];
    r.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
    r.start();
    recorder.current = r;
    started.current = Date.now();
    setStartedAt(started.current);
  }, []);

  const reset = () => {
    recorder.current = null;
    started.current = null;
    setStartedAt(null);
  };

  /** Stops and hands back the clip. Duration is measured here: webm from MediaRecorder carries none. */
  const stop = useCallback(
    (): Promise<Recording | null> =>
      new Promise((resolve) => {
        const r = recorder.current;
        const since = started.current;
        if (!r || r.state === "inactive" || since === null) return resolve(null);
        const durationMs = Date.now() - since;
        r.onstop = () => resolve({ blob: new Blob(chunks.current, { type: r.mimeType }), durationMs });
        r.stop();
        reset();
      }),
    [],
  );

  const cancel = useCallback(() => {
    const r = recorder.current;
    if (r && r.state !== "inactive") {
      r.onstop = null;
      r.stop();
    }
    reset();
  }, []);

  return { startedAt, start, stop, cancel };
}
