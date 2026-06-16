// Polyphonic synth track emulating the Circuit Tracks synth engine:
// 2 oscillators + noise, filter (6 types), env1 (amp) / env2 (filter) / env3,
// 2 LFOs, mod matrix routing, macro knobs that ADD to base parameter values,
// per-patch distortion / chorus / EQ.
import { OSC_WAVEFORMS, MACRO_DESTINATIONS, lfoSyncToBeats } from '../constants.js';
import { makeDistortionCurve } from './fx.js';
import { initPatch } from '../patch.js';

const MAX_VOICES = 6; // matches the Circuit Tracks hardware: 6-voice polyphony per synth

// Guards NaN as well as range: Math.max(0, Math.min(127, NaN)) is NaN, which
// would silently poison any AudioParam it reaches, so non-finite values snap to 0.
const clamp127 = (v) => (Number.isFinite(v) ? Math.max(0, Math.min(127, v)) : 0);
const midiToHz = (n) => 440 * Math.pow(2, (n - 69) / 12);

function oscType(waveIndex) {
  const name = OSC_WAVEFORMS[waveIndex] ?? 'sawtooth';
  if (name.includes('sine')) return 'sine';
  if (name === 'triangle' || name.includes('triangle')) return 'triangle';
  if (name.includes('square') || name.includes('pulse')) return 'square';
  return 'sawtooth';
}

// --- LFO waveforms beyond the basic four (Nova-engine heritage) ---
// Rhythmic level sequences (unipolar/bipolar steps, one pattern per cycle).
const LFO_LEVEL_SEQ = {
  7: [1, 0, 1, 0, 1, 1, 0, 0],
  8: [1, 1, 0, 1, 0, 0, 1, 0],
  9: [1, 0, 0, 0, 1, 0, 1, 1],
  10: [1, 0, 1, 1, 0, 1, 0, 0],
  11: [1, 1, 1, 0, 0, 1, 0, 1],
  12: [1, 0, 0, 1, 0, 0, 1, 0],
  13: [1, 1, 0, 0, 1, 0, 1, 1],
  14: [1, -1],
  15: [1, -1, 1, -1, 1, 1, -1, -1],
  16: [1, 0, -1, 0],
  17: [1, 1, -1, -1],
  18: [1, -1, -1, 1],
  19: [1, 0.5, -0.5, -1],
  20: [1, -0.5, 1, -1, 0.5, -1],
  21: [0.5, 1, -1, -0.5],
};
// Pitch sequences in semitones (full scale = 1 octave, so a pitch-mod depth
// of ±1200 cents reproduces the intervals exactly).
const LFO_PITCH_SEQ = {
  22: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11], // chromatic
  23: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15], // chromatic 16
  24: [0, 2, 4, 5, 7, 9, 11, 12], // major
  25: [0, 4, 7, 11], // major 7
  26: [0, 3, 7, 10], // minor 7
  27: [0, 3, 7, 12], // min arp 1
  28: [0, 3, 7, 12, 7, 3], // min arp 2
  29: [0, 3, 6, 9], // diminished
  30: [12, 10, 8, 7, 5, 3, 2, 0], // dec minor (descending)
  31: [0, 3], // minor 3rd
  32: [0, 0, 12, 0], // pedal
  33: [0, 5], // 4ths
  34: [0, 5, 10, 3, 8, 1, 6, 11, 4, 9, 2, 7], // 4ths x12 (circle of 4ths)
  35: [0, 9, 2, 7], // 1625 maj
  36: [0, 8, 2, 7], // 1625 min
  37: [2, 7, 0, 0], // 2511
};

const lfoBufferCache = new Map();

