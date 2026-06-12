// Drum playback: 4 tracks playing the 64-sample bank with per-track
// pitch/decay/distortion/EQ and per-hit velocity/sample-flip/p-locks.
import { makeDistortionCurve } from './fx.js';

const clamp127 = (v) => Math.max(0, Math.min(127, v));

export class DrumEngine {
  constructor(engine) {
    this.engine = engine;
    this.ctx = engine.ctx;
    this.buffers = new Array(64).fill(null);
    this.names = new Array(64).fill('');
    this.loaded = false;

    // Per drum track (0-3): config + distortion/EQ sub-chain feeding the
    // track channel (tracks 4-7 in the engine).
    this.tracks = [];
    for (let i = 0; i < 4; i++) {
      const ctx = this.ctx;
      const input = ctx.createGain();
      const shaper = ctx.createWaveShaper();
      shaper.oversample = '2x';
      const wet = ctx.createGain();
      const dry = ctx.createGain();
      const eqLow = ctx.createBiquadFilter();
      eqLow.type = 'lowshelf';
      eqLow.frequency.value = 250;
      const eqHigh = ctx.createBiquadFilter();
      eqHigh.type = 'highshelf';
      eqHigh.frequency.value = 3000;
      input.connect(dry);
      input.connect(shaper);
      shaper.connect(wet);
      const sum = ctx.createGain();
      dry.connect(sum);
      wet.connect(sum);
      sum.connect(eqLow);
      eqLow.connect(eqHigh);
      eqHigh.connect(engine.tracks[4 + i].input);
      this.tracks.push({
        input, shaper, wet, dry, eqLow, eqHigh,
        config: { patchSelect: [0, 2, 4, 8][i], level: 100, pitch: 64, decay: 127, distortion: 0, eq: 64, pan: 64, reverbSend: 0, delaySend: 0 },
      });
      this.applyConfig(i, this.tracks[i].config);
    }
  }

  // Load the 64-sample bank of a Components pack from its index.json
  // ({ samples: [{ name, url }] }), fetched relative to packBase.
  async loadSampleBank(packBase) {
    const res = await fetch(packBase + 'index.json');
    if (!res.ok) throw new Error(`No pack index at ${packBase}`);
    const index = await res.json();
    return this.loadSampleBankFrom(index, async (url) => {
      const r = await fetch(packBase + url);
      if (!r.ok) throw new Error(`Failed to load sample ${url}`);
      return r.arrayBuffer();
    });
  }

  // Load a bank from an already-parsed pack index; getBuf(url) resolves a
  // pack-relative URL to an ArrayBuffer (fetch, local File, zip entry, ...).
  async loadSampleBankFrom(index, getBuf) {
    if (!Array.isArray(index.samples) || index.samples.length === 0) {
      throw new Error('Pack index has no samples');
    }
    const jobs = index.samples.slice(0, 64).map(async (s, i) => {
      this.buffers[i] = await this.ctx.decodeAudioData(await getBuf(s.url));
      this.names[i] = s.name;
    });
    await Promise.all(jobs);
    this.loaded = true;
  }

  sampleName(idx) {
    return this.names[idx] ?? '';
  }

  applyConfig(trackIdx, config) {
    const t = this.tracks[trackIdx];
    t.config = { ...t.config, ...config };
    const ch = this.engine.tracks[4 + trackIdx];
    ch.setLevel(t.config.level);
    ch.setPan(t.config.pan);
    ch.setReverbSend(t.config.reverbSend);
    ch.setDelaySend(t.config.delaySend);
    this.applyDistortionEq(trackIdx, t.config.distortion, t.config.eq);
  }

  applyDistortionEq(trackIdx, distortion, eq, time = null) {
    const t = this.tracks[trackIdx];
    const tm = time ?? this.ctx.currentTime;
    const d = clamp127(distortion) / 127;
    t.shaper.curve = makeDistortionCurve(d, 0);
    t.wet.gain.setTargetAtTime(d, tm, 0.01);
    t.dry.gain.setTargetAtTime(1 - d * 0.7, tm, 0.01);
    // EQ knob is a tilt: <64 darker, >64 brighter.
    const tilt = ((clamp127(eq) - 64) / 64) * 10; // dB
    t.eqLow.gain.setTargetAtTime(-tilt, tm, 0.01);
    t.eqHigh.gain.setTargetAtTime(tilt, tm, 0.01);
  }

  // Apply track-level lock values (distortion/eq/level/pan/sends) at `time`,
  // independent of whether the step has a hit.
  applyStepAutomation(trackIdx, locks, time) {
    const cfg = this.tracks[trackIdx].config;
    const ch = this.engine.tracks[4 + trackIdx];
    if (locks.distortion != null || locks.eq != null) {
      this.applyDistortionEq(trackIdx, locks.distortion ?? cfg.distortion, locks.eq ?? cfg.eq, time);
    }
    if (locks.level != null) ch.setLevel(locks.level, time);
    if (locks.pan != null) ch.setPan(locks.pan, time);
    if (locks.reverb_send != null) ch.setReverbSend(locks.reverb_send, time);
    if (locks.delay_send != null) ch.setDelaySend(locks.delay_send, time);
  }

  // Trigger a drum hit. locks may override pitch/decay (per-hit) and
  // distortion/eq/level/pan/sends (track-level automation).
  play(trackIdx, time, velocity = 96, sampleOverride = null, locks = null) {
    const t = this.tracks[trackIdx];
    const cfg = t.config;
    const idx = sampleOverride != null && sampleOverride !== 0xff
      ? clamp127(sampleOverride) % 64
      : cfg.patchSelect % 64;
    const buffer = this.buffers[idx];
    if (!buffer) return;

    const pitch = locks?.pitch ?? cfg.pitch;
    const decay = locks?.decay ?? cfg.decay;
    if (locks) {
      const ch = this.engine.tracks[4 + trackIdx];
      if (locks.distortion != null || locks.eq != null) {
        this.applyDistortionEq(trackIdx, locks.distortion ?? cfg.distortion, locks.eq ?? cfg.eq, time);
      }
      if (locks.level != null) ch.setLevel(locks.level, time);
      if (locks.pan != null) ch.setPan(locks.pan, time);
      if (locks.reverb_send != null) ch.setReverbSend(locks.reverb_send, time);
      if (locks.delay_send != null) ch.setDelaySend(locks.delay_send, time);
    }

    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    const semis = (clamp127(pitch) - 64) * 0.378; // ±24 semitones across the range
    src.playbackRate.value = Math.pow(2, semis / 12);

    const gain = this.ctx.createGain();
    const velGain = Math.pow(clamp127(velocity) / 127, 1.5);
    gain.gain.setValueAtTime(velGain, time);
    src.connect(gain);
    gain.connect(t.input);
    src.start(time);
    // Decay shortens the sample tail; 127 = play in full. (stop() must be
    // scheduled after start() or the node throws InvalidStateError.)
    if (decay < 124) {
      const tail = 0.02 + Math.pow(decay / 127, 1.5) * 1.8;
      gain.gain.setValueAtTime(velGain, time + tail * 0.5);
      gain.gain.exponentialRampToValueAtTime(0.001, time + tail);
      src.stop(time + tail + 0.05);
    }
    src.onended = () => { try { gain.disconnect(); } catch { /* gone */ } };

    this.engine.triggerSidechain(trackIdx, time);
  }
}
