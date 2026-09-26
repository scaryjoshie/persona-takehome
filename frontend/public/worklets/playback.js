// PCM16 mono at sourceRate (24 kHz) -> speakers. Ring of chunks, ~100 ms prebuffer,
// linear resampling when the context runs at another rate. Posts {level} every ~50 ms
// measured at playback time, so the orb moves with what is actually heard.
class PcmPlayback extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = (options && options.processorOptions) || {};
    const src = o.sourceRate || 24000;
    this.ratio = src / sampleRate; // source samples per output sample
    this.prebuffer = Math.round((src * (o.prebufferMs || 100)) / 1000);
    this.queue = [];
    this.queued = 0;
    this.cur = null;
    this.idx = 0;
    this.playing = false;
    this.levelAcc = 0;
    this.levelN = 0;
    this.levelEvery = Math.round(sampleRate * 0.05);
    this.port.onmessage = (e) => {
      if (e.data === "flush") {
        this.queue = [];
        this.queued = 0;
        this.cur = null;
        this.idx = 0;
        this.playing = false;
        return;
      }
      const a = new Int16Array(e.data);
      this.queue.push(a);
      this.queued += a.length;
    };
  }
  next() {
    while (!this.cur || this.idx >= this.cur.length) {
      if (this.cur) this.idx -= this.cur.length;
      const c = this.queue.shift();
      if (!c) {
        this.cur = null;
        return null;
      }
      this.queued -= c.length;
      this.cur = c;
    }
    const i = Math.floor(this.idx);
    const f = this.idx - i;
    const a = this.cur[i];
    let b;
    if (i + 1 < this.cur.length) b = this.cur[i + 1];
    else if (this.queue.length) b = this.queue[0][0];
    else b = a;
    this.idx += this.ratio;
    return (a + (b - a) * f) / 32768;
  }
  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!out) return true;
    if (!this.playing) {
      if (this.queued >= this.prebuffer) this.playing = true;
      else {
        out.fill(0);
        this.report(0, out.length);
        return true;
      }
    }
    for (let i = 0; i < out.length; i++) {
      const s = this.next();
      if (s === null) {
        out.fill(0, i);
        this.playing = false;
        break;
      }
      out[i] = s;
      this.levelAcc += s * s;
    }
    this.report(null, out.length);
    return true;
  }
  report(_unused, n) {
    this.levelN += n;
    if (this.levelN >= this.levelEvery) {
      const rms = Math.sqrt(this.levelAcc / this.levelN);
      this.port.postMessage({ level: rms });
      this.levelAcc = 0;
      this.levelN = 0;
    }
  }
}
registerProcessor("pcm-playback", PcmPlayback);