// Build a one-second looped buffer holding one cycle of the LFO shape.
// S/H waves hold 64 random steps per cycle (played at rate/64 so each step
// lands at the LFO rate).
function lfoBuffer(ctx, wave) {
  const key = `${wave}:${ctx.sampleRate}`;
  if (lfoBufferCache.has(key)) return lfoBufferCache.get(key);
  const sr = ctx.sampleRate;
  const buf = ctx.createBuffer(1, sr, sr);
  const d = buf.getChannelData(0);

  if (wave <= 3) {
    // Basic shapes as buffers for exact phase at key-sync. The hardware saw
    // FALLS (starts at max, decays) — the percussive direction.
    for (let i = 0; i < sr; i++) {
      const ph = i / sr;
      if (wave === 0) d[i] = Math.sin(2 * Math.PI * ph);
      else if (wave === 1) d[i] = 1 - 4 * Math.abs(((ph + 0.25) % 1) - 0.5); // tri from 0 up
      else if (wave === 2) d[i] = 1 - 2 * ph; // falling saw: +1 -> -1
      else d[i] = ph < 0.5 ? 1 : -1;
    }
    lfoBufferCache.set(key, buf);
    return buf;
  }
  if (wave === 6) {
    // Piano envelope: exponential decay over the cycle.
    for (let i = 0; i < sr; i++) d[i] = Math.exp((-5 * i) / sr);
  } else {
    let values;
    if (wave === 4 || wave === 5) {
      values = Array.from({ length: 64 }, () => Math.random() * 2 - 1);
    } else if (LFO_LEVEL_SEQ[wave]) {
      values = LFO_LEVEL_SEQ[wave];
    } else if (LFO_PITCH_SEQ[wave]) {
      values = LFO_PITCH_SEQ[wave].map((s) => s / 12);
    } else {
      values = [0];
    }
    const stepLen = Math.floor(sr / values.length);
    const ramp = Math.min(64, stepLen >> 2); // short ramps avoid clicks
    for (let s = 0; s < values.length; s++) {
      const v = values[s];
      const prev = values[(s - 1 + values.length) % values.length];
      const base = s * stepLen;
      const end = s === values.length - 1 ? sr : base + stepLen;
      for (let i = base; i < end; i++) {
        const k = i - base;
        d[i] = k < ramp ? prev + ((v - prev) * k) / ramp : v;
      }
    }
  }
  lfoBufferCache.set(key, buf);
  return buf;
}

// Parameter scaling
const cutoffHz = (v) => 20 * Math.pow(1000, clamp127(v) / 127); // 20 Hz .. 20 kHz
const envSeconds = (v, max) => 0.002 + Math.pow(clamp127(v) / 127, 2.2) * max;
const lfoHz = (v) => 0.05 * Math.pow(600, clamp127(v) / 127); // 0.05 .. 30 Hz

let sharedNoiseBuffer = null;
function noiseBuffer(ctx) {
  if (!sharedNoiseBuffer || sharedNoiseBuffer.sampleRate !== ctx.sampleRate) {
    const len = ctx.sampleRate * 2;
    sharedNoiseBuffer = ctx.createBuffer(1, len, ctx.sampleRate);
    const d = sharedNoiseBuffer.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
  }
  return sharedNoiseBuffer;
}

