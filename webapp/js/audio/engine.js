// AudioEngine: master chain, per-track channel strips, FX buses, sidechain.
import { ReverbBus, DelayBus } from './fx.js';
import { TRACKS } from '../constants.js';

class TrackChannel {
  constructor(ctx, engine) {
    this.ctx = ctx;
    this.input = ctx.createGain(); // instruments connect here
    this.duck = ctx.createGain(); // sidechain ducking
    this.level = ctx.createGain();
    this.pan = ctx.createStereoPanner();
    this.revSend = ctx.createGain();
    this.dlySend = ctx.createGain();
    this.revSend.gain.value = 0;
    this.dlySend.gain.value = 0;
    this.muteGain = ctx.createGain();

    this.input.connect(this.duck);
    this.duck.connect(this.level);
    this.level.connect(this.pan);
    this.pan.connect(this.muteGain);
    this.muteGain.connect(engine.masterIn);
    // Sends are post-level, pre-pan.
    this.level.connect(this.revSend);
    this.level.connect(this.dlySend);
    this.revSend.connect(engine.reverb.input);
    this.dlySend.connect(engine.delay.input);

    this.levelValue = 100;
    this.panValue = 64;
    this.revValue = 0;
    this.dlyValue = 0;
    this.muted = false;
    this.applyAll();
  }

  static levelGain(v) { return Math.pow(v / 127, 1.5); }

  setLevel(v, time = null) {
    this.levelValue = v;
    const t = time ?? this.ctx.currentTime;
    this.level.gain.setTargetAtTime(TrackChannel.levelGain(v), t, 0.01);
  }

  setPan(v, time = null) {
    this.panValue = v;
    const t = time ?? this.ctx.currentTime;
    this.pan.pan.setTargetAtTime((v - 64) / 63, t, 0.01);
  }

  setReverbSend(v, time = null) {
    this.revValue = v;
    this.revSend.gain.setTargetAtTime(Math.pow(v / 127, 1.2), time ?? this.ctx.currentTime, 0.01);
  }

  setDelaySend(v, time = null) {
    this.dlyValue = v;
    this.dlySend.gain.setTargetAtTime(Math.pow(v / 127, 1.2), time ?? this.ctx.currentTime, 0.01);
  }

  setMuted(m) {
    this.muted = m;
    this.muteGain.gain.setTargetAtTime(m ? 0 : 1, this.ctx.currentTime, 0.005);
  }

  applyAll() {
    this.setLevel(this.levelValue);
    this.setPan(this.panValue);
    this.setReverbSend(this.revValue);
    this.setDelaySend(this.dlyValue);
    this.setMuted(this.muted);
  }
}

export class AudioEngine {
  constructor() {
    this.ctx = new (window.AudioContext || window.webkitAudioContext)({ latencyHint: 'interactive' });

    this.masterVolume = this.ctx.createGain();
    this.masterVolume.gain.value = 0.8;
    this.masterFilter = this.ctx.createBiquadFilter();
    this.masterFilter.type = 'lowpass';
    this.masterFilter.frequency.value = 20000;
    this.limiter = this.ctx.createDynamicsCompressor();
    this.limiter.threshold.value = -8;
    this.limiter.ratio.value = 8;
    this.limiter.attack.value = 0.003;
    this.limiter.release.value = 0.12;

    // FX buses feed masterIn so the master filter/volume affect them too.
    this.masterIn = this.ctx.createGain();
    this.fxEnable = this.ctx.createGain(); // scales bus inputs when bypassed
    this.masterIn.connect(this.masterFilter);
    this.masterFilter.connect(this.masterVolume);
    this.masterVolume.connect(this.limiter);
    this.limiter.connect(this.ctx.destination);

    this.reverb = new ReverbBus(this.ctx, this.masterIn);
    this.delay = new DelayBus(this.ctx, this.masterIn);

    this.tracks = TRACKS.map(() => new TrackChannel(this.ctx, this));
    this.fxBypass = false;
    this.bpm = 120; // kept in sync by the sequencer (LFO rate sync needs it)
    // sidechain config per synth/MIDI track id (0,1,2,3): {source: drumTrack 0-3|null, attack, hold, decay, depth}
    this.sidechain = [null, null, null, null];
  }

  resume() {
    if (this.ctx.state !== 'running') return this.ctx.resume();
    return Promise.resolve();
  }

  now() { return this.ctx.currentTime; }

  setMasterVolume(v127) {
    this.masterVolume.gain.setTargetAtTime(Math.pow(v127 / 127, 1.5), this.ctx.currentTime, 0.01);
  }

  // Master filter knob: 0-63 lowpass sweep, 64 off, 65-127 highpass sweep.
  setMasterFilter(v127) {
    const now = this.ctx.currentTime;
    if (v127 < 64) {
      this.masterFilter.type = 'lowpass';
      const norm = v127 / 63; // 0 = fully closed, 1 = open
      const freq = 60 * Math.pow(20000 / 60, norm);
      this.masterFilter.frequency.setTargetAtTime(freq, now, 0.02);
      this.masterFilter.Q.value = 1.2;
    } else if (v127 === 64) {
      this.masterFilter.type = 'lowpass';
      this.masterFilter.frequency.setTargetAtTime(20000, now, 0.02);
      this.masterFilter.Q.value = 0.0001;
    } else {
      this.masterFilter.type = 'highpass';
      const norm = (v127 - 65) / 62;
      const freq = 20 * Math.pow(12000 / 20, norm);
      this.masterFilter.frequency.setTargetAtTime(freq, now, 0.02);
      this.masterFilter.Q.value = 1.2;
    }
  }

  setFxBypass(bypass) {
    this.fxBypass = bypass;
    const t = this.ctx.currentTime;
    this.reverb.wet.gain.setTargetAtTime(bypass ? 0 : 0.8, t, 0.01);
    this.delay.out.gain.setTargetAtTime(bypass ? 0 : 0.9, t, 0.01);
  }

  configureSidechain(trackId, cfg) {
    // trackId 0-3 (S1,S2,M1,M2). cfg: {preset, source, attack, hold, decay, depth}
    // The curve values are taken as given: the project model already holds
    // the preset's curve (the FX view and the agent copy SIDECHAIN_PRESETS in
    // when a preset is picked), so explicit overrides survive a reload.
    if (!cfg || cfg.preset === 0 || cfg.source >= 4) {
      this.sidechain[trackId] = null;
      return;
    }
    const { source, attack, hold, decay, depth } = cfg;
    this.sidechain[trackId] = { source, attack, hold, decay, depth };
  }

  // Called by the drum engine whenever drum track (0-3) triggers at `time`.
  triggerSidechain(drumTrack, time) {
    for (let t = 0; t < 4; t++) {
      const sc = this.sidechain[t];
      if (!sc || sc.source !== drumTrack) continue;
      const chId = t < 2 ? t : t; // S1,S2,M1,M2 are track channels 0-3
      const g = this.tracks[chId].duck.gain;
      const depth = sc.depth / 127;
      const attack = 0.001 + (sc.attack / 127) * 0.05;
      const hold = 0.001 + (sc.hold / 127) * 0.2;
      const decay = 0.01 + (sc.decay / 127) * 0.5;
      g.cancelScheduledValues(time);
      g.setValueAtTime(1, time);
      g.linearRampToValueAtTime(1 - depth, time + attack);
      g.setValueAtTime(1 - depth, time + attack + hold);
      g.linearRampToValueAtTime(1, time + attack + hold + decay);
    }
  }
}
