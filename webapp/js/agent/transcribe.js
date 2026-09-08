// Monophonic audio -> note transcription (singing, humming, whistling).
// Direct port of circuit_tracks/transcribe.py so both servers answer the
// same way for the same take: framewise YIN pitch + RMS -> voicing -> note
// segmentation -> 16th-note step quantization -> optional scale snap.
//
// Pure functions over Float32Array/Array; no DOM or Web Audio API here (see
// mic.js for getUserMedia capture) so this half is unit-testable in Node.
import { quantizeToScale } from '../scales.js';
import { SCALE_ROOT_INDEX, SCALE_TYPE_INDEX } from './song-compiler.js';

const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];

// Voice-friendly defaults: ~C2 to ~D6.
export const DEFAULT_FMIN = 60.0;
export const DEFAULT_FMAX = 1200.0;

export function midiToName(note) {
  return `${NOTE_NAMES[((note % 12) + 12) % 12]}${Math.floor(note / 12) - 1}`;
}

function midiToHz(midi) {
  return 440.0 * 2.0 ** ((midi - 69.0) / 12.0);
}

// ---------------------------------------------------------------------------
// YIN pitch detection
// ---------------------------------------------------------------------------

// YIN difference function d(tau) for tau in 0..tauMax, direct time-domain
// sum (no FFT: frame windows here are a few thousand samples at most, so the
// O(win*tauMax) sum stays well under a browser's per-frame budget for a
// one-shot post-recording analysis pass).
function differenceFunction(frame, win, tauMax) {
  const d = new Float64Array(tauMax + 1);
  for (let tau = 0; tau <= tauMax; tau++) {
    let sum = 0;
    for (let j = 0; j < win; j++) {
      const diff = frame[j] - frame[j + tau];
      sum += diff * diff;
    }
    d[tau] = sum;
  }
  return d;
}

// Cumulative mean normalized difference function.
function cmndf(d) {
  const out = new Float64Array(d.length);
  out[0] = 1.0;
  let running = 0;
  for (let tau = 1; tau < d.length; tau++) {
    running += d[tau];
    out[tau] = running > 0 ? (d[tau] * tau) / running : 1.0;
  }
  return out;
}

function parabolicMin(y, i) {
  if (i > 0 && i < y.length - 1) {
    const a = y[i - 1];
    const b = y[i];
    const c = y[i + 1];
    const denom = a - 2.0 * b + c;
    if (denom !== 0) return i + 0.5 * (a - c) / denom;
  }
  return i;
}

/**
 * Estimate the fundamental frequency of one frame with the YIN algorithm.
 * Returns { f0, confidence }; f0 is NaN if the frame is too short.
 */
export function yinPitch(frame, sr, fmin = DEFAULT_FMIN, fmax = DEFAULT_FMAX, threshold = 0.15, win = null) {
  const tauMax = Math.floor(sr / fmin);
  const tauMin = Math.max(2, Math.floor(sr / fmax));
  const w = win ?? frame.length - tauMax;
  if (w < tauMin || frame.length < w + tauMax) return { f0: NaN, confidence: 0.0 };

  const cm = cmndf(differenceFunction(frame, w, tauMax));
  let tau = -1;
  for (let i = tauMin; i < cm.length - 1; i++) {
    if (cm[i] < threshold) { tau = i; break; }
  }
  if (tau < 0) {
    let best = tauMin;
    let bestVal = Infinity;
    for (let i = tauMin; i < cm.length - 1; i++) {
      if (cm[i] < bestVal) { bestVal = cm[i]; best = i; }
    }
    tau = best;
  } else {
    while (tau + 1 < cm.length - 1 && cm[tau + 1] < cm[tau]) tau += 1;
  }

  const tauF = parabolicMin(cm, tau);
  const confidence = Math.min(1.0, Math.max(0.0, 1.0 - cm[tau]));
  return { f0: sr / tauF, confidence };
}