export class SynthTrack {
  constructor(engine, trackId) {
    this.engine = engine;
    this.ctx = engine.ctx;
    this.trackId = trackId;
    this.voices = [];
    this.patch = null;
    this.macroPositions = new Array(8).fill(0);

    const ctx = this.ctx;
    this.voiceBus = ctx.createGain();
    this.voiceBus.gain.value = 0.5;

    // Distortion (dry/wet)
    this.distDry = ctx.createGain();
    this.distWet = ctx.createGain();
    this.shaper = ctx.createWaveShaper();
    this.shaper.oversample = '2x';
    this.distSum = ctx.createGain();
    this.voiceBus.connect(this.distDry);
    this.voiceBus.connect(this.shaper);
    this.shaper.connect(this.distWet);
    this.distDry.connect(this.distSum);
    this.distWet.connect(this.distSum);

    // Chorus (modulated delay, dry/wet)
    this.chorusDelay = ctx.createDelay(0.1);
    this.chorusDelay.delayTime.value = 0.02;
    this.chorusLfo = ctx.createOscillator();
    this.chorusLfo.frequency.value = 0.4;
    this.chorusLfoGain = ctx.createGain();
    this.chorusLfoGain.gain.value = 0.006;
    this.chorusLfo.connect(this.chorusLfoGain);
    this.chorusLfoGain.connect(this.chorusDelay.delayTime);
    this.chorusLfo.start();
    this.chorusWet = ctx.createGain();
    this.chorusWet.gain.value = 0;
    this.chorusSum = ctx.createGain();
    this.distSum.connect(this.chorusSum);
    this.distSum.connect(this.chorusDelay);
    this.chorusDelay.connect(this.chorusWet);
    this.chorusWet.connect(this.chorusSum);

    // 3-band EQ
    this.eqBass = ctx.createBiquadFilter();
    this.eqBass.type = 'lowshelf';
    this.eqMid = ctx.createBiquadFilter();
    this.eqMid.type = 'peaking';
    this.eqTreble = ctx.createBiquadFilter();
    this.eqTreble.type = 'highshelf';
    this.chorusSum.connect(this.eqBass);
    this.eqBass.connect(this.eqMid);
    this.eqMid.connect(this.eqTreble);
    this.eqTreble.connect(engine.tracks[trackId].input);

    // Track LFOs: a stable output gain per LFO so voice mod-routings stay
    // connected while the internal source is rebuilt on waveform changes.
    this.lfos = [1, 2].map(() => ({
      out: ctx.createGain(),
      src: null,
      wave: -1,
    }));

    this.setPatch(initPatch());
  }

  setPatch(patch) {
    this.patch = patch;
    // Macro knob positions come from the patch (knob value byte).
    this.macroPositions = patch.macros.map((m) => m.position);
    this.applyPatchFx();
    this.applyLfos();
    this.updateActiveVoices();
  }

  // Live-update currently-sounding voices so held notes reflect patch edits in
  // real time. Time-shaped stages already in flight (envelope attack/decay)
  // can't be rewound, but the steady-state controls — filter cutoff/resonance,
  // oscillator/noise levels, fine detune — track edits while a key is held.
  updateActiveVoices() {
    if (!this.voices.length) return;
    const now = this.ctx.currentTime;
    const cutoff = cutoffHz(this.param('filter_frequency'));
    const reso = this.param('filter_resonance') / 127;
    const l1 = this.param('osc1_level') / 127;
    const l2 = this.param('osc2_level') / 127;
    const ln = this.param('noise_level') / 127;
    const sum = Math.max(0.4, l1 + l2 + ln);
    const osc1Cents = this.param('osc1_cents') - 64;
    const osc2Cents = this.param('osc2_cents') - 64;
    for (const voice of this.voices) {
      if (voice.released) continue;
      for (const f of voice.filters) {
        f.frequency.setTargetAtTime(Math.min(20000, cutoff * voice.envBoost), now, 0.02);
        f.Q.setTargetAtTime(0.5 + reso * 14, now, 0.02);
      }
      voice.g1?.gain.setTargetAtTime(l1 / sum, now, 0.02);
      voice.g2?.gain.setTargetAtTime(l2 / sum, now, 0.02);
      voice.gn?.gain.setTargetAtTime(ln / sum, now, 0.02);
      // Intrinsic detune; mod-matrix sources stay summed on top of it.
      voice.osc1.detune.setTargetAtTime(osc1Cents, now, 0.02);
      voice.osc2.detune.setTargetAtTime(osc2Cents, now, 0.02);
    }
  }

  // Summed macro contribution for a destination name. Each target ramps from
  // 0 to depth×2 as the knob travels its start→end range (depth ±63 maps to
  // a full ±126 parameter swing, so e.g. filter macros sweep 0→127).
  macroContribution(name, macroOverrides = null) {
    let v = 0;
    for (let k = 0; k < 8; k++) {
      const macro = this.patch.macros[k];
      if (!macro) continue;
      const pos = macroOverrides?.[k] ?? this.macroPositions[k];
      for (const t of macro.targets) {
        if (MACRO_DESTINATIONS[t.destination] !== name) continue;
        const t01 = Math.min(1, Math.max(0, (pos - t.start) / (t.end - t.start)));
        v += t01 * t.depth * 2;
      }
    }
    return v;
  }

