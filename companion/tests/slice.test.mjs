import test from 'node:test';
import assert from 'node:assert/strict';
import { detectTransients, sliceRanges, energyEnvelope } from '../js/audio/slice.js';

const RATE = 48000;

/** Silence with decaying-noise bursts at the given times (seconds). */
function burstSignal(times, seconds, amp = 0.8, burstLen = 0.12) {
  const out = new Float32Array(Math.round(RATE * seconds));
  let seed = 42;
  const rand = () => {
    // deterministic LCG noise
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 0xffffffff - 0.5;
  };
  for (const t of times) {
    const start = Math.round(t * RATE);
    const n = Math.round(burstLen * RATE);
    for (let i = 0; i < n && start + i < out.length; i++) {
      const decay = Math.exp((-4 * i) / n);
      out[start + i] += amp * decay * rand() * 2;
    }
  }
  return out;
}

test('detects one onset per burst at the right positions', () => {
  const times = [0.2, 0.7, 1.3, 2.0];
  const signal = burstSignal(times, 2.6);
  const onsets = detectTransients(signal, RATE);
  assert.equal(onsets.length, times.length, `got onsets at ${onsets}`);
  onsets.forEach((onset, i) => {
    const err = Math.abs(onset - times[i] * RATE);
    assert.ok(err < RATE * 0.02, `onset ${i} off by ${(err / RATE) * 1000} ms`);
  });
});

test('onsets land slightly before the attack (pre-roll)', () => {
  const signal = burstSignal([0.5], 1.0);
  const [onset] = detectTransients(signal, RATE);
  assert.ok(onset <= 0.5 * RATE, 'onset must not start after the attack');
  assert.ok(onset > 0.5 * RATE - RATE * 0.03, 'onset should be within 30 ms of the attack');
});

test('quieter hits are still detected at default threshold', () => {
  const loud = burstSignal([0.2], 1.6, 0.9);
  const soft = burstSignal([1.0], 1.6, 0.35);
  const signal = new Float32Array(loud.length);
  for (let i = 0; i < signal.length; i++) signal[i] = loud[i] + soft[i];
  const onsets = detectTransients(signal, RATE);
  assert.equal(onsets.length, 2, `got onsets at ${onsets}`);
});

test('minGapMs merges retriggers into one onset', () => {
  const signal = burstSignal([0.5, 0.53], 1.2); // 30 ms apart
  const merged = detectTransients(signal, RATE, { minGapMs: 80 });
  assert.equal(merged.length, 1);
  const split = detectTransients(signal, RATE, { minGapMs: 10 });
  assert.equal(split.length, 2);
});

test('threshold controls sensitivity', () => {
  const loud = burstSignal([0.2], 1.6, 0.9);
  const soft = burstSignal([1.0], 1.6, 0.2);
  const signal = new Float32Array(loud.length);
  for (let i = 0; i < signal.length; i++) signal[i] = loud[i] + soft[i];
  const strict = detectTransients(signal, RATE, { threshold: 0.6 });
  const lax = detectTransients(signal, RATE, { threshold: 0.1 });
  assert.equal(strict.length, 1, 'high threshold keeps only the loud hit');
  assert.equal(lax.length, 2, 'low threshold catches the soft hit too');
});

test('silence and near-silence produce no onsets', () => {
  assert.deepEqual(detectTransients(new Float32Array(RATE), RATE), []);
  const hiss = new Float32Array(RATE);
  let seed = 7;
  for (let i = 0; i < hiss.length; i++) {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    hiss[i] = (seed / 0xffffffff - 0.5) * 0.005; // below the RMS floor
  }
  assert.deepEqual(detectTransients(hiss, RATE), []);
});

test('sliceRanges spans onset-to-onset and onset-to-end', () => {
  assert.deepEqual(sliceRanges([100, 500, 900], 1200), [
    { start: 100, end: 500 },
    { start: 500, end: 900 },
    { start: 900, end: 1200 },
  ]);
  assert.deepEqual(sliceRanges([], 1200), []);
});

test('energyEnvelope tracks signal level', () => {
  const signal = burstSignal([0.1], 0.5);
  const env = energyEnvelope(signal);
  const peak = Math.max(...env);
  assert.ok(peak > 0.1);
  assert.ok(env[0] < peak / 10, 'leading silence stays near zero');
});
