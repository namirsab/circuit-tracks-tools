import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  yinPitch, analyze, segmentNotes, quantizeNotes, transcribe, synthesizeMelody, midiToName, scaleIndices,
} from '../js/agent/transcribe.js';

const SR = 22050;

function sine(freq, seconds = 0.1, sr = SR) {
  const n = Math.round(seconds * sr);
  const out = new Float64Array(n);
  for (let i = 0; i < n; i++) out[i] = Math.sin((2 * Math.PI * freq * i) / sr);
  return out;
}

// ---------------------------------------------------------------------------
// YIN
// ---------------------------------------------------------------------------

for (const freq of [80.0, 110.0, 261.63, 440.0, 880.0]) {
  test(`yinPitch detects a ${freq} Hz sine`, () => {
    const { f0, confidence } = yinPitch(sine(freq), SR, undefined, undefined, undefined, Math.round(0.04 * SR));
    assert.ok(Math.abs(f0 - freq) / freq < 0.005, `f0=${f0}`);
    assert.ok(confidence > 0.95, `confidence=${confidence}`);
  });
}

test('yinPitch finds the fundamental of a harmonic tone', () => {
  const n = Math.round(0.1 * SR);
  const x = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const t = i / SR;
    x[i] = Math.sin(2 * Math.PI * 220 * t) + 0.6 * Math.sin(2 * Math.PI * 440 * t) + 0.4 * Math.sin(2 * Math.PI * 660 * t);
  }
  const { f0 } = yinPitch(x, SR, undefined, undefined, undefined, Math.round(0.04 * SR));
  assert.ok(Math.abs(f0 - 220) < 2, `f0=${f0}`);
});

test('yinPitch gives noise low confidence', () => {
  // xorshift32, seeded: deterministic white noise (no periodicity to detect).
  const n = Math.round(0.1 * SR);
  const x = new Float64Array(n);
  let s = 12345;
  for (let i = 0; i < n; i++) {
    s ^= s << 13; s ^= s >>> 17; s ^= s << 5; s |= 0;
    x[i] = (s / 2147483648) * 0.1;
  }
  const { confidence } = yinPitch(x, SR, undefined, undefined, undefined, Math.round(0.04 * SR));
  assert.ok(confidence < 0.6, `confidence=${confidence}`);
});

test('yinPitch on a too-short frame returns NaN', () => {
  const { f0, confidence } = yinPitch(new Float64Array(10), SR);
  assert.ok(Number.isNaN(f0));
  assert.equal(confidence, 0.0);
});

// ---------------------------------------------------------------------------
// analyze / segmentNotes
// ---------------------------------------------------------------------------

test('analyze marks a steady tone as voiced with the right pitch', () => {
  const audio = synthesizeMelody([[69, 0.5]], { sr: SR }); // A4 = 440 Hz
  const fa = analyze(audio, SR);
  const voicedCount = fa.voiced.reduce((a, b) => a + b, 0);
  assert.ok(voicedCount > 0, 'expected some voiced frames');
  let sum = 0;
  let count = 0;
  for (let i = 0; i < fa.midi.length; i++) if (fa.voiced[i]) { sum += fa.midi[i]; count += 1; }
  assert.ok(Math.abs(sum / count - 69) < 0.5, `mean midi=${sum / count}`);
});

test('analyze treats silence as unvoiced', () => {
  const fa = analyze(new Float64Array(SR * 0.5), SR);
  assert.ok(fa.voiced.every((v) => v === 0));
});

test('segmentNotes finds one note per pitch in a scale run', () => {
  const notes = [[60, 0.3], [62, 0.3], [64, 0.3], [65, 0.3]];
  const audio = synthesizeMelody(notes, { sr: SR, seed: 1 });
  const fa = analyze(audio, SR);
  const events = segmentNotes(fa);
  assert.equal(events.length, 4);
  events.forEach((e, i) => assert.ok(Math.abs(Math.round(e.midi) - notes[i][0]) <= 1, `event ${i} midi=${e.midi}`));
});

test('segmentNotes splits repeated same-pitch re-articulations ("da da da")', () => {
  const audio = synthesizeMelody([[64, 0.25], [64, 0.25], [64, 0.25]], { sr: SR, seed: 2 });
  const fa = analyze(audio, SR);
  const events = segmentNotes(fa);
  assert.equal(events.length, 3);
});

test('segmentNotes handles rests', () => {
  const audio = synthesizeMelody([[60, 0.3], [null, 0.2], [64, 0.3]], { sr: SR, seed: 3 });
  const fa = analyze(audio, SR);
  const events = segmentNotes(fa);
  assert.equal(events.length, 2);
});