  // Effective parameter = patch base + macro contributions (macros ADD).
  param(name, macroOverrides = null) {
    const v = (this.patch.params[name] ?? 0) + this.macroContribution(name, macroOverrides);
    return clamp127(Math.round(v));
  }

  setMacro(idx, value) {
    this.macroPositions[idx] = clamp127(value);
    this.applyPatchFx();
    this.applyLfos();
    // Live-update sounding voices for the most audible targets.
    const now = this.ctx.currentTime;
    const cutoff = cutoffHz(this.param('filter_frequency'));
    const reso = this.param('filter_resonance') / 127;
    for (const voice of this.voices) {
      for (const f of voice.filters) {
        f.frequency.setTargetAtTime(Math.min(20000, cutoff * voice.envBoost), now, 0.02);
        f.Q.setTargetAtTime(0.5 + reso * 14, now, 0.02);
      }
    }
  }

  applyPatchFx() {
    const now = this.ctx.currentTime;
    const distLevel = this.param('distortion_level') / 127;
    this.shaper.curve = makeDistortionCurve(distLevel, this.patch.params.distortion_type ?? 0);
    this.distWet.gain.setTargetAtTime(distLevel, now, 0.02);
    this.distDry.gain.setTargetAtTime(1 - distLevel * 0.7, now, 0.02);
    const chorus = this.param('chorus_level') / 127;
    this.chorusWet.gain.setTargetAtTime(chorus * 0.7, now, 0.02);

    const p = this.patch.params;
    const shelfFreq = (v, lo, hi) => lo * Math.pow(hi / lo, clamp127(v) / 127);
    const shelfGain = (v) => ((clamp127(v) - 64) / 64) * 12;
    this.eqBass.frequency.value = shelfFreq(p.eq_bass_frequency ?? 64, 40, 400);
    this.eqBass.gain.value = shelfGain(p.eq_bass_level ?? 64);
    this.eqMid.frequency.value = shelfFreq(p.eq_mid_frequency ?? 64, 250, 4000);
    this.eqMid.gain.value = shelfGain(p.eq_mid_level ?? 64);
    this.eqMid.Q.value = 0.8;
    this.eqTreble.frequency.value = shelfFreq(p.eq_treble_frequency ?? 64, 1500, 12000);
    this.eqTreble.gain.value = shelfGain(p.eq_treble_level ?? 64);
  }

  // Effective LFO frequency: a non-zero rate-sync field locks the cycle to a
  // musical division of the project tempo; otherwise the free rate is used.
  lfoEffectiveHz(n) {
    const sync = this.patch.params[`lfo${n + 1}_rate_sync`] ?? 0;
    if (sync > 0) return 1 / (lfoSyncToBeats(sync) * (60 / (this.engine.bpm || 120)));
    return lfoHz(this.param(`lfo${n + 1}_rate`));
  }

  // Build an unstarted LFO source node. All shapes use one-cycle buffers so
  // a key-synced start lands on the exact waveform phase (sawtooth = falling,
  // as on hardware).
  makeLfoNode(wave, hz, { loop = true } = {}) {
    const rate = wave === 4 || wave === 5 ? hz / 64 : hz;
    const src = this.ctx.createBufferSource();
    src.buffer = lfoBuffer(this.ctx, wave);
    src.loop = loop;
    src.playbackRate.value = rate;
    return src;
  }

