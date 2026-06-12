// Global FX buses: reverb (convolver with generated IR) and stereo delay.
import { DELAY_LR_RATIOS, delaySyncToBeats } from '../constants.js';

export function buildReverbIR(ctx, decay127, damping127) {
  // decay 0-127 -> 0.3..6 s tail; damping 0-127 -> progressively darker tail.
  const duration = 0.3 + Math.pow(decay127 / 127, 1.6) * 5.7;
  const rate = ctx.sampleRate;
  const len = Math.max(1, Math.floor(duration * rate));
  const ir = ctx.createBuffer(2, len, rate);
  const dampNorm = damping127 / 127;
  for (let ch = 0; ch < 2; ch++) {
    const data = ir.getChannelData(ch);
    let lp = 0;
    // One-pole lowpass on the noise, coefficient tightens along the tail
    // to mimic high-frequency damping.
    for (let i = 0; i < len; i++) {
      const t = i / len;
      const env = Math.pow(1 - t, 1.5 + dampNorm * 1.5) * Math.exp(-3 * t);
      const a = Math.max(0.05, 1 - dampNorm * (0.45 + 0.5 * t));
      lp += a * ((Math.random() * 2 - 1) - lp);
      data[i] = lp * env;
    }
  }
  return ir;
}

export class ReverbBus {
  constructor(ctx, destination) {
    this.ctx = ctx;
    this.input = ctx.createGain();
    this.convolver = ctx.createConvolver();
    this.wet = ctx.createGain();
    this.wet.gain.value = 0.8;
    this.input.connect(this.convolver);
    this.convolver.connect(this.wet);
    this.wet.connect(destination);
    this.setParams(80, 80);
  }

  setParams(decay, damping) {
    this.decay = decay;
    this.damping = damping;
    this.convolver.buffer = buildReverbIR(this.ctx, decay, damping);
  }
}

export class DelayBus {
  constructor(ctx, destination) {
    this.ctx = ctx;
    this.input = ctx.createGain();
    this.delayL = ctx.createDelay(5);
    this.delayR = ctx.createDelay(5);
    this.fbL = ctx.createGain();
    this.fbR = ctx.createGain();
    this.damp = ctx.createBiquadFilter();
    this.damp.type = 'lowpass';
    this.damp.frequency.value = 6000;
    this.merger = ctx.createChannelMerger(2);
    this.out = ctx.createGain();
    this.out.gain.value = 0.9;

    this.input.connect(this.damp);
    this.damp.connect(this.delayL);
    this.damp.connect(this.delayR);
    this.delayL.connect(this.fbL);
    this.delayR.connect(this.fbR);
    // Ping-pong style cross-feedback.
    this.fbL.connect(this.delayR);
    this.fbR.connect(this.delayL);
    this.delayL.connect(this.merger, 0, 0);
    this.delayR.connect(this.merger, 0, 1);
    this.merger.connect(this.out);
    this.out.connect(destination);

    this.params = { time: 64, sync: 20, feedback: 64, width: 127, lrRatio: 4 };
    this.bpm = 120;
  }

  setParams({ time, sync, feedback, width, lrRatio }, bpm) {
    if (bpm) this.bpm = bpm;
    Object.assign(this.params, { time, sync, feedback, width, lrRatio });
    const beats = delaySyncToBeats(sync);
    const base = Math.min(4.5, beats * (60 / this.bpm));
    const [lr, rr] = DELAY_LR_RATIOS[Math.min(12, lrRatio)] ?? [1, 1];
    const maxR = Math.max(lr, rr) || 1;
    const tL = lr === 0 ? 0 : base * (lr / maxR);
    const tR = rr === 0 ? 0 : base * (rr / maxR);
    const now = this.ctx.currentTime;
    this.delayL.delayTime.setTargetAtTime(Math.max(0.01, tL || 0.01), now, 0.05);
    this.delayR.delayTime.setTargetAtTime(Math.max(0.01, tR || 0.01), now, 0.05);
    const fb = Math.min(0.92, (feedback / 127) * 0.92);
    this.fbL.gain.setTargetAtTime(lr === 0 ? 0 : fb, now, 0.05);
    this.fbR.gain.setTargetAtTime(rr === 0 ? 0 : fb, now, 0.05);
  }

  setBpm(bpm) {
    this.setParams(this.params, bpm);
  }
}

export function makeDistortionCurve(amount, type = 0) {
  // amount 0..1; type indexes DISTORTION_TYPES (approximated).
  const n = 1024;
  const curve = new Float32Array(n);
  const k = 1 + amount * 30;
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * 2 - 1;
    let y;
    switch (type) {
      case 2: // clipper
        y = Math.max(-0.8, Math.min(0.8, x * k * 0.5));
        break;
      case 4: // rectifier
        y = Math.abs(x * (1 + amount)) * 2 - 1;
        break;
      case 5: { // bit reducer
        const levels = Math.max(2, Math.round(16 - amount * 13));
        y = Math.round(x * levels) / levels;
        break;
      }
      default: // diode/valve/etc -> tanh family
        y = Math.tanh(x * k) / Math.tanh(k * 0.6);
    }
    curve[i] = y;
  }
  return curve;
}
