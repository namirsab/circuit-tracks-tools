// Audio conversion to the Circuit Tracks sample format:
// WAV, 48 kHz, 16-bit PCM, mono (verified against CircuitFactorySamples.zip).
// Pure module — no Web Audio dependency — so it runs in Node for unit tests
// and produces deterministic output in the app.

export const TARGET_SAMPLE_RATE = 48000;
export const TARGET_BIT_DEPTH = 16;
export const TARGET_CHANNELS = 1;
export const BYTES_PER_SECOND = TARGET_SAMPLE_RATE * 2; // 96,000

/** Mix N Float32 channel arrays down to one by averaging. */
export function mixdownToMono(channels) {
  if (channels.length === 1) return channels[0];
  const len = channels[0].length;
  const out = new Float32Array(len);
  for (let i = 0; i < len; i++) {
    let sum = 0;
    for (const ch of channels) sum += ch[i];
    out[i] = sum / channels.length;
  }
  return out;
}

/**
 * Resample mono Float32 samples with linear interpolation.
 * Adequate for a sampler workflow; deterministic across environments
 * (unlike OfflineAudioContext, whose resampler varies per browser).
 */
export function resampleLinear(samples, fromRate, toRate) {
  if (fromRate === toRate) return samples;
  const outLen = Math.max(1, Math.round((samples.length * toRate) / fromRate));
  const out = new Float32Array(outLen);
  const ratio = fromRate / toRate;
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio;
    const i0 = Math.floor(pos);
    const i1 = Math.min(i0 + 1, samples.length - 1);
    const frac = pos - i0;
    out[i] = samples[i0] * (1 - frac) + samples[i1] * frac;
  }
  return out;
}

/** Convert Float32 [-1, 1] samples to clamped 16-bit PCM. */
export function floatTo16BitPcm(samples) {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    out[i] = Math.round(s < 0 ? s * 0x8000 : s * 0x7fff);
  }
  return out;
}

/** Build a complete mono 16-bit WAV file from Int16 PCM samples. */
export function buildWavFile(pcm, sampleRate = TARGET_SAMPLE_RATE) {
  const dataSize = pcm.length * 2;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);
  const writeStr = (off, s) => {
    for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i));
  };
  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + dataSize, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, TARGET_CHANNELS, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * TARGET_CHANNELS * 2, true); // byte rate
  view.setUint16(32, TARGET_CHANNELS * 2, true); // block align
  view.setUint16(34, TARGET_BIT_DEPTH, true);
  writeStr(36, 'data');
  view.setUint32(40, dataSize, true);
  new Int16Array(buffer, 44).set(pcm);
  return new Uint8Array(buffer);
}

/** Parse a WAV header; returns format info and the data chunk bounds. */
export function parseWavHeader(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const str = (off, len) =>
    String.fromCharCode(...bytes.subarray(off, off + len));
  if (str(0, 4) !== 'RIFF' || str(8, 4) !== 'WAVE') {
    throw new Error('Not a WAV file');
  }
  let offset = 12;
  let fmt = null;
  let dataOffset = null;
  let dataSize = null;
  while (offset + 8 <= bytes.length) {
    const id = str(offset, 4);
    const size = view.getUint32(offset + 4, true);
    if (id === 'fmt ') {
      fmt = {
        audioFormat: view.getUint16(offset + 8, true),
        channels: view.getUint16(offset + 10, true),
        sampleRate: view.getUint32(offset + 12, true),
        byteRate: view.getUint32(offset + 16, true),
        blockAlign: view.getUint16(offset + 20, true),
        bitsPerSample: view.getUint16(offset + 22, true),
      };
    } else if (id === 'data') {
      dataOffset = offset + 8;
      dataSize = Math.min(size, bytes.length - dataOffset);
    }
    offset += 8 + size + (size & 1);
  }
  if (!fmt || dataOffset === null) throw new Error('Missing fmt or data chunk');
  return { ...fmt, dataOffset, dataSize };
}

/**
 * Full conversion: multi-channel Float32 at any rate → Circuit Tracks WAV.
 * Returns { wav: Uint8Array, pcm: Int16Array, seconds }.
 */
export function convertToCircuitWav(channels, sourceRate) {
  const mono = mixdownToMono(channels);
  const resampled = resampleLinear(mono, sourceRate, TARGET_SAMPLE_RATE);
  const pcm = floatTo16BitPcm(resampled);
  return {
    wav: buildWavFile(pcm),
    pcm,
    seconds: pcm.length / TARGET_SAMPLE_RATE,
  };
}

// --- Editing operations (used by the editor UI; pure and testable) ---

/** Peak-normalize samples to the given peak (default 0.97 ≈ −0.26 dBFS). */
export function normalize(samples, targetPeak = 0.97) {
  let peak = 0;
  for (let i = 0; i < samples.length; i++) {
    const a = Math.abs(samples[i]);
    if (a > peak) peak = a;
  }
  if (peak === 0) return samples.slice();
  const gain = targetPeak / peak;
  const out = new Float32Array(samples.length);
  for (let i = 0; i < samples.length; i++) out[i] = samples[i] * gain;
  return out;
}

/** Apply a linear gain factor. */
export function applyGain(samples, gain) {
  const out = new Float32Array(samples.length);
  for (let i = 0; i < samples.length; i++) out[i] = samples[i] * gain;
  return out;
}

/** Apply linear fade-in/out over the given number of samples (in place safe). */
export function applyFades(samples, fadeInSamples, fadeOutSamples) {
  const out = samples.slice();
  const fi = Math.min(fadeInSamples, out.length);
  for (let i = 0; i < fi; i++) out[i] *= i / fi;
  const fo = Math.min(fadeOutSamples, out.length);
  for (let i = 0; i < fo; i++) out[out.length - 1 - i] *= i / fo;
  return out;
}

/** Extract the [start, end) sample range. */
export function trim(samples, start, end) {
  return samples.slice(
    Math.max(0, Math.floor(start)),
    Math.min(samples.length, Math.ceil(end)),
  );
}