  applyLfos() {
    if (!this.patch) return;
    for (let n = 0; n < 2; n++) {
      const lfo = this.lfos[n];
      const wave = this.patch.params[`lfo${n + 1}_waveform`] ?? 0;
      const hz = this.lfoEffectiveHz(n);
      const rate = wave === 4 || wave === 5 ? hz / 64 : hz;

      if (lfo.wave !== wave || !lfo.src) {
        if (lfo.src) {
          try { lfo.src.stop(); lfo.src.disconnect(); } catch { /* gone */ }
        }
        const src = this.makeLfoNode(wave, hz);
        src.connect(lfo.out);
        src.start();
        lfo.src = src;
        lfo.wave = wave;
      } else {
        lfo.src.playbackRate.setTargetAtTime(rate, this.ctx.currentTime, 0.02);
      }
    }
  }

  // Schedule a note. If durationSec is null, the note holds until noteOff(note).
  noteOn(time, midiNote, velocity = 96, durationSec = null, macroOverrides = null) {
    const ctx = this.ctx;
    const P = (name) => this.param(name, macroOverrides);

    if (this.voices.length >= MAX_VOICES) {
      this.killVoice(this.voices[0], time);
    }
    const mono = (this.patch.params.polyphony_mode ?? 2) < 2;
    let glideFrom = null;
    if (mono) {
      // Portamento: glide from the previous mono note's pitch.
      const prev = this.voices[this.voices.length - 1];
      const portaRate = P('portamento_rate');
      if (prev && portaRate > 0 && prev.note !== midiNote) {
        glideFrom = { note: prev.note, seconds: Math.pow(portaRate / 127, 2) * 0.8 };
      }
      for (const v of [...this.voices]) this.releaseVoice(v, time);
    }

    const osc1Semi = P('osc1_semitones') - 64;
    const osc1Cents = P('osc1_cents') - 64;
    const osc2Semi = P('osc2_semitones') - 64;
    const osc2Cents = P('osc2_cents') - 64;

    const osc1 = ctx.createOscillator();
    osc1.type = oscType(this.patch.params.osc1_wave ?? 2);
    osc1.detune.value = osc1Cents;
    const osc2 = ctx.createOscillator();
    osc2.type = oscType(this.patch.params.osc2_wave ?? 2);
    osc2.detune.value = osc2Cents;
    if (glideFrom) {
      osc1.frequency.setValueAtTime(midiToHz(glideFrom.note + osc1Semi), time);
      osc1.frequency.exponentialRampToValueAtTime(midiToHz(midiNote + osc1Semi), time + glideFrom.seconds);
      osc2.frequency.setValueAtTime(midiToHz(glideFrom.note + osc2Semi), time);
      osc2.frequency.exponentialRampToValueAtTime(midiToHz(midiNote + osc2Semi), time + glideFrom.seconds);
    } else {
      osc1.frequency.value = midiToHz(midiNote + osc1Semi);
      osc2.frequency.value = midiToHz(midiNote + osc2Semi);
    }

    const g1 = ctx.createGain();
    const g2 = ctx.createGain();
    const gn = ctx.createGain();
    const l1 = P('osc1_level') / 127;
    const l2 = P('osc2_level') / 127;
    const ln = P('noise_level') / 127;
    const sum = Math.max(0.4, l1 + l2 + ln);
    g1.gain.value = l1 / sum;
    g2.gain.value = l2 / sum;
    gn.gain.value = ln / sum;

    const noise = ctx.createBufferSource();
    noise.buffer = noiseBuffer(ctx);
    noise.loop = true;

    // Filter: 12 dB types use one biquad, 24 dB types use two in series.
    const ftype = this.patch.params.filter_type ?? 0;
    const biquadType = ftype <= 1 ? 'lowpass' : ftype <= 3 ? 'bandpass' : 'highpass';
    const stages = ftype === 1 || ftype === 3 || ftype === 5 ? 2 : 1;
    const filters = [];
    for (let i = 0; i < stages; i++) {
      const f = ctx.createBiquadFilter();
      f.type = biquadType;
      filters.push(f);
    }

    const amp = ctx.createGain();
    amp.gain.value = 0;

    osc1.connect(g1);
    osc2.connect(g2);
    noise.connect(gn);
    let node = ctx.createGain();
    g1.connect(node);
    g2.connect(node);
    gn.connect(node);
    for (const f of filters) {
      node.connect(f);
      node = f;
    }
    node.connect(amp);
    amp.connect(this.voiceBus);

    // --- Envelope 1 (amp) ---
    const a = envSeconds(P('env1_attack'), 3);
    const dcy = envSeconds(P('env1_decay'), 6);
    const sus = P('env1_sustain') / 127;
    const rel = envSeconds(P('env1_release'), 8);
    const velAmt = P('env1_velocity') / 127;
    const velScale = (1 - velAmt) + velAmt * (velocity / 127);
    const peak = 0.9 * velScale;

    amp.gain.setValueAtTime(0, time);
    amp.gain.linearRampToValueAtTime(peak, time + a);
    amp.gain.setTargetAtTime(peak * sus, time + a, Math.max(0.01, dcy / 3));
    // Shape parameters so a release can re-anchor the envelope mid-flight
    // (a gate shorter than the attack must not silence the note).
    const ampEnv = { t0: time, del: 0, a: Math.max(0.001, a), peak, sus, dTau: Math.max(0.01, dcy / 3) };

    // --- Envelope 2 (filter) ---
    const baseCut = cutoffHz(P('filter_frequency'));
    const envDepth = (P('env2_to_filter_freq') - 64) / 63; // -1..+1
    const fa = envSeconds(P('env2_attack'), 3);
    const fd = envSeconds(P('env2_decay'), 6);
    const fs = P('env2_sustain') / 127;
    const reso = P('filter_resonance') / 127;
    const peakCut = Math.min(20000, Math.max(20, baseCut * Math.pow(2, envDepth * 5)));
    const susCut = Math.min(20000, Math.max(20, baseCut * Math.pow(2, envDepth * 5 * fs)));
    for (const f of filters) {
      f.Q.value = 0.5 + reso * 14;
      f.frequency.setValueAtTime(Math.max(20, baseCut), time);
      f.frequency.linearRampToValueAtTime(peakCut, time + fa);
      f.frequency.setTargetAtTime(susCut, time + fa, Math.max(0.01, fd / 3));
    }

    // --- Mod matrix ---
    // Sources: 4 = velocity (static), 6/8 = LFO 1/2 unipolar (0..depth),
    // 7/9 = LFO 1/2 bipolar, 10/11/12 = env amp / env filter / env 3
    // (unipolar 0..1, generated per voice). A key-synced LFO (flags bit 1)
    // gets a fresh per-voice source started at note time; one-shot (bit 0)
    // plays one cycle.
    const modConnections = [];
    const modSources = [];
    const envSources = []; // {cs, sustain, releaseTau} — released at note-off
    const makeEnvSource = (prefix) => {
      const cs = ctx.createConstantSource();
      cs.offset.value = 0;
      const del = prefix === 'env3' ? envSeconds(P('env3_delay'), 2) : 0;
      const eA = envSeconds(P(`${prefix}_attack`), 3);
      const eD = envSeconds(P(`${prefix}_decay`), 6);
      const eS = P(`${prefix}_sustain`) / 127;
      const eR = envSeconds(P(`${prefix}_release`), 8);
      cs.offset.setValueAtTime(0, time);
      cs.offset.setValueAtTime(0, time + del);
      cs.offset.linearRampToValueAtTime(1, time + del + eA);
      cs.offset.setTargetAtTime(eS, time + del + eA, Math.max(0.01, eD / 3));
      cs.start(time);
      modSources.push(cs);
      envSources.push({
        cs, releaseTau: Math.max(0.008, eR / 4),
        t0: time, del, a: Math.max(0.001, eA), peak: 1, sus: eS, dTau: Math.max(0.01, eD / 3),
      });
      return cs;
    };

    for (let slotIdx = 0; slotIdx < this.patch.modMatrix.length; slotIdx++) {
      const slot = this.patch.modMatrix[slotIdx];
      // Macros can target a slot's depth (destinations 51-70): the stored
      // depth is often 0 with the whole routing driven by a knob (e.g.
      // PolterGeist macro 7 raises LFO2->Frequency for the filter chop).
      const macroDepth = this.macroContribution(`mod${slotIdx + 1}_depth`, macroOverrides);
      const slotDepth = Math.max(-64, Math.min(63, slot.depth - 64 + macroDepth));
      if (slotDepth === 0 || (slot.source1 === 0 && slot.source2 === 0)) continue;
      const src = slot.source1 || slot.source2;
      let lfoIdx = null;
      let envPrefix = null;
      let staticValue = null;
      if (src === 6 || src === 7) lfoIdx = 0;
      else if (src === 8 || src === 9) lfoIdx = 1;
      else if (src === 10) envPrefix = 'env1';
      else if (src === 11) envPrefix = 'env2';
      else if (src === 12) envPrefix = 'env3';
      else if (src === 4) staticValue = velocity / 127;
      else continue;
      const unipolar = src === 6 || src === 8;

      // Destination -> [scale, AudioParams]
      const depth = slotDepth / 63; // -1..+1
      const dest = slot.destination;
      let scale;
      let targets;
      if (dest === 0 || dest === 1 || dest === 2) {
        // Pitch: squared depth curve. Full depth (±63) spans ±1 octave so the
        // arp LFO waves land on exact semitones, but factory shimmer patches
        // (Ethereal +2, PolterGeist +7, NeonLights -2) get cents, not a warble.
        scale = Math.sign(depth) * depth * depth * 1200;
        targets = [];
        if (dest !== 2) targets.push(osc1.detune);
        if (dest !== 1) targets.push(osc2.detune);
      } else if (dest === 12) {
        scale = depth * 4800; // filter freq, ±4 octaves via detune
        targets = filters.map((f) => f.detune);
      } else if (dest === 13) {
        scale = depth * 14; // resonance (Q swing)
        targets = filters.map((f) => f.Q);
      } else if (dest === 7) {
        scale = depth; // osc levels: full swing of the voice mixer gain
        targets = [g1.gain];
      } else if (dest === 8) {
        scale = depth;
        targets = [g2.gain];
      } else if (dest === 9) {
        scale = depth;
        targets = [gn.gain];
      } else {
        continue;
      }

      let sourceNode = null;
      if (lfoIdx != null) {
        const flags = this.patch.params[`lfo${lfoIdx + 1}_flags`] ?? 0;
        if ((flags & 2) !== 0) { // key sync: per-voice phase-exact source
          const node = this.makeLfoNode(this.patch.params[`lfo${lfoIdx + 1}_waveform`] ?? 0,
            this.lfoEffectiveHz(lfoIdx), { loop: (flags & 1) === 0 });
          node.start(time);
          modSources.push(node);
          sourceNode = node;
        } else {
          sourceNode = this.lfos[lfoIdx].out;
        }
      } else if (envPrefix) {
        sourceNode = makeEnvSource(envPrefix);
      } else {
        // Velocity: a static offset.
        const cs = ctx.createConstantSource();
        cs.offset.value = staticValue;
        cs.start(time);
        modSources.push(cs);
        sourceNode = cs;
      }

      const g = ctx.createGain();
      g.gain.value = unipolar ? scale * 0.5 : scale;
      sourceNode.connect(g);
      for (const t of targets) g.connect(t);
      modConnections.push(g);
      if (unipolar) {
        // Shift the bipolar LFO up: out = scale * (lfo + 1) / 2.
        const offset = ctx.createConstantSource();
        offset.offset.value = scale * 0.5;
        for (const t of targets) offset.connect(t);
        offset.start(time);
        modSources.push(offset);
      }
    }

    osc1.start(time);
    osc2.start(time);
    noise.start(time);

    const voice = {
      note: midiNote, time, osc1, osc2, noise, amp, filters, g1, g2, gn,
      modConnections, modSources, envSources,
      rel, baseCut, ampEnv, envBoost: 1, released: false,
    };
    this.voices.push(voice);

    if (durationSec != null) {
      this.releaseVoice(voice, time + Math.max(0.01, durationSec));
    }
    return voice;
  }