// ---------------------------------------------------------------------------
// quantizeNotes / scaleIndices
// ---------------------------------------------------------------------------

const ev = (startS, durationS, midi) => ({ startS, endS: startS + durationS, midi, confidence: 0.9, levelDb: -6 });

test('quantizeNotes snaps onsets to the 16th-note grid at 120 BPM', () => {
  const q = quantizeNotes([ev(0.5, 0.4, 60)], 120, 4);
  // 120 BPM, 16th = 0.125s; 0.5s -> step 4
  assert.equal(q.length, 1);
  assert.equal(q[0].step, 4);
  assert.equal(q[0].note, 60);
});

test('quantizeNotes compensates latency', () => {
  const withLatency = quantizeNotes([ev(0.56, 0.2, 60)], 120, 4, { latencyS: 0.06 });
  assert.equal(withLatency[0].step, 4);
});

test('quantizeNotes drops notes outside the bar range', () => {
  const q = quantizeNotes([ev(10.0, 0.2, 60)], 120, 1);
  assert.equal(q.length, 0);
});

test('quantizeNotes transposes', () => {
  const q = quantizeNotes([ev(0.0, 0.2, 60)], 120, 1, { transpose: -12 });
  assert.equal(q[0].note, 48);
});

test('quantizeNotes snaps to a scale', () => {
  const q = quantizeNotes([ev(0.0, 0.2, 61)], 120, 1, { scaleRoot: 'C', scaleType: 'major' }); // C#4 -> nearest C major note
  assert.notEqual(q[0].note, 61);
});

test('quantizeNotes rejects an unknown scale', () => {
  assert.throws(() => quantizeNotes([ev(0, 0.2, 60)], 120, 1, { scaleType: 'not-a-scale' }), /Unknown scale_type/);
});

test('scaleIndices returns null without a scale type', () => {
  assert.equal(scaleIndices('C', null), null);
});

// ---------------------------------------------------------------------------
// End-to-end transcribe()
// ---------------------------------------------------------------------------

test('transcribe recovers a twinkle-twinkle melody', () => {
  const bpm = 100;
  const step = 60 / bpm / 4; // 16th note
  const melody = [60, 60, 67, 67, 69, 69, 67].map((m) => [m, step * 2]);
  const audio = synthesizeMelody(melody, { sr: SR, seed: 4 });
  const result = transcribe(audio, SR, bpm, { bars: 2 });
  assert.equal(result.notes.length, 7);
  assert.deepEqual(result.notes.map((n) => n.note), [60, 60, 67, 67, 69, 69, 67]);
  assert.equal(result.warnings.length, 0);
});

test('transcribe splits notes across patterns by pattern_length', () => {
  const bpm = 120;
  const step = 60 / bpm / 4;
  const melody = Array.from({ length: 20 }, (_, i) => [60 + (i % 5), step * 2]);
  const audio = synthesizeMelody(melody, { sr: SR, seed: 5 });
  const result = transcribe(audio, SR, bpm, { bars: 4, patternLength: 16 });
  assert.ok(result.patterns.length >= 2, `expected multiple patterns, got ${result.patterns.length}`);
});

test('transcribe honors offsetS (count-in stripped before transcription)', () => {
  const bpm = 120;
  const countIn = synthesizeMelody([[null, 1.0]], { sr: SR });
  const melody = synthesizeMelody([[64, 0.3]], { sr: SR, seed: 6 });
  const audio = new Float32Array(countIn.length + melody.length);
  audio.set(countIn, 0);
  audio.set(melody, countIn.length);
  const result = transcribe(audio, SR, bpm, { bars: 2, offsetS: 1.0 });
  assert.equal(result.notes.length, 1);
  assert.equal(result.notes[0].note, 64);
});

test('transcribe warns on silence', () => {
  const result = transcribe(new Float32Array(SR * 1.0), SR, 120, { bars: 1 });
  assert.ok(result.warnings.some((w) => /No pitched notes/.test(w)));
});

test('transcribe works at 48kHz', () => {
  const audio = synthesizeMelody([[67, 0.4]], { sr: 48000, seed: 7 });
  const result = transcribe(audio, 48000, 120, { bars: 2 });
  assert.equal(result.notes.length, 1);
  assert.equal(result.notes[0].note, 67);
});

test('midiToName formats octave and pitch class', () => {
  assert.equal(midiToName(60), 'C4');
  assert.equal(midiToName(69), 'A4');
});
