// Interface sound effects. Files live in public/sounds/.
const SOUNDS = {
  callJoin: "/sounds/call-join.mp3",
  callLeave: "/sounds/call-leave.mp3",
} as const;

export type Sound = keyof typeof SOUNDS;

const cache = new Map<Sound, HTMLAudioElement>();

/** Plays a sound from the start. Autoplay rules can block it before the first click; that is fine. */
export function play(sound: Sound, volume = 0.6) {
  let audio = cache.get(sound);
  if (!audio) {
    audio = new Audio(SOUNDS[sound]);
    audio.preload = "auto";
    cache.set(sound, audio);
  }
  audio.volume = volume;
  audio.currentTime = 0;
  void audio.play().catch(() => undefined);
}
