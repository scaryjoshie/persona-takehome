import type { TranscriptLine } from "../components/orb/Transcript";

export const AGENT_NAME = "Juno";

export const INTRO = [
  "hey! I'm your new assistant from Persona. think of me as someone who takes things off your plate.",
  "first things first: what do you want to call me?",
];

export const RANDOM_AGENT_LINES = [
  "ok, calling you now.",
  "looks like we got cut off. want to keep going here?",
  "what's been eating your time lately?",
  "got it. I'll take that off your plate.",
  "one more thing: connect your gmail and I can actually start doing things for you.",
  "you have an unanswered email from your professor about friday's deadline. want me to draft a reply?",
  "nice to meet you, Sam.",
  "no rush. the link's above whenever you're ready.",
];

export const CALL_SCRIPT: Array<Pick<TranscriptLine, "speaker" | "text">> = [
  { speaker: "agent", text: "hey, it's Juno. so, what should I call you?" },
  { speaker: "user", text: "you can call me Sam." },
  { speaker: "agent", text: "nice to meet you, Sam. what's been eating your time lately?" },
  { speaker: "user", text: "honestly, keeping up with school emails." },
  { speaker: "agent", text: "got it. I just texted you a link to connect your gmail. tap it whenever, I'll wait." },
];

export const RANDOM_USER_LINES = [
  "sounds good",
  "yes please",
  "hold on",
  "can you check my calendar too?",
  "thanks!",
  "skip that for now",
];

let n = 100;
export function nextId(): string {
  return String(++n);
}
export function pick<T>(xs: readonly T[]): T {
  return xs[Math.floor(Math.random() * xs.length)];
}

/** A speech-shaped level: syllables at ~4 Hz inside a slower phrase envelope. */
export function speechLevel(t: number): number {
  const syllables = Math.abs(Math.sin(2 * Math.PI * 3.8 * t));
  const phrase = 0.65 + 0.35 * Math.sin(2 * Math.PI * 0.7 * t);
  return 0.25 + 0.65 * syllables * phrase;
}

export const DRAFT_BODY = `Hi Professor,

I wanted to check on the deadline for Friday's assignment. Is it due at the start of class or by midnight?

Thanks,
Sam`;
