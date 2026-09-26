import { useCallback, useEffect, useRef, useState } from "react";
import type { CallState } from "../types";
import type { Transport } from "../transport/types";
import { AUDIO_BUSY_CODE } from "../transport/ws";
import type { SessionActions } from "../state/session";
import { MicDeniedError, startAudioCall, type AudioCall } from "./call";

/**
 * Owns the audio side of a call. Intent goes out first (accept or start), then the mic
 * and the audio socket; failures are reported back with a reason so the floor stays with text.
 */
export function useCallAudio(transport: Transport, call: CallState, actions: SessionActions) {
  const audioRef = useRef<AudioCall | null>(null);
  const pending = useRef(false);
  const [muted, setMutedState] = useState(false);
  const [busyTab, setBusyTab] = useState(false);

  const begin = useCallback(
    async (action: "accept" | "start") => {
      if (audioRef.current || pending.current) return;
      pending.current = true;
      setBusyTab(false);
      actions.call(action);
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
    [transport, actions],
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

  const getInputLevel = useCallback(() => audioRef.current?.inputLevel.current ?? 0, []);
  const getOutputLevel = useCallback(() => audioRef.current?.outputLevel.current ?? 0, []);

  return {
    accept: () => void begin("accept"),
    start: () => void begin("start"),
    decline: () => actions.call("decline"),
    hangup,
    muted,
    setMuted,
    busyTab,
    getInputLevel,
    getOutputLevel,
  };
}
