import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CallState } from "../types";
import type { Transport } from "../transport/types";
import { AUDIO_BUSY_CODE } from "../transport/ws";
import type { SessionActions } from "../state/session";
import { MicDeniedError, startAudioCall, type AudioCall } from "./call";

/** How long to wait for the server to confirm a call is connecting before giving up. */
const CONNECTING_TIMEOUT_MS = 5000;

/**
 * Owns the audio side of a call. Intent goes out first (accept or start); once the server says the
 * call is connecting, the mic and the audio socket come up. The server refuses an audio socket
 * (close 4400) when no call is connecting, so it must not race ahead of the intent. Failures are
 * reported back with a reason so the floor stays with text.
 */
export function useCallAudio(transport: Transport, call: CallState, actions: SessionActions) {
  const audioRef = useRef<AudioCall | null>(null);
  const pending = useRef(false);
  const connecting = useConnectingSignal(call.phase);
  const [muted, setMutedState] = useState(false);
  const [busyTab, setBusyTab] = useState(false);

  const begin = useCallback(
    async (action: "accept" | "start") => {
      if (audioRef.current || pending.current) return;
      pending.current = true;
      setBusyTab(false);
      actions.call(action);
      if (!(await connecting.wait(CONNECTING_TIMEOUT_MS))) {
        pending.current = false;
        return actions.call("failed", "audio_socket");
      }
      const link = transport.openAudio();
      link.onClose((code) => {
        if (code === AUDIO_BUSY_CODE) setBusyTab(true);
        if (audioRef.current) {
          audioRef.current.stop();
          audioRef.current = null;
        }
      });
      try {
        audioRef.current = await startAudioCall(link);
      } catch (e) {
        actions.call("failed", e instanceof MicDeniedError ? "mic_denied" : "audio_socket");
      } finally {
        pending.current = false;
      }
    },
    [transport, actions, connecting],
  );

  const hangup = useCallback(() => {
    actions.call("hangup");
    audioRef.current?.stop();
    audioRef.current = null;
  }, [actions]);

  useEffect(() => {
    if (call.phase === "none" || call.phase === "ended") {
      audioRef.current?.stop();
      audioRef.current = null;
      setMutedState(false);
    }
  }, [call.phase]);

  useEffect(() => () => audioRef.current?.stop(), []);

  const setMuted = useCallback((m: boolean) => {
    setMutedState(m);
    audioRef.current?.setMuted(m);
  }, []);

  const getOutputLevel = useCallback(() => audioRef.current?.outputLevel.current ?? 0, []);

  return {
    accept: () => void begin("accept"),
    start: () => void begin("start"),
    decline: () => actions.call("decline"),
    hangup,
    muted,
    setMuted,
    busyTab,
    getOutputLevel,
  };
}

/** Resolves waiters when the call reaches `connecting` (or is already past it). */
function useConnectingSignal(phase: CallState["phase"]) {
  const waiters = useRef(new Set<(ok: boolean) => void>());
  const current = useRef(phase);
  current.current = phase;

  useEffect(() => {
    if (phase !== "connecting" && phase !== "connected") return;
    for (const resolve of waiters.current) resolve(true);
    waiters.current.clear();
  }, [phase]);

  return useMemo(
    () => ({
      wait(timeoutMs: number): Promise<boolean> {
        if (current.current === "connecting" || current.current === "connected") return Promise.resolve(true);
        return new Promise((resolve) => {
          const done = (ok: boolean) => {
            clearTimeout(timer);
            waiters.current.delete(done);
            resolve(ok);
          };
          const timer = setTimeout(() => done(false), timeoutMs);
          waiters.current.add(done);
        });
      },
    }),
    [],
  );
}
