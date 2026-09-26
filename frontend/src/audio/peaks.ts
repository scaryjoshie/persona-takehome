// Waveform bars for a recorded clip: decode it once and take the loudness of each slice.
const cache = new Map<string, Promise<number[]>>();

export function audioPeaks(src: string, bars: number): Promise<number[]> {
  const key = `${src}#${bars}`;
  let pending = cache.get(key);
  if (!pending) {
    pending = decode(src, bars).catch(() => Array.from({ length: bars }, () => 0.3));
    cache.set(key, pending);
  }
  return pending;
}

async function decode(src: string, bars: number): Promise<number[]> {
  const data = await (await fetch(src)).arrayBuffer();
  const context = new OfflineAudioContext(1, 1, 44_100);
  const samples = (await context.decodeAudioData(data)).getChannelData(0);
  const size = Math.max(1, Math.floor(samples.length / bars));
  const rms = Array.from({ length: bars }, (_, i) => {
    let sum = 0;
    for (let j = i * size; j < (i + 1) * size && j < samples.length; j++) sum += samples[j] ** 2;
    return Math.sqrt(sum / size);
  });
  const peak = Math.max(...rms, 1e-6);
  return rms.map((v) => Math.max(0.12, v / peak));
}