// ---------------------------------------------------------------------------
// Framewise analysis
// ---------------------------------------------------------------------------

function nanMedianFilter(x, width) {
  const half = Math.floor(width / 2);
  const out = new Float64Array(x.length).fill(NaN);
  for (let i = 0; i < x.length; i++) {
    const window = [];
    for (let j = Math.max(0, i - half); j <= Math.min(x.length - 1, i + half); j++) {
      if (!Number.isNaN(x[j])) window.push(x[j]);
    }
    if (window.length) {
      window.sort((a, b) => a - b);
      const mid = Math.floor(window.length / 2);
      out[i] = window.length % 2 ? window[mid] : (window[mid - 1] + window[mid]) / 2;
    }
  }
  return out;
}

/**
 * Run YIN + RMS over `audio` (Float32Array/Array, mono, -1..1) in frames and
 * decide which frames are voiced: louder than peak+silenceDb (and above
 * floorDb absolute), YIN confidence >= minConfidence, pitch inside fmin..fmax.
 */
export function analyze(audio, sr, {
  fmin = DEFAULT_FMIN, fmax = DEFAULT_FMAX, hopS = 0.010, winS = 0.040, threshold = 0.15,
  silenceDb = -30.0, floorDb = -60.0, minConfidence = 0.6,
} = {}) {
  const hop = Math.max(1, Math.round(sr * hopS));
  const win = Math.max(64, Math.round(sr * winS));
  const tauMax = Math.floor(sr / fmin);
  const frameLen = win + tauMax;

  const nFrames = Math.max(0, Math.floor((audio.length - win) / hop) + 1);
  const padded = new Float64Array(audio.length + frameLen);
  padded.set(audio);

  const times = new Float64Array(nFrames);
  const f0 = new Float64Array(nFrames).fill(NaN);
  const conf = new Float64Array(nFrames);
  const rms = new Float64Array(nFrames);

  for (let i = 0; i < nFrames; i++) {
    const start = i * hop;
    const frame = padded.subarray(start, start + frameLen);
    let sumSq = 0;
    for (let j = 0; j < win; j++) sumSq += frame[j] * frame[j];
    rms[i] = Math.sqrt(sumSq / win);
    times[i] = (start + win / 2) / sr;
    if (rms[i] < 1e-5) continue;
    const { f0: freq, confidence } = yinPitch(frame, sr, fmin, fmax, threshold, win);
    f0[i] = freq;
    conf[i] = confidence;
  }

  const rmsDb = Float64Array.from(rms, (v) => 20.0 * Math.log10(v + 1e-9));
  const peakDb = nFrames ? Math.max(...rmsDb) : -120.0;
  const voiced = new Uint8Array(nFrames);
  for (let i = 0; i < nFrames; i++) {
    const inRange = f0[i] >= fmin && f0[i] <= fmax;
    voiced[i] = rmsDb[i] > Math.max(peakDb + silenceDb, floorDb) && conf[i] >= minConfidence && inRange ? 1 : 0;
  }

  let midi = new Float64Array(nFrames).fill(NaN);
  for (let i = 0; i < nFrames; i++) if (voiced[i]) midi[i] = 69.0 + 12.0 * Math.log2(f0[i] / 440.0);
  midi = nanMedianFilter(midi, 5);
  for (let i = 0; i < nFrames; i++) if (!voiced[i]) midi[i] = NaN;

  return { sr, hop, times, f0, midi, confidence: conf, rmsDb, voiced, peakDb };
}

// ---------------------------------------------------------------------------
// Note segmentation
// ---------------------------------------------------------------------------

