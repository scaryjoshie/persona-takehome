// A fake backend that lives in the browser. It follows the protocol in docs 09/14 closely
// enough to exercise every UI state: text onboarding, an agent-initiated call, live
// transcript partials, synthetic call audio, Gmail link and connect, hang-ups, graduation.
// It is a demo fixture, not an agent: replies are scripted per missing slot.

import type { CallState, ClientMessage, Event, Medium, Origin, Channel, Payload, ServerMessage, Slots, Snapshot } from "../types";
import { EMPTY_CALL, EMPTY_SLOTS, missingSlots } from "../types";
import { Emitter, type AudioLink, type ConnectionStatus, type Transport } from "./types";


class Cancelled extends Error {}

export class ScriptedTransport implements Transport {
  readonly kind = "scripted" as const;
  private messages = new Emitter<ServerMessage>();
  private status = new Emitter<ConnectionStatus>();
  private backend = new FakeBackend((m) => this.messages.emit(m));

  async connect(phone: string): Promise<Snapshot> {
    this.backend.phone = phone;
    this.backend.load(phone);
    setTimeout(() => this.status.emit("open"), 50);
    return this.backend.snapshot();
  }
  send(msg: ClientMessage): void {
    this.backend.handle(msg);
  }
  onMessage(cb: (m: ServerMessage) => void): () => void {
    return this.messages.on(cb);
  }
  onStatus(cb: (s: ConnectionStatus) => void): () => void {
    return this.status.on(cb);
  }
  openAudio(): AudioLink {
    return this.backend.openAudio();
  }
  close(): void {
    this.backend.dispose();
  }
}

// Chrome loads voices lazily; touching the list early makes them available by the first call.
if (typeof speechSynthesis !== "undefined") speechSynthesis.getVoices();

class ScriptedAudioLink implements AudioLink {
  readonly ready: Promise<void>;
  private frames = new Emitter<ArrayBuffer>();
  private closes = new Emitter<number>();
  private closed = false;
  constructor(private backend: FakeBackend, busy: boolean) {
    this.ready = busy ? Promise.reject(new Error("busy")) : new Promise((r) => setTimeout(r, 120));
    this.ready.catch(() => undefined);
    if (busy) setTimeout(() => this.closes.emit(4409), 130);
  }
  send(): void {
    /* mic audio is discarded; the mock cannot listen */
  }
  onFrame(cb: (f: ArrayBuffer) => void): () => void {
    return this.frames.on(cb);
  }
  onClose(cb: (c: number) => void): () => void {
    return this.closes.on(cb);
  }
  levelHint = (): number => this.backend.speakingLevel();
  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.closes.emit(1000);
    this.backend.audioClosed(this);
  }
}

