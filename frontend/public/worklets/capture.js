// Mic -> mono PCM16 little-endian at targetRate (24 kHz), in 20 ms chunks (480 samples).
// Resamples linearly when the AudioContext could not open at the target rate (Safari).
class PcmCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const target = (options.processorOptions && options.processorOptions.targetRate) || 24000;
    this.ratio = sampleRate / target; // input samples per output sample
    this.chunk = Math.round(target * 0.02);
    this.buf = new Int16Array(this.chunk);
    this.n = 0;
    this.prev = 0; // last input sample of the previous block
    this.pos = 0; // fractional read position relative to prev (index -1 = prev, 0 = block[0])
  }
  push(s) {
    const v = Math.max(-1, Math.min(1, s));
    this.buf[this.n++] = v < 0 ? v * 32768 : v * 32767;
    if (this.n === this.chunk) {
      this.port.postMessage(this.buf.buffer, [this.buf.buffer]);
      this.buf = new Int16Array(this.chunk);
      this.n = 0;
    }
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    if (this.ratio === 1) {
      for (let i = 0; i < ch.length; i++) this.push(ch[i]);
      return true;
    }
    // Positions are measured on a virtual array [prev, ...ch]; index 0 is prev.
    let p = this.pos;
    const len = ch.length;
    while (p < len) {
      const i = Math.floor(p);
      const f = p - i;
      const a = i === 0 ? this.prev : ch[i - 1];
      const b = ch[i];
      this.push(a + (b - a) * f);
      p += this.ratio;
    }
    this.pos = p - len;
    this.prev = ch[len - 1];
    return true;
  }
}
registerProcessor("pcm-capture", PcmCapture);