  noteOff(midiNote, time = null) {
    const t = time ?? this.ctx.currentTime;
    for (const v of this.voices) {
      if (v.note === midiNote && !v.released) this.releaseVoice(v, t);
    }
  }

  // DAHDS envelope value at time t (before any release), from stored shape.
  static envValueAt(e, t) {
    const tA = e.t0 + (e.del ?? 0);
    if (t <= tA) return 0;
    if (t < tA + e.a) return e.peak * ((t - tA) / e.a);
    const dt = t - (tA + e.a);
    return e.peak * e.sus + e.peak * (1 - e.sus) * Math.exp(-dt / e.dTau);
  }

  releaseVoice(voice, time) {
    // Sequenced voices are "released" at schedule time with a future
    // timestamp; allow re-releasing earlier so transport stop can cut a
    // long tied note that is still sounding.
    if (voice.released && time >= voice.releaseAt) return;
    voice.released = true;
    voice.releaseAt = time;
    const { amp, rel } = voice;
    // cancelScheduledValues would kill an attack ramp still in flight (its
    // event time lies past the release), leaving the gain stuck at 0 — any
    // gate shorter than the attack would be silent. Re-anchor at the level
    // the envelope analytically reaches at release time, then release.
    amp.gain.cancelScheduledValues(time);
    amp.gain.setValueAtTime(SynthTrack.envValueAt(voice.ampEnv, time), time);
    amp.gain.setTargetAtTime(0, time, Math.max(0.008, rel / 4));
    for (const e of voice.envSources ?? []) {
      e.cs.offset.cancelScheduledValues(time);
      e.cs.offset.setValueAtTime(SynthTrack.envValueAt(e, time), time);
      e.cs.offset.setTargetAtTime(0, time, e.releaseTau);
    }
    const stopAt = time + rel + 0.3;
    voice.osc1.stop(stopAt);
    voice.osc2.stop(stopAt);
    voice.noise.stop(stopAt);
    voice.osc1.onended = () => this.disposeVoice(voice);
  }