class FakeBackend {
  phone = "";
  private events: Event[] = [];
  private slots: Slots = { ...EMPTY_SLOTS };
  private call: CallState = { ...EMPTY_CALL };
  private floor: Medium = "text";
  private seq = 0;
  private audio: ScriptedAudioLink | null = null;
  private timers = new Set<ReturnType<typeof setTimeout>>();
  private callGen = 0; // bumps on every call end; in-flight voice scripts check it
  private textGen = 0; // bumps on every user message; in-flight text replies check it
  private textBusy = false;
  private saidTypingHint = false;
  private speaking = false;
  private ringTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private emit: (m: ServerMessage) => void) {}

  // ---- persistence (localStorage, per phone) -------------------------------------
  private key() {
    return `persona-mock:${this.phone}`;
  }
  load(phone: string) {
    this.phone = phone;
    try {
      const raw = localStorage.getItem(this.key());
      if (raw) {
        const s = JSON.parse(raw) as { events: Event[]; slots: Slots; seq: number };
        this.events = s.events;
        this.slots = s.slots;
        this.seq = s.seq;
        // a call that was live when the page died ends as server_restart
        this.call = { ...EMPTY_CALL };
        this.floor = "text";
      }
    } catch {
      /* ignore */
    }
  }
  private save() {
    try {
      localStorage.setItem(this.key(), JSON.stringify({ events: this.events, slots: this.slots, seq: this.seq }));
    } catch {
      /* ignore */
    }
  }
  snapshot(): Snapshot {
    // Copies: the UI keys memoization on array identity, and this array keeps growing.
    return { events: [...this.events], slots: { ...this.slots }, call: { ...this.call }, floor: this.floor };
  }

  // ---- primitives -----------------------------------------------------------------
  private append(origin: Origin, channel: Channel, payload: Payload): Event {
    const ev: Event = { seq: ++this.seq, ts: new Date().toISOString(), origin, channel, payload };
    this.events.push(ev);
    this.emit({ type: "event", event: ev });
    this.save();
    return ev;
  }
  private setSlot<K extends keyof Slots>(slot: K, value: Slots[K], via: Medium) {
    const old = this.slots[slot];
    this.slots = { ...this.slots, [slot]: value };
    const origin: Origin = via === "voice" ? "voice_agent" : "text_agent";
    this.append(origin, via, { kind: "tool_call", name: `set_${slot}`, args: { value }, result: { ok: true } });
    this.append("system", "system", { kind: "slot_changed", slot, old, new: value });
    this.emit({ type: "slots", slots: this.slots });
  }
  private setCall(patch: Partial<CallState>) {
    this.call = { ...this.call, ...patch };
    this.emit({ type: "call", call: this.call });
  }
  private decision(trigger_kind: string, verb: "interrupt" | "absorb" | "defer" | "start", note?: string) {
    this.append("system", "system", {
      kind: "decision",
      trigger_kind,
      verb,
      by: "fixed",
      confidence: 1,
      ms: Math.round(1 + Math.random() * 3),
      note: note ?? null,
    });
  }
  private sleep(ms: number, gen?: { get: () => number; at: number }): Promise<void> {
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => {
        this.timers.delete(t);
        if (gen && gen.get() !== gen.at) reject(new Cancelled());
        else resolve();
      }, ms);
      this.timers.add(t);
    });
  }
  dispose() {
    for (const t of this.timers) clearTimeout(t);
    this.timers.clear();
    this.audio?.close();
    this.stopSpeaking();
  }

  private stopSpeaking() {
    this.speaking = false;
    if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
  }

  /** A speech-shaped envelope while the agent talks, for the orb and the island meter. */
  speakingLevel(): number {
    if (!this.speaking) return 0;
    const t = performance.now() / 1000;
    const syllables = Math.abs(Math.sin(2 * Math.PI * 3.6 * t));
    const phrase = 0.7 + 0.3 * Math.sin(2 * Math.PI * 0.8 * t + 1);
    return 0.35 + 0.5 * syllables * phrase;
  }

  // ---- inbound -----------------------------------------------------------------------
  handle(msg: ClientMessage) {
    switch (msg.type) {
      case "message":
        return this.onUserMessage(msg.text);
      case "typing":
        return this.onTyping(msg.active);
      case "call":
        return this.onCallAction(msg.action, msg.reason);
      case "reset":
        return this.reset();
    }
  }

  private reset() {
    this.dispose();
    this.events = [];
    this.slots = { ...EMPTY_SLOTS };
    this.call = { ...EMPTY_CALL };
    this.floor = "text";
    this.seq = 0;
    this.textBusy = false;
    this.saidTypingHint = false;
    this.callGen++;
    this.textGen++;
    this.save();
    this.emit({ type: "snapshot", ...this.snapshot() });
  }

  private onTyping(active: boolean) {
    if (!active) return;
    if (this.floor === "voice" && this.call.phase === "connected") {
      this.decision("typing", "absorb", "typing during a voice run; agent finishes its sentence");
      if (!this.saidTypingHint) {
        this.saidTypingHint = true;
        void this.voiceLine("looks like you're typing, go ahead, I can read it.", this.callGen).catch(() => undefined);
      }
    }
  }

  private onUserMessage(text: string) {
    this.textGen++;
    const gen = this.textGen;
    this.append("user", "text", { kind: "user_message", text });
    if (this.floor === "voice" && this.call.phase === "connected") {
      this.decision("user_message", "interrupt", "text arrived during a call; injected into the live session");
      void this.voiceLine(`got your text: ${shorten(text)}. noted.`, this.callGen).catch(() => undefined);
      return;
    }
    this.decision("user_message", this.textBusy ? "interrupt" : "start");
    void this.textTurn(text, gen).catch((e) => {
      if (!(e instanceof Cancelled)) console.error(e);
    });
  }

  // ---- text agent -----------------------------------------------------------------
  private async bubbles(lines: string[], gen: number) {
    const g = { get: () => this.textGen, at: gen };
    this.textBusy = true;
    try {
      for (const line of lines) {
        this.emit({ type: "typing", active: true });
        await this.sleep(Math.min(3000, 800 + line.length * 25), g);
        this.emit({ type: "typing", active: false });
        this.append("text_agent", "text", { kind: "agent_message", text: line, from_call: false });
        await this.sleep(400, g);
      }
    } catch (e) {
      this.emit({ type: "typing", active: false });
      throw e;
    } finally {
      if (this.textGen === gen) this.textBusy = false;
    }
  }

  private async textTurn(text: string, gen: number) {
    const s = this.slots;
    const userTexts = this.events.filter((e) => e.payload.kind === "user_message").length;

    if (s.agent_name === null) {
      if (userTexts <= 1) {
        await this.bubbles(["hey! I'm your new assistant from Persona.", "first things first: what do you want to call me?"], gen);
        return;
      }
      const name = pickName(text);
      this.setSlot("agent_name", name, "text");
      await this.bubbles([`${name} it is.`, "mind if I call you real quick? it's faster than typing all this out."], gen);
      await this.sleep(1800, { get: () => this.textGen, at: gen });
      this.startRinging("agent");
      return;
    }

    // Early graduation: a concrete ask before we got to that slot.
    if (s.help_need === null && looksLikeATask(text)) {
      this.setSlot("help_need", text, "text");
      if (s.user_name === null) {
        await this.bubbles(["ok, that I can do.", "before I dig in, what should I call you?"], gen);
        return;
      }
    }

    if (s.user_name === null) {
      if (userTexts > 0 && this.lastAgentAsked("call you")) {
        this.setSlot("user_name", pickName(text), "text");
      } else {
        await this.bubbles(["what should I call you?"], gen);
        return;
      }
    }
    if (this.slots.help_need === null) {
      if (this.lastAgentAsked("eating your time")) {
        this.setSlot("help_need", text, "text");
      } else {
        await this.bubbles([`nice to meet you, ${this.slots.user_name}.`, "what's been eating your time lately? anything I could take off your plate?"], gen);
        return;
      }
    }
    if (this.slots.gmail === null) {
      await this.sendGmailLink("text", gen);
      return;
    }
    if (this.slots.gmail === "link_sent") {
      if (/skip|no|later|nah/i.test(text)) {
        this.setSlot("gmail", "skipped", "text");
        this.append("google", "system", { kind: "gmail", phase: "skipped", email: null });
      } else {
        await this.bubbles(["no rush. the link's above whenever you're ready, or say skip."], gen);
        return;
      }
    }
    if (!this.slots.graduated) {
      await this.graduate("text", gen);
      return;
    }
    await this.bubbles([pickReply(text)], gen);
  }

  private lastAgentAsked(needle: string): boolean {
    for (let i = this.events.length - 1; i >= 0; i--) {
      const p = this.events[i].payload;
      if (p.kind === "agent_message" || (p.kind === "voice_utterance" && p.speaker === "agent")) return (p.text ?? "").includes(needle);
    }
    return false;
  }

  private gmailUrl() {
    return `${location.origin}/api/auth/google/start?phone=${encodeURIComponent(this.phone)}`;
  }

  private async sendGmailLink(via: Medium, gen: number) {
    this.setSlot("gmail", "link_sent", via);
    this.append("google", "system", { kind: "gmail", phase: "link_sent", email: null });
    if (via === "text") {
      await this.bubbles([
        "last thing: connect your gmail and I can actually start doing things for you.",
        `tap this and pick your account: ${this.gmailUrl()}`,
        "or say skip and we'll do it later.",
      ], gen);
    } else {
      this.append("voice_agent", "text", { kind: "agent_message", text: `here's the gmail link: ${this.gmailUrl()}`, from_call: true });
    }
    // The mock cannot see the OAuth callback, so it pretends the user connected after a while.
    const t = setTimeout(() => {
      this.timers.delete(t);
      if (this.slots.gmail !== "link_sent") return;
      this.slots = { ...this.slots, gmail: "connected", gmail_email: "you@gmail.com" };
      this.emit({ type: "slots", slots: this.slots });
      this.append("google", "system", { kind: "gmail", phase: "connected", email: "you@gmail.com" });
      if (this.call.phase === "connected") {
        this.decision("gmail", "defer", "gmail connected mid-call; agent reacts at the next boundary");
        void this.voiceScript(async (line) => {
          await line("perfect, you're connected. I can see two unanswered emails from your professor about friday.");
          await line("I'll text you the details. talk soon.");
          this.endCall("agent_hangup");
        });
      } else {
        this.decision("gmail", "start");
        void this.graduate("text", ++this.textGen).catch(() => undefined);
      }
    }, 9000);
    this.timers.add(t);
  }

  private async graduate(via: Medium, gen: number) {
    this.slots = { ...this.slots, graduated: true };
    this.append(via === "voice" ? "voice_agent" : "text_agent", via, { kind: "tool_call", name: "graduate", args: {}, result: { ok: true } });
    this.append("system", "system", { kind: "graduated" });
    this.emit({ type: "slots", slots: this.slots });
    const need = this.slots.help_need ?? "what you asked for";
    const first = this.slots.gmail === "connected"
      ? "you have an unanswered email from your professor about friday's deadline. want me to draft a reply?"
      : `first thing I'll do is dig into ${shorten(need)}. want me to start now?`;
    if (via === "text") await this.bubbles(["ok, I've got what I need.", first], gen);
  }

  // ---- calls --------------------------------------------------------------------------
  private startRinging(by: "agent" | "user") {
    if (this.call.phase !== "none") return;
    const call_id = `mock_${Date.now()}`;
    this.call = { phase: "ringing", reason: null, call_id, initiated_by: by, started_at: null, ended_at: null };
    this.emit({ type: "call", call: this.call });
    this.append("call", "system", { kind: "call", transition: "ringing", reason: "collect name and what they need help with", call_id, initiated_by: by });
    this.ringTimer = setTimeout(() => {
      this.ringTimer = null;
      if (this.call.phase === "ringing") this.declined("timeout");
    }, 30000);
    this.timers.add(this.ringTimer);
  }

  private declined(reason: string) {
    if (this.ringTimer) clearTimeout(this.ringTimer);
    this.ringTimer = null;
    this.append("call", "system", { kind: "call", transition: "declined", reason, call_id: this.call.call_id, initiated_by: this.call.initiated_by });
    this.call = { ...EMPTY_CALL };
    this.emit({ type: "call", call: this.call });
    this.decision("call", "start", `declined (${reason}); text agent responds`);
    const gen = ++this.textGen;
    void this.bubbles(["no worries, we can do it all here."], gen)
      .then(() => this.textTurn("", gen))
      .catch(() => undefined);
  }

  private onCallAction(action: string, reason?: string) {
    switch (action) {
      case "start":
        if (this.call.phase !== "none") return;
        this.call = { phase: "connecting", reason: null, call_id: `mock_${Date.now()}`, initiated_by: "user", started_at: null, ended_at: null };
        this.emit({ type: "call", call: this.call });
        this.append("call", "system", { kind: "call", transition: "connecting", reason: null, call_id: this.call.call_id, initiated_by: "user" });
        this.armAudioTimeout();
        return;
      case "accept":
        if (this.call.phase !== "ringing") return;
        if (this.ringTimer) clearTimeout(this.ringTimer);
        this.ringTimer = null;
        this.setCall({ phase: "connecting" });
        this.append("call", "system", { kind: "call", transition: "connecting", reason: null, call_id: this.call.call_id, initiated_by: this.call.initiated_by });
        this.armAudioTimeout();
        return;
      case "decline":
        if (this.call.phase === "ringing") this.declined("user_declined");
        return;
      case "failed":
        if (this.call.phase === "connecting" || this.call.phase === "ringing") this.failCall(reason ?? "unknown");
        return;
      case "hangup":
        if (this.call.phase === "connected" || this.call.phase === "connecting") this.endCall("user_hangup");
        return;
    }
  }

  private armAudioTimeout() {
    const t = setTimeout(() => {
      this.timers.delete(t);
      if (this.call.phase === "connecting") this.failCall("audio_socket");
    }, 10000);
    this.timers.add(t);
  }

  private failCall(reason: string) {
    this.append("call", "system", { kind: "call", transition: "failed", reason, call_id: this.call.call_id, initiated_by: this.call.initiated_by });
    this.call = { ...EMPTY_CALL };
    this.floor = "text";
    this.emit({ type: "call", call: this.call });
    this.decision("call", "start", `call failed (${reason}); text agent responds`);
    const gen = ++this.textGen;
    const why = reason === "mic_denied" ? "looks like I couldn't get your mic." : "the call didn't connect.";
    void this.bubbles([`${why} no problem, let's keep going here.`], gen).then(() => this.textTurn("", gen)).catch(() => undefined);
  }

  openAudio(): AudioLink {
    if (this.audio) return new ScriptedAudioLink(this, true);
    const link = new ScriptedAudioLink(this, false);
    this.audio = link;
    void link.ready.then(() => {
      if (this.call.phase !== "connecting") return;
      this.call = { ...this.call, phase: "connected", started_at: new Date().toISOString() };
      this.floor = "voice";
      this.emit({ type: "call", call: this.call });
      this.append("call", "system", { kind: "call", transition: "connected", reason: null, call_id: this.call.call_id, initiated_by: this.call.initiated_by });
      this.saidTypingHint = false;
      void this.voiceScript(this.callScript.bind(this));
    });
    return link;
  }

  audioClosed(link: ScriptedAudioLink) {
    if (this.audio !== link) return;
    this.audio = null;
    if (this.call.phase === "connected" || this.call.phase === "connecting") this.endCall("connection_lost");
  }

  private endCall(reason: string) {
    if (this.call.phase === "none") return;
    this.callGen++;
    this.stopSpeaking();
    const link = this.audio;
    this.audio = null;
    link?.close();
    this.call = { ...this.call, phase: "ended", reason, ended_at: new Date().toISOString() };
    this.floor = "text";
    this.emit({ type: "call", call: this.call });
    this.append("call", "system", { kind: "call", transition: "ended", reason, call_id: this.call.call_id, initiated_by: this.call.initiated_by });
    this.call = { ...EMPTY_CALL };
    this.emit({ type: "call", call: this.call });
    this.decision("call", "interrupt", `call ended (${reason}); floor to text`);
    const gen = ++this.textGen;
    const t = setTimeout(() => {
      this.timers.delete(t);
      const opener = reason === "agent_hangup" ? ["ok, that was fun."] : ["looks like we got cut off.", "want to keep going here?"];
      void this.bubbles(opener, gen)
        .then(() => (reason === "agent_hangup" || missingSlots(this.slots).length === 0 ? this.textTurn("", gen) : undefined))
        .catch(() => undefined);
    }, 900);
    this.timers.add(t);
  }

  // ---- voice agent ---------------------------------------------------------------------
  private async voiceScript(script: (line: (t: string) => Promise<void>) => Promise<void>) {
    const gen = this.callGen;
    try {
      await script((t) => this.voiceLine(t, gen));
    } catch (e) {
      if (!(e instanceof Cancelled)) console.error(e);
    }
  }

  private async callScript(line: (t: string) => Promise<void>) {
    const g = { get: () => this.callGen, at: this.callGen };
    const me = this.slots.agent_name ?? "your assistant";
    await this.sleep(600, g);
    if (this.slots.user_name === null) {
      await line(`hey, it's ${me}. so, what should I call you?`);
      await this.sleep(4500, g);
      await this.userLine("you can call me Sam.", g);
      this.setSlot("user_name", "Sam", "voice");
    } else {
      await line(`hey ${this.slots.user_name}, it's ${me}.`);
    }
    if (this.slots.help_need === null) {
      await line(`nice to meet you, ${this.slots.user_name}. what's been eating your time lately?`);
      await this.sleep(5000, g);
      await this.userLine("honestly, keeping up with school emails. my professor sends a lot.", g);
      this.setSlot("help_need", "keeping up with school emails", "voice");
    }
    if (this.slots.gmail === null) {
      await line("got it. I just texted you a link to connect your gmail. tap it whenever, I'll wait.");
      await this.sendGmailLink("voice", this.textGen);
      return; // the gmail timer continues the script
    }
    await line("I think I've got everything. I'll text you where to go from here. talk soon.");
    if (!this.slots.graduated) await this.graduate("voice", this.textGen);
    this.endCall("agent_hangup");
  }

  private async userLine(text: string, g: { get: () => number; at: number }) {
    const turn_id = `u_${Date.now()}`;
    const words = text.split(" ");
    for (let i = 1; i <= words.length; i++) {
      this.emit({ type: "partial", speaker: "user", turn_id, text: words.slice(0, i).join(" "), final: i === words.length });
      await this.sleep(140, g);
    }
    this.append("user", "voice", { kind: "voice_utterance", speaker: "user", text, turn_id, inferred: true });
  }

  /** The agent says a line out loud (browser speech synthesis), partials follow the spoken words. */
  private async voiceLine(text: string, gen: number) {
    const g = { get: () => this.callGen, at: gen };
    const turn_id = `a_${Date.now()}_${Math.floor(Math.random() * 1e4)}`;
    const words = text.split(" ");
    let emitted = 0;
    const emitUpTo = (n: number) => {
      const k = Math.min(words.length, Math.max(n, emitted));
      if (k === emitted) return;
      emitted = k;
      this.emit({ type: "partial", speaker: "agent", turn_id, text: words.slice(0, k).join(" "), final: k === words.length });
    };

    this.speaking = true;
    await new Promise<void>((resolve) => {
      let done = false;
      let sawBoundary = false;
      const finish = () => {
        if (done) return;
        done = true;
        clearInterval(watch);
        clearTimeout(safety);
        clearInterval(fallback);
        resolve();
      };
      const perWord = 260;
      const fallback = setInterval(() => {
        if (sawBoundary) return;
        emitUpTo(emitted + 1);
        if (emitted >= words.length) finish();
      }, perWord);
      const safety = setTimeout(finish, words.length * 450 + 2500);
      const watch = setInterval(() => {
        if (this.callGen !== gen) {
          this.stopSpeaking();
          finish();
        }
      }, 150);

      if (typeof speechSynthesis === "undefined") return;
      const u = new SpeechSynthesisUtterance(text);
      u.rate = 1.04;
      u.pitch = 1;
      const voice = pickVoice();
      if (voice) u.voice = voice;
      u.onboundary = (e) => {
        if (e.name && e.name !== "word") return;
        sawBoundary = true;
        const before = text.slice(0, e.charIndex);
        emitUpTo((before.match(/\s/g)?.length ?? 0) + 1);
      };
      u.onend = () => {
        emitUpTo(words.length);
        finish();
      };
      u.onerror = () => finish();
      speechSynthesis.cancel();
      speechSynthesis.speak(u);
    });
    this.speaking = false;
    if (this.callGen !== gen) throw new Cancelled();
    emitUpTo(words.length);
    this.append("voice_agent", "voice", { kind: "voice_utterance", speaker: "agent", text, turn_id, inferred: true });
    await this.sleep(350, g);
  }
}