function median(arr) {
  const s = [...arr].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/**
 * Group voiced frames into notes. A note ends when voicing stops, when the
 * pitch drifts by more than pitchTolerance semitones for splitFrames
 * consecutive frames, or when the level rises by more than onsetDb within
 * onsetLag frames (a new attack on the same pitch, e.g. "da da da").
 */
export function segmentNotes(fa, {
  minNoteS = 0.06, pitchTolerance = 0.75, splitFrames = 3, onsetDb = 6.0, onsetLag = 3,
} = {}) {
  const hopS = fa.hop / fa.sr;
  const minFrames = Math.max(1, Math.round(minNoteS / hopS));
  const notes = [];

  let start = null;
  let pitches = [];
  let drift = 0;

  const close = (endIdx) => {
    if (start !== null && endIdx - start >= minFrames) {
      let confSum = 0;
      let levelMax = -Infinity;
      for (let i = start; i < endIdx; i++) {
        confSum += fa.confidence[i];
        if (fa.rmsDb[i] > levelMax) levelMax = fa.rmsDb[i];
      }
      notes.push({
        startS: fa.times[start] - fa.hop / fa.sr / 2,
        endS: fa.times[endIdx - 1] + hopS / 2,
        midi: median(pitches),
        confidence: confSum / (endIdx - start),
        levelDb: levelMax,
      });
    }
    start = null;
    pitches = [];
    drift = 0;
  };

  for (let i = 0; i < fa.times.length; i++) {
    if (!fa.voiced[i]) { close(i); continue; }
    const p = fa.midi[i];
    if (start === null) { start = i; pitches = [p]; drift = 0; continue; }

    // New attack on a (possibly) same pitch: level jumped up recently.
    if (i - start >= Math.max(minFrames, onsetLag) && fa.rmsDb[i] - fa.rmsDb[i - onsetLag] > onsetDb) {
      close(i - onsetLag + 1);
      start = i - onsetLag + 1;
      pitches = [];
      for (let j = start; j <= i; j++) pitches.push(fa.midi[j]);
      drift = 0;
      continue;
    }

    // Pitch moved to a different note.
    if (Math.abs(p - median(pitches)) > pitchTolerance) {
      drift += 1;
      if (drift >= splitFrames) {
        const splitAt = i - splitFrames + 1;
        close(splitAt);
        start = splitAt;
        pitches = [];
        for (let j = start; j <= i; j++) pitches.push(fa.midi[j]);
        drift = 0;
      } else {
        pitches.push(p);
      }
    } else {
      drift = 0;
      pitches.push(p);
    }
  }
  close(fa.times.length);
  return notes;
}

// ---------------------------------------------------------------------------
// Quantization to sequencer steps
// ---------------------------------------------------------------------------

function toStep(n) {
  return { note: n.note, gate: n.gate, velocity: n.velocity };
}

export function scaleIndices(scaleRoot, scaleType) {
  if (!scaleType) return null;
  const typeI = SCALE_TYPE_INDEX[scaleType.toLowerCase()];
  if (typeI === undefined) throw new Error(`Unknown scale_type '${scaleType}'. Valid: ${Object.keys(SCALE_TYPE_INDEX).sort().join(', ')}`);
  const rootI = SCALE_ROOT_INDEX[scaleRoot || 'C'];
  if (rootI === undefined) throw new Error(`Unknown scale_root '${scaleRoot}'. Valid: ${Object.keys(SCALE_ROOT_INDEX).sort().join(', ')}`);
  return [rootI, typeI];
}

/**
 * Snap note events to a 16th-note grid and (optionally) to a scale.
 * `latencyS` is subtracted from every onset to compensate capture delay.
 * Notes outside `bars` are dropped; two notes landing on the same step keep
 * the longer one; gates are clipped so notes never overlap the next onset.
 */
export function quantizeNotes(events, bpm, bars, {
  stepsPerBar = 16, latencyS = 0.0, transpose = 0, scaleRoot = null, scaleType = null, gateResolution = 0.5,
} = {}) {
  const stepS = 60.0 / bpm / (stepsPerBar / 4);
  const total = bars * stepsPerBar;
  const scale = scaleIndices(scaleRoot, scaleType);
  const maxLevel = events.length ? Math.max(...events.map((e) => e.levelDb)) : 0.0;

  const byStep = new Map();
  for (const ev of events) {
    const step = Math.round((ev.startS - latencyS) / stepS);
    if (step < 0 || step >= total) continue;
    const durationS = ev.endS - ev.startS;
    let gate = Math.round(durationS / stepS / gateResolution) * gateResolution;
    gate = Math.min(16.0, Math.max(gateResolution, gate));
    let note = Math.round(ev.midi) + transpose;
    if (scale) note = quantizeToScale(note, scale[1], scale[0]);
    note = Math.min(127, Math.max(0, note));
    let velocity = Math.round(127 + 4 * (ev.levelDb - maxLevel));
    velocity = Math.min(127, Math.max(40, velocity));
    const q = {
      step, note, gate, velocity,
      confidence: Math.round(ev.confidence * 1000) / 1000,
      startS: Math.round(ev.startS * 1000) / 1000,
      durationS: Math.round(durationS * 1000) / 1000,
    };
    const prev = byStep.get(step);
    if (!prev || q.durationS > prev.durationS) byStep.set(step, q);
  }

  const ordered = [...byStep.keys()].sort((a, b) => a - b).map((s) => byStep.get(s));
  for (let i = 0; i < ordered.length - 1; i++) {
    ordered[i].gate = Math.min(ordered[i].gate, ordered[i + 1].step - ordered[i].step);
  }
  return ordered;
}

// ---------------------------------------------------------------------------
// End-to-end
// ---------------------------------------------------------------------------

function summary(bpm, bars, notes) {
  const names = notes.length ? notes.map((n) => midiToName(n.note)).join(' ') : '(none)';
  return `${notes.length} notes over ${bars} bar(s) at ${bpm} BPM: ${names}`;
}

function patternsOf(notes, bars, stepsPerBar, patternLength) {
  const count = Math.max(1, Math.ceil((bars * stepsPerBar) / patternLength));
  const out = Array.from({ length: count }, () => ({}));
  for (const n of notes) out[Math.floor(n.step / patternLength)][String(n.step % patternLength)] = toStep(n);
  return out;
}

/**
 * Transcribe monophonic `audio` (Float32Array, -1..1, mono) into quantized
 * sequencer notes. `offsetS` skips the start of the audio (e.g. a count-in).
 * When `bars` is omitted it is derived from the audio length.
 */
export function transcribe(audio, sr, bpm, {
  bars = null, stepsPerBar = 16, offsetS = 0.0, latencyS = 0.0, transpose = 0,
  scaleRoot = null, scaleType = null, patternLength = 32,
} = {}) {
  let a = audio;
  if (offsetS > 0) a = a.subarray ? a.subarray(Math.round(offsetS * sr)) : a.slice(Math.round(offsetS * sr));
  const barS = (60.0 / bpm) * 4;
  const durationS = a.length / sr;
  const barsUsed = bars ?? Math.max(1, Math.ceil(durationS / barS - 0.05));

  const fa = analyze(a, sr);
  const events = segmentNotes(fa);
  const notes = quantizeNotes(events, bpm, barsUsed, { stepsPerBar, latencyS, transpose, scaleRoot, scaleType });

  const warnings = [];
  if (fa.peakDb < -40) {
    warnings.push(`Audio is very quiet (peak ${fa.peakDb.toFixed(0)} dBFS). Check the microphone permission.`);
  }
  if (!events.length) {
    warnings.push('No pitched notes detected.');
  } else if (!notes.length) {
    warnings.push('Notes were detected but none fell inside the requested bars.');
  }
  const dropped = events.length - notes.length;
  if (events.length && notes.length && dropped > 0) {
    warnings.push(`${dropped} note(s) dropped (outside the bar range or merged onto the same step).`);
  }

  const scaleLabel = scaleType ? `${scaleRoot || 'C'} ${scaleType}` : 'chromatic';
  const stepsDict = {};
  for (const n of notes) stepsDict[String(n.step)] = toStep(n);

  return {
    summary: summary(bpm, barsUsed, notes),
    bpm,
    bars: barsUsed,
    steps_per_bar: stepsPerBar,
    scale: scaleLabel,
    peak_db: Math.round(fa.peakDb * 10) / 10,
    notes: notes.map((n) => ({
      step: n.step, note: n.note, name: midiToName(n.note), gate: n.gate, velocity: n.velocity,
      confidence: n.confidence, start_s: n.startS, duration_s: n.durationS,
    })),
    steps: stepsDict,
    patterns: patternsOf(notes, barsUsed, stepsPerBar, patternLength),
    warnings,
  };
}

// ---------------------------------------------------------------------------
// Synthetic test audio (tests only — mirrors transcribe.py's synthesize_melody)
// ---------------------------------------------------------------------------

// Small seeded PRNG (mulberry32) so synthetic takes are reproducible without
// depending on Math.random in tests.
function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Render a "sung" monophonic melody for testing. `notes` is a list of
 * [midiNoteOrNull, durationS]; null is a rest. Each note is a harmonic tone
 * with vibrato, an attack/release envelope and a short silent gap (the
 * consonant) before the next note.
 */
export function synthesizeMelody(notes, {
  sr = 22050, vibratoHz = 5.5, vibratoSemitones = 0.25, gapS = 0.04, noise = 0.002, seed = 0,
} = {}) {
  const rng = mulberry32(seed);
  const uniform = (lo, hi) => lo + rng() * (hi - lo);
  // Box-Muller for the additive noise floor (matches numpy's rng.normal closely enough for test tolerances).
  const gaussian = () => {
    const u1 = Math.max(rng(), 1e-12);
    const u2 = rng();
    return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
  };

  const chunks = [];
  for (const [midi, dur] of notes) {
    const n = Math.round(dur * sr);
    if (midi === null || midi === undefined) { chunks.push(new Float64Array(n)); continue; }
    const gap = Math.min(Math.round(gapS * sr), Math.floor(n / 4));
    const length = n - gap;
    const detune = uniform(-0.1, 0.1);
    const tone = new Float64Array(length);
    let phase = 0;
    const gainEnv = uniform(0.7, 1.0);
    const attack = Math.min(Math.round(0.02 * sr), Math.floor(length / 2));
    const release = Math.min(Math.round(0.04 * sr), length - attack);
    for (let i = 0; i < length; i++) {
      const t = i / sr;
      const inst = midiToHz(midi + detune + vibratoSemitones * Math.sin(2 * Math.PI * vibratoHz * t));
      phase += (2 * Math.PI * inst) / sr;
      let s = Math.sin(phase) + 0.5 * Math.sin(2 * phase) + 0.3 * Math.sin(3 * phase) + 0.15 * Math.sin(4 * phase);
      let env = 1.0;
      if (i < attack) env = i / attack;
      if (release && i >= length - release) env *= (length - i) / release;
      s *= env * gainEnv;
      tone[i] = s;
    }
    const chunk = new Float64Array(n);
    chunk.set(tone, 0);
    chunks.push(chunk);
  }
  let total = 0;
  for (const c of chunks) total += c.length;
  const audio = new Float64Array(total);
  let offset = 0;
  for (const c of chunks) { audio.set(c, offset); offset += c.length; }
  for (let i = 0; i < audio.length; i++) audio[i] += noise * gaussian();
  let peak = 0;
  for (let i = 0; i < audio.length; i++) peak = Math.max(peak, Math.abs(audio[i]));
  const scale = 0.8 / Math.max(peak, 1e-9);
  const out = new Float32Array(audio.length);
  for (let i = 0; i < audio.length; i++) out[i] = audio[i] * scale;
  return out;
}
