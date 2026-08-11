// Transient detection for slicing one recording into multiple samples.
// Energy-flux onset detection: RMS envelope → positive flux → peak-pick
// above an adaptive threshold. Pure module, unit-tested in Node.

export const HOP = 256; // samples per envelope frame
export const WIN = 512; // RMS window

/** RMS envelope, one value per HOP samples. */
export function energyEnvelope(samples, hop = HOP, win = WIN) {
  const n = Math.max(0, Math.floor((samples.length - win) / hop) + 1);
  const env = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const off = i * hop;
    let sum = 0;
    for (let j = 0; j < win; j++) {
      const v = samples[off + j] || 0;
      sum += v * v;
    }
    env[i] = Math.sqrt(sum / win);
  }
  return env;
}

/**
 * Detect transient onsets. Returns ascending sample indices.
 *
 * threshold: fraction of the strongest onset's flux a peak must reach
 *   (lower = more sensitive = more slices). 0.05–0.8 is the useful range.
 * minGapMs: minimum spacing between onsets — flams/retriggers inside the
 *   gap are merged into the first hit.
 * floor: absolute RMS a frame must reach, so hiss never triggers slices.
 * preRollMs: how far to back the cut off from the detected frame so the
 *   attack itself isn't clipped.
 */
export function detectTransients(samples, sampleRate, {
  threshold = 0.2,
  minGapMs = 80,
  floor = 0.01,
  preRollMs = 5,
} = {}) {
  const env = energyEnvelope(samples);
  if (env.length < 3) return [];

  const flux = new Float32Array(env.length);
  for (let i = 1; i < env.length; i++) flux[i] = Math.max(0, env[i] - env[i - 1]);

  let maxFlux = 0;
  for (let i = 0; i < flux.length; i++) if (flux[i] > maxFlux) maxFlux = flux[i];
  if (maxFlux <= 0) return [];

  const thr = maxFlux * threshold;
  const minGap = Math.round((minGapMs / 1000) * sampleRate / HOP);
  const preRoll = Math.round((preRollMs / 1000) * sampleRate);

  const onsets = [];
  let lastFrame = -Infinity;
  for (let i = 1; i < flux.length - 1; i++) {
    if (
      flux[i] >= thr &&
      flux[i] >= flux[i - 1] &&
      flux[i] >= flux[i + 1] &&
      env[i] >= floor &&
      i - lastFrame >= minGap
    ) {
      // Walk back to the envelope valley so the cut lands before the
      // attack instead of a hop-quantized frame inside it.
      let j = i;
      while (j > 0 && env[j - 1] < env[j]) j--;
      onsets.push(Math.max(0, j * HOP - preRoll));
      lastFrame = i;
    }
  }
  return onsets;
}

/**
 * Convert onsets into [start, end) slice ranges: each slice runs to the
 * next onset (or to totalLength for the last one).
 */
export function sliceRanges(onsets, totalLength) {
  return onsets.map((start, i) => ({
    start,
    end: i + 1 < onsets.length ? onsets[i + 1] : totalLength,
  }));
}