function pickVoice(): SpeechSynthesisVoice | null {
  const voices = speechSynthesis.getVoices();
  const preferred = ["Samantha", "Google US English", "Microsoft Aria", "Karen", "Daniel", "Moira"];
  for (const name of preferred) {
    const v = voices.find((x) => x.name.startsWith(name));
    if (v) return v;
  }
  return voices.find((v) => v.lang.startsWith("en") && v.localService) ?? voices.find((v) => v.lang.startsWith("en")) ?? null;
}

// ---- small helpers -----------------------------------------------------------------------
function pickName(text: string): string {
  const filler = /^(?:you can|just|maybe|how about|let's go with|lets go with|i'll|ill|i'd|id|i will|please)\s+/i;
  const verbs = /^(?:call (?:me|you|yourself)|name (?:is|yourself)|be|go with|you're|youre|you are|i'm|im|i am|my name is|my name's|name's|it's|its|it is)\s+/i;
  let cleaned = text.trim();
  for (let i = 0; i < 3; i++) cleaned = cleaned.replace(filler, "").replace(verbs, "");
  const first = cleaned.replace(/[.!?,]+$/, "").split(/\s+/)[0] || "Juno";
  return first.charAt(0).toUpperCase() + first.slice(1);
}
function looksLikeATask(text: string): boolean {
  return text.length > 40 && /(help me|i need|can you|i want|remind|draft|email|schedule|plan|find)/i.test(text);
}
function shorten(text: string): string {
  return text.length > 60 ? `${text.slice(0, 57)}...` : text;
}
function pickReply(text: string): string {
  if (/thank/i.test(text)) return "anytime.";
  if (/\?$/.test(text)) return "good question. give me a sec and I'll look into it.";
  return "on it.";
}