  // Steal the oldest voice when over MAX_VOICES. Reclaim it SYNCHRONOUSLY:
  // remove it from the array and orphan it from the bus right now, rather than
  // waiting on a scheduled stop + onended. Sequenced notes are marked released
  // the instant they're scheduled, with osc.stop() set ~8 s out (releaseAt +
  // long release tail). Trying to reschedule that stop earlier proved
  // unreliable, so voices piled up (100+) until the render thread choked and
  // the context clock stalled. disposeVoice() disconnects the voice's output,
  // so an orphaned subgraph (no path to destination) stops being rendered
  // immediately — freeing CPU and enforcing the cap regardless of stop timing.
  // A brief click on a stolen voice is the standard voice-stealing trade-off,
  // and only happens under heavy polyphony (≥ MAX_VOICES on one track).
  killVoice(voice, time) {
    voice.released = true;
    const t = Math.max(this.ctx.currentTime, voice.time) + 0.001;
    try {
      voice.osc1.stop(t);
      voice.osc2.stop(t);
      voice.noise.stop(t);
    } catch { /* not started / already stopped */ }
    this.disposeVoice(voice);
  }

  disposeVoice(voice) {
    const i = this.voices.indexOf(voice);
    if (i >= 0) this.voices.splice(i, 1);
    for (const g of voice.modConnections) {
      try { g.disconnect(); } catch { /* already gone */ }
    }
    for (const s of voice.modSources ?? []) {
      try { s.stop(); } catch { /* not started / already stopped */ }
      try { s.disconnect(); } catch { /* already gone */ }
    }
    try { voice.amp.disconnect(); } catch { /* already gone */ }
  }

  allNotesOff(time = null) {
    const t = time ?? this.ctx.currentTime;
    for (const v of [...this.voices]) this.releaseVoice(v, t);
  }
}
