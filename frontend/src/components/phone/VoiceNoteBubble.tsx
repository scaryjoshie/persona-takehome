import { useEffect, useRef, useState } from "react";
import { Icon } from "framework7-react";
import { Waveform } from "../ui/waveform";
import { audioPeaks } from "../../audio/peaks";

export interface VoiceNote {
  src: string;
  durationMs: number;
  /** Shown under the waveform, as Messages does since iOS 17. `null` while transcribing, absent if none. */
  transcript?: string | null;
}

const BARS = 36;

function clock(ms: number): string {
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** A Messages audio message: play/pause, the clip's waveform filling as it plays, the time, the transcript. */
export function VoiceNoteBubble({ note }: { note: VoiceNote }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [peaks, setPeaks] = useState<number[]>([]);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);

  useEffect(() => {
    let live = true;
    void audioPeaks(note.src, BARS).then((p) => live && setPeaks(p));
    return () => {
      live = false;
    };
  }, [note.src]);

  const toggle = () => {
    const a = audio.current;
    if (!a) return;
    if (a.paused) void a.play();
    else a.pause();
  };
  const progress = note.durationMs ? Math.min(1, (position * 1000) / note.durationMs) : 0;
  const remaining = playing || position > 0 ? note.durationMs - position * 1000 : note.durationMs;

  return (
    <div className="voice-note">
      <div className="voice-note-row">
        <button type="button" className="voice-note-play" onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
          <Icon f7={playing ? "pause_fill" : "play_fill"} />
        </button>
        {/* Two copies of the waveform: dim underneath, bright on top, clipped to what has played. */}
        <div className="voice-note-wave">
          <Waveform
            data={peaks}
            height={26}
            barWidth={2.5}
            barGap={2}
            barRadius={1.25}
            barColor="rgba(255,255,255,0.6)"
            fadeEdges={false}
          />
          <Waveform
            data={peaks}
            height={26}
            barWidth={2.5}
            barGap={2}
            barRadius={1.25}
            barColor="#fff"
            fadeEdges={false}
            className="voice-note-played"
            style={{ clipPath: `inset(0 ${100 - progress * 100}% 0 0)` }}
          />
        </div>
        <span className="voice-note-time">{clock(Math.max(0, remaining))}</span>
      </div>
      {note.transcript !== undefined && <p className="voice-note-transcript">{note.transcript ?? "Transcribing…"}</p>}
      <audio
        ref={audio}
        src={note.src}
        preload="none"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => {
          setPlaying(false);
          setPosition(0);
        }}
        onTimeUpdate={(e) => setPosition(e.currentTarget.currentTime)}
      />
    </div>
  );
}
