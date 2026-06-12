// View logic: pad colours and pad interaction per mode, following the
// Circuit Tracks user guide layouts exactly (pages 27-93).
import {
  TRACK_COLORS, SCALE_TYPES, SCALE_ROOTS, SYNC_RATE_NAMES,
  STEP_BLUE, STEP_NOTE, SAND, PEACH, CREAM, SIDECHAIN_PRESETS,
  PROJECT_COLORS,
} from '../constants.js';
import { keyboardLayout, midiToNcs } from '../scales.js';

const OFF = '#1a1a1f';
const GREY = '#3a3a42';
const WHITE = '#ffffff';

function rgba(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

// 3×4 digit glyphs for the Tempo/Swing display (guide p.85).
const DIGITS = {
  0: ['111', '101', '101', '111'],
  1: ['010', '110', '010', '111'],
  2: ['111', '001', '110', '111'],
  3: ['111', '011', '001', '111'],
  4: ['101', '111', '001', '001'],
  5: ['111', '100', '011', '111'],
  6: ['100', '111', '101', '111'],
  7: ['111', '001', '001', '001'],
  8: ['010', '111', '101', '111'],
  9: ['111', '101', '111', '001'],
};
const HUNDREDS = {
  1: ['01', '01', '01', '01'],
  2: ['11', '01', '10', '11'],
};

export class Views {
  constructor(app) {
    this.app = app;
    this.playheadStep = new Array(8).fill(-1);
    this.playingPattern = new Array(8).fill(0);
    this.pressedNotePads = new Map(); // padIdx -> midi note
    this.activeNotes = []; // {trackId, midi, until} — sequencer-played notes
    this.auditionHeld = []; // notes held by a pressed step pad
    this.drumFlashes = []; // {trackId, until}
    this.heldPatternPads = new Map(); // padIdx -> {trackId, patIdx}
    this.activeScene = -1;
    this.heldScenePad = null;
    this.sceneCopySource = null;
    this.assignFlash = null;
    this._lastScenePulse = 0;
    this.samplePressTime = new Map(); // padIdx -> timestamp (drum tap vs press)
  }

  get ui() { return this.app.ui; }
  get project() { return this.app.project; }
  get pattern() {
    const t = this.ui.currentTrack;
    const patIdx = this.app.seq.playing
      ? this.app.seq.trackState[t]?.patIdx ?? this.ui.currentPattern[t]
      : this.ui.currentPattern[t];
    return this.project.patterns[t][patIdx];
  }

  trackColor(t = this.ui.currentTrack) { return TRACK_COLORS[t]; }

  // ---------- Rendering ----------

  render() {
    this.app.updateArrowLeds?.();
    const pads = this.app.pads;
    const colors = this.padColors();
    for (let i = 0; i < 32; i++) {
      pads[i].style.background = colors[i]?.bg ?? OFF;
      pads[i].querySelector('.pad-label').textContent = colors[i]?.label ?? '';
    }
  }

  padColors() {
    switch (this.ui.view) {
      case 'note': return this.notePads();
      case 'velocity': return this.faderPads('velocity');
      case 'gate': return this.faderPads('gate');
      case 'probability': return this.probabilityPads();
      case 'microStep': return this.microStepPads();
      case 'patternSettings': return this.patternSettingsPads();
      case 'patterns': return this.patternsPads();
      case 'projects': return this.projectsPads();
      case 'mixer': return this.mixerPads();
      case 'fx': return this.fxPads();
      case 'sidechain': return this.sidechainPads();
      case 'scales': return this.scalesPads();
      case 'preset': return this.presetPads();
      case 'tempo': return this.tempoPads();
      default: return Array.from({ length: 32 }, () => ({ bg: OFF }));
    }
  }

  // Steps on pads 0-15: pale blue, bright cyan when the step holds notes,
  // white playhead, near-white selected cursor (guide p.27/42).
  stepRowPads(out) {
    const t = this.ui.currentTrack;
    const pat = this.pattern;
    const pageOff = this.ui.stepPage * 16;
    for (let i = 0; i < 16; i++) {
      const stepIdx = pageOff + i;
      const inRange = stepIdx >= pat.settings.playbackStart && stepIdx <= pat.settings.playbackEnd;
      const step = pat.steps[stepIdx];
      const hasContent = pat.kind === 'drum' ? step?.active : step?.mask > 0;
      let bg = OFF;
      if (inRange) {
        if (hasContent) {
          // Sample-flipped drum steps illuminate pink (guide p.63). Files
          // sometimes store the active sample index explicitly instead of
          // 0xFF, so compare against the track's active sample.
          const flipped = pat.kind === 'drum'
            && step.drumChoice !== 0xff
            && step.drumChoice !== this.project.drumConfigs[t - 4]?.patchSelect;
          bg = flipped ? rgba('#ff7ac8', 0.95) : rgba(STEP_NOTE, 0.95);
        } else {
          bg = rgba(STEP_BLUE, 0.45);
        }
      }
      if (this.ui.selectedStep[t] === stepIdx && inRange) bg = rgba(WHITE, 0.8);
      if (this.playheadStep[t] === stepIdx) bg = WHITE;
      if (this.ui.heldStep === stepIdx) bg = '#ff5050';
      out[i] = { bg };
    }
  }

  soundingNote(trackId, midi) {
    return this.activeNotes.some((n) => n.trackId === trackId && n.midi === midi);
  }

  notePads() {
    const out = Array.from({ length: 32 }, () => ({ bg: OFF }));
    const t = this.ui.currentTrack;
    const color = this.trackColor();

    if (this.app.trackKind(t) === 'drum') {
      if (this.ui.noteExpanded) {
        // Expanded Drum View (p.61): only pads 28-31 are live play pads.
        for (let d = 0; d < 4; d++) {
          const flash = this.drumFlashes.some((f) => f.trackId === 4 + d);
          out[28 + d] = { bg: flash ? WHITE : rgba(TRACK_COLORS[4 + d], 0.85), label: `D${d + 1}` };
        }
        for (let i = 0; i < 28; i++) out[i] = { bg: GREY };
        return out;
      }
      this.stepRowPads(out);
      // Rows 3-4: one page (16) of samples in the track colour; the active
      // sample is brightest; the sample that actually played flashes (p.60).
      const drumIdx = t - 4;
      const page = this.ui.samplePage[drumIdx];
      const current = this.project.drumConfigs[drumIdx].patchSelect;
      const flashing = new Set(this.drumFlashes.filter((f) => f.trackId === t).map((f) => f.sample));
      for (let i = 0; i < 16; i++) {
        const sampleIdx = page * 16 + i;
        if (sampleIdx >= 64) break;
        const isCurrent = sampleIdx === current;
        let bg = isCurrent ? rgba(color, 1) : rgba(color, 0.3);
        if (flashing.has(sampleIdx)) bg = WHITE;
        out[16 + i] = { bg, label: String(sampleIdx + 1) };
      }
      return out;
    }

    // Synth/MIDI keyboard.
    const layout = keyboardLayout(
      this.project.scaleRoot, this.project.scaleType,
      this.ui.octave[Math.min(t, 3)], this.ui.noteExpanded,
    );
    if (!this.ui.noteExpanded) this.stepRowPads(out);
    const lo = this.ui.noteExpanded ? 0 : 16;
    for (let i = lo; i < 32; i++) {
      const key = layout[i];
      if (!key) {
        out[i] = { bg: this.ui.noteExpanded || i >= 16 ? GREY : OFF };
        continue;
      }
      let bg = key.pale ? rgba(this.trackColor(), 0.3) : rgba(this.trackColor(), 0.85);
      if (this.pressedNotePads.has(i) || this.soundingNote(t, key.midi)) bg = WHITE;
      out[i] = { bg };
    }
    return out;
  }

  faderPads(kind) {
    const out = Array.from({ length: 32 }, () => ({ bg: OFF }));
    this.stepRowPads(out);
    const t = this.ui.currentTrack;
    const pat = this.pattern;
    const stepIdx = this.ui.heldStep ?? this.ui.selectedStep[t];
    const step = pat.steps[stepIdx];

    // 16-pad sand "fader" on rows 3-4, left-to-right top-down (p.42/45).
    const padFor = (i) => 16 + i;

    if (kind === 'velocity') {
      let v = 0;
      let vMax = 0;
      if (step) {
        if (pat.kind === 'drum') {
          v = vMax = step.active ? step.velocity : 0;
        } else {
          const vels = step.notes.filter((n, idx) => step.mask & (1 << idx)).map((n) => n.velocity);
          v = vels.length ? Math.min(...vels) : 0;
          vMax = vels.length ? Math.max(...vels) : 0;
        }
      }
      const full = Math.floor(v / 8);
      const fullMax = Math.floor(vMax / 8);
      for (let i = 0; i < 16; i++) {
        let bg = rgba(SAND, 0.08);
        if (i < full) bg = rgba(SAND, 0.95);
        else if (i < fullMax) bg = rgba(SAND, 0.4); // per-note velocity range (p.44)
        out[padFor(i)] = { bg };
      }
      return out;
    }

    // Gate: lit pad count = note duration in steps; fractional value dims the
    // last pad (p.45-46). For drum tracks, Gate View hosts the drum micro
    // steps instead (p.66).
    if (pat.kind === 'drum') return this.drumMicroPads(out, step);
    let ticks = 0;
    if (step && step.mask) {
      const n = step.notes.find((n2, idx) => step.mask & (1 << idx));
      if (n) ticks = Math.max(1, n.gate & 0x7f);
    }
    const whole = Math.floor(ticks / 6);
    const frac = ticks % 6;
    for (let i = 0; i < 16; i++) {
      let bg = rgba(SAND, 0.06);
      if (i < whole) bg = rgba(SAND, 0.95);
      else if (i === whole && frac > 0) bg = rgba(SAND, 0.2 + 0.12 * frac);
      out[padFor(i)] = { bg, label: '' };
    }
    return out;
  }

  probabilityPads() {
    const out = Array.from({ length: 32 }, () => ({ bg: OFF }));
    this.stepRowPads(out);
    const t = this.ui.currentTrack;
    const stepIdx = this.ui.heldStep ?? this.ui.selectedStep[t];
    const step = this.pattern.steps[stepIdx];
    const prob = step ? Math.min(7, step.probability ?? 7) : 7;
    // Row 3 = 8-pad probability meter, colour deepening left to right (p.47).
    for (let i = 0; i < 8; i++) {
      out[16 + i] = {
        bg: i <= prob ? rgba('#3cff8c', 0.2 + 0.8 * (i / 7)) : rgba('#3cff8c', 0.05),
      };
    }
    return out;
  }

  // The note slots affected by micro-step/tie editing (null = all assigned).
  selectedSlots(step) {
    const assigned = [];
    for (let s = 0; s < 6; s++) if (step.mask & (1 << s)) assigned.push(s);
    if (!this.ui.selectedNoteSlots) return assigned;
    const sel = assigned.filter((s) => this.ui.selectedNoteSlots.has(s));
    return sel.length ? sel : assigned;
  }

  // Micro Step View (guide p.48-51): row 3 pads 1-6 = micro positions,
  // row 3 pad 8 = tie-forward, row 4 = per-note selector.
  microStepPads() {
    const out = Array.from({ length: 32 }, () => ({ bg: OFF }));
    this.stepRowPads(out);
    const t = this.ui.currentTrack;
    const pat = this.pattern;
    const color = this.trackColor();
    const stepIdx = this.ui.heldStep ?? this.ui.selectedStep[t];
    const step = pat.steps[stepIdx];

    if (pat.kind === 'drum') return this.drumMicroPads(out, step);

    const slots = step?.mask ? this.selectedSlots(step) : [];
    const delays = new Set(slots.map((s) => step.notes[s].delay));
    for (let i = 0; i < 6; i++) {
      out[16 + i] = {
        bg: delays.has(i) ? rgba(SAND, 1) : rgba(SAND, 0.2),
        label: i === 0 ? 'on' : `+${i}`,
      };
    }
    // Tie-forward on/off (p.51, orange pad at the end of row 3).
    const tied = slots.some((s) => (step.notes[s].gate & 0x80) !== 0);
    out[23] = { bg: rgba('#ff9a2e', tied ? 1 : 0.25), label: 'TIE' };

    // Row 4: one selector pad per assigned note, in assignment order.
    if (step?.mask) {
      const assigned = [];
      for (let s = 0; s < 6; s++) if (step.mask & (1 << s)) assigned.push(s);
      assigned.forEach((slot, i) => {
        const sel = !this.ui.selectedNoteSlots || this.ui.selectedNoteSlots.has(slot);
        out[24 + i] = { bg: rgba(color, sel ? 1 : 0.3), label: `N${i + 1}` };
      });
      for (let i = assigned.length; i < 6; i++) out[24 + i] = { bg: GREY };
    }
    return out;
  }

  // Drum micro steps (guide p.66-67): six toggle pads; multiple lit ticks
  // mean duplicate hits within the step. Shown in Gate View for drums.
  drumMicroPads(out, step) {
    const micro = step?.active
      ? (step.micro?.some(Boolean) ? step.micro : [true, false, false, false, false, false])
      : null;
    for (let i = 0; i < 6; i++) {
      out[16 + i] = {
        bg: micro?.[i] ? rgba(WHITE, 0.95) : rgba(WHITE, 0.15),
        label: String(i + 1),
      };
    }
    return out;
  }

  patternSettingsPads() {
    const out = Array.from({ length: 32 }, () => ({ bg: OFF }));
    const pat = this.pattern;
    const color = this.trackColor();
    const pageOff = this.ui.stepPage * 16;
    for (let i = 0; i < 16; i++) {
      const stepIdx = pageOff + i;
      let bg = rgba(color, 0.08);
      if (stepIdx >= pat.settings.playbackStart && stepIdx <= pat.settings.playbackEnd) bg = rgba(color, 0.45);
      if (stepIdx === pat.settings.playbackStart) bg = rgba('#3cff8c', 0.9);
      if (stepIdx === pat.settings.playbackEnd) bg = rgba('#ff5a3c', 0.9);
      out[i] = { bg };
    }
    // Sync-rate pads run 1/4 (slowest) to 1/32T left to right; the stored
    // byte is reversed (7 = 1/4 ... 0 = 1/32T, default 3 = 1/16).
    for (let i = 0; i < 8; i++) {
      const stored = 7 - i;
      out[16 + i] = {
        bg: pat.settings.syncRate === stored ? rgba('#ffb43c', 1) : rgba('#ffb43c', 0.15),
        label: SYNC_RATE_NAMES[stored],
      };
    }
    const dirs = ['FWD', 'REV', 'PNG', 'RND'];
    for (let i = 0; i < 4; i++) {
      out[24 + i] = {
        bg: pat.settings.direction === i ? rgba('#b48cf0', 1) : rgba('#b48cf0', 0.15),
        label: dirs[i],
      };
    }
    return out;
  }

  // Patterns View (p.73): columns = 8 tracks, rows = 4 pattern memories;
  // ▼▲ switch between patterns 1-4 and 5-8.
  patternsPads() {
    const out = Array.from({ length: 32 }, () => ({ bg: OFF }));
    for (let t = 0; t < 8; t++) {
      const color = TRACK_COLORS[t];
      const chain = this.app.seq.chainFor(t);
      const playing = this.app.seq.playing ? this.app.seq.trackState[t]?.patIdx : -1;
      const current = this.ui.currentPattern[t];
      for (let row = 0; row < 4; row++) {
        const patIdx = this.ui.patternPage * 4 + row;
        const pat = this.project.patterns[t][patIdx];
        const hasData = pat.kind === 'drum'
          ? pat.steps.some((s) => s.active)
          : pat.steps.some((s) => s.mask > 0);
        let bg = rgba(color, hasData ? 0.35 : 0.15);
        if (chain.includes(patIdx)) bg = rgba(color, 0.7);
        if (patIdx === current) bg = rgba(color, 1);
        if (patIdx === playing) bg = WHITE;
        out[row * 8 + t] = { bg, label: String(patIdx + 1) };
      }
    }
    return out;
  }

  // Projects View: one pad per pack project in its project colour (guide
  // p.18). Dim = stored project, bright = currently loaded, flashing white =
  // queued to take over at the end of the current pattern.
  projectsPads() {
    const out = Array.from({ length: 32 }, () => ({ bg: OFF }));
    const bank = this.app.projectBank;
    const page = this.ui.projectPage ?? 0;
    const flash = performance.now() % 360 < 180;
    for (let i = 0; i < 32; i++) {
      const idx = page * 32 + i;
      const entry = bank[idx];
      if (!entry) continue;
      const [r, g, b] = PROJECT_COLORS[entry.color % PROJECT_COLORS.length] ?? [128, 128, 128];
      let bg = `rgba(${r},${g},${b},0.35)`;
      if (idx === this.ui.currentProjectIdx) bg = `rgba(${r},${g},${b},1)`;
      if (this.app.pendingProject?.idx === idx) bg = flash ? WHITE : `rgba(${r},${g},${b},0.9)`;
      out[i] = { bg, label: String(idx + 1) };
    }
    return out;
  }

  projectsPressed(i) {
    const idx = (this.ui.projectPage ?? 0) * 32 + i;
    if (this.app.projectBank[idx]) this.app.selectProjectFromBank(idx);
  }

  // Mixer View (p.88): row 1 = mutes, rows 3-4 = the 16 Scene pads.
  // Scene pad colours (guide p.81-84): dim white = empty, bright white =
  // stored, dim gold with Shift (bright gold while storing), green members
  // for a scene chain, pulsing green = selected scene whose patterns match,
  // flashing green = queued.
  mixerPads() {
    const out = Array.from({ length: 32 }, () => ({ bg: OFF }));
    for (let t = 0; t < 8; t++) {
      const color = TRACK_COLORS[t];
      out[t] = { bg: rgba(color, this.ui.mutes[t] ? 0.15 : 0.9), label: this.ui.mutes[t] ? 'MUTE' : '' };
    }
    const nowMs = performance.now();
    const pulse = 0.5 + 0.45 * Math.sin(nowMs / 170);
    const sc = this.app.seq.sceneState;
    const chain = this.project.sceneChain ?? { start: 0, end: 0 };
    const chainLo = Math.min(chain.start ?? 0, 15);
    const chainHi = Math.min(chain.end ?? 0, 15);
    const multiChain = chainHi > chainLo;
    for (let i = 0; i < 16; i++) {
      const scene = this.project.scenes[i];
      const stored = scene.flags === 1
        || scene.trackChains.some((c) => (c.start ?? 0) !== 0 || (c.end ?? 0) !== 0);
      if (this.ui.shift) {
        const storing = this.assignFlash && this.assignFlash.scene === i && this.assignFlash.until > nowMs;
        out[16 + i] = { bg: rgba('#c8a028', storing ? 1 : 0.4), label: `S${i + 1}` };
        continue;
      }
      let bg = rgba('#e8ecf2', stored ? 0.55 : 0.1);
      if (multiChain && i >= chainLo && i <= chainHi) bg = rgba('#3cff8c', 0.35);
      const queued = sc && sc.pos === -1 && sc.chain[0] === i;
      const playingScene = sc && sc.pos >= 0 && sc.current === i && this.app.seq.playing;
      const selectedMatch = this.activeScene === i && this.scenesMatchCurrent(i);
      if (queued) bg = rgba('#3cff8c', nowMs % 300 < 150 ? 0.95 : 0.2);
      else if (playingScene || selectedMatch) bg = rgba('#3cff8c', pulse);
      out[16 + i] = { bg, label: `S${i + 1}` };
    }
    return out;
  }

  // Do the live pattern chains correspond to scene i's stored assignment?
  // (guide p.83 — drives the pulsing green of the last selected scene)
  scenesMatchCurrent(i) {
    const scene = this.project.scenes[i];
    if (!scene) return false;
    for (let t = 0; t < 8; t++) {
      const c = scene.trackChains[t] ?? { start: 0, end: 0 };
      const pc = this.project.patternChains[t] ?? { start: 0, end: 0 };
      if ((pc.start ?? 0) !== (c.start ?? 0) || (pc.end ?? 0) !== (c.end ?? 0)) return false;
      if (this.ui.currentPattern[t] !== Math.max(0, Math.min(7, c.start ?? 0))) return false;
    }
    return true;
  }

  // FX View (p.90): rows 1-2 = 16 delay presets (peach), row 3 = 8 reverb
  // presets (cream), row 4 pad 25 = FX on/off, rest unused.
  fxPads() {
    const out = Array.from({ length: 32 }, () => ({ bg: OFF }));
    for (let i = 0; i < 16; i++) {
      out[i] = { bg: rgba(PEACH, this.project.delayPreset === i ? 1 : 0.3) };
    }
    for (let i = 0; i < 8; i++) {
      out[16 + i] = { bg: rgba(CREAM, this.project.reverbPreset === i ? 1 : 0.3) };
    }
    const on = !this.project.fx.fxBypass;
    out[24] = { bg: on ? WHITE : rgba(WHITE, 0.15), label: on ? 'FX ON' : 'FX OFF' };
    for (let i = 25; i < 32; i++) out[i] = { bg: GREY };
    return out;
  }

  // Side Chain View (p.93): row 1 pads 5-8 = drum trigger source for the
  // focused track; rows 3-4 = OFF + presets 1-7 for the page's two tracks.
  scTracks() { return this.ui.scPage === 0 ? [0, 1] : [2, 3]; }

  sidechainPads() {
    const out = Array.from({ length: 32 }, () => ({ bg: GREY }));
    const [a, b] = this.scTracks();
    const chMap = { 0: 0, 1: 1, 2: 2, 3: 3 }; // sidechain idx -> track channel
    const focus = this.ui.scFocus;
    const focusColor = TRACK_COLORS[chMap[focus]];
    const source = this.project.sidechain[focus]?.source ?? 4;
    for (let d = 0; d < 4; d++) {
      out[4 + d] = {
        bg: source === d ? rgba(focusColor, 1) : rgba(WHITE, 0.25),
        label: `Drum ${d + 1}`,
      };
    }
    const rowFor = (scIdx, padRow) => {
      const color = TRACK_COLORS[chMap[scIdx]];
      const preset = this.project.sidechain[scIdx]?.preset ?? 0;
      out[padRow] = { bg: preset === 0 ? rgba('#ff3c3c', 1) : rgba('#ff3c3c', 0.25), label: 'OFF' };
      for (let p = 1; p <= 7; p++) {
        out[padRow + p] = { bg: rgba(color, preset === p ? 1 : 0.3), label: String(p) };
      }
    };
    rowFor(a, 16);
    rowFor(b, 24);
    return out;
  }

  scalesPads() {
    const out = Array.from({ length: 32 }, () => ({ bg: OFF }));
    // Keynote selection in piano layout (p.30): row 1 = black keys
    // (pads 2,3,5,6,7), row 2 = white keys (pads 9-15); 1,4,8,16 disabled.
    const intervals = SCALE_TYPES[this.project.scaleType]?.intervals ?? [];
    const inScale = (pc) => intervals.includes(((pc - this.project.scaleRoot) + 12) % 12);
    const layout = [
      [null, 1, 3, null, 6, 8, 10, null], // row 1: black keys
      [0, 2, 4, 5, 7, 9, 11, null],       // row 2: white keys
    ];
    for (let row = 0; row < 2; row++) {
      for (let col = 0; col < 8; col++) {
        const pc = layout[row][col];
        if (pc == null) { out[row * 8 + col] = { bg: GREY }; continue; }
        let bg = inScale(pc) ? rgba('#1ed8e8', 0.9) : rgba(STEP_BLUE, 0.25);
        if (pc === this.project.scaleRoot) bg = rgba('#6a5af0', 1);
        out[row * 8 + col] = { bg, label: SCALE_ROOTS[pc] };
      }
    }
    // Rows 3-4: the 16 scales (salmon; selected brighter — p.30).
    for (let i = 0; i < 16; i++) {
      out[16 + i] = {
        bg: rgba('#ff7878', this.project.scaleType === i ? 1 : 0.3),
        label: SCALE_TYPES[i].name.split(' ').map((w) => w.slice(0, 5)).join(' '),
      };
    }
    return out;
  }

  presetPads() {
    const out = Array.from({ length: 32 }, () => ({ bg: OFF }));
    const t = this.ui.currentTrack;
    const color = this.trackColor();
    const kind = this.app.trackKind(t);

    if (kind === 'drum') {
      const drumIdx = t - 4;
      const page = Math.min(1, this.ui.presetPage);
      const current = this.project.drumConfigs[drumIdx].patchSelect;
      for (let i = 0; i < 32; i++) {
        const idx = page * 32 + i;
        if (idx >= 64) break;
        out[i] = {
          bg: idx === current ? WHITE : rgba(color, 0.25 + 0.15 * (Math.floor(i / 8) % 2)),
          label: this.app.drums.sampleName(idx).slice(0, 10),
        };
      }
      return out;
    }

    if (kind === 'midi') {
      const sel = this.ui.midiTemplate[t - 2];
      for (let i = 0; i < 8; i++) {
        out[i] = { bg: i === sel ? WHITE : rgba(color, 0.3), label: `Tpl ${i + 1}` };
      }
      return out;
    }

    const bank = this.app.patchBank;
    const page = Math.min(this.ui.presetPage, Math.max(0, Math.ceil(bank.length / 32) - 1));
    for (let i = 0; i < 32; i++) {
      const idx = page * 32 + i;
      if (idx >= bank.length) break;
      out[i] = {
        bg: this.ui.patchIndex[t] === idx ? WHITE : rgba(color, 0.25 + 0.15 * (Math.floor(i / 8) % 2)),
        label: bank[idx].name.slice(0, 10),
      };
    }
    return out;
  }

  // Tempo View (p.85): BPM as large digits — hundreds in columns 1-2,
  // tens and units in three columns each, alternating colours for
  // readability. Turning Macro 2 shows the Swing value (orange) instead.
  tempoPads() {
    const out = Array.from({ length: 32 }, () => ({ bg: rgba(WHITE, 0.07) }));
    const draw = (glyph, colOff, color) => {
      for (let r = 0; r < 4; r++) {
        for (let c = 0; c < glyph[r].length; c++) {
          if (glyph[r][c] === '1') out[r * 8 + colOff + c] = { bg: color };
        }
      }
    };
    const disp = this.app.tempoDisplay;
    if (disp?.mode === 'swing' && disp.until > performance.now()) {
      // Swing: two digits in alternating orange shades (Macro 2's colour).
      const swing = this.app.seq.swing;
      draw(DIGITS[Math.floor(swing / 10) % 10], 2, rgba('#ff9a2e', 1));
      draw(DIGITS[swing % 10], 5, rgba('#ffd24b', 1));
      return out;
    }
    const bpm = this.app.seq.bpm;
    const hundreds = Math.floor(bpm / 100);
    if (HUNDREDS[hundreds]) draw(HUNDREDS[hundreds], 0, rgba(WHITE, 0.95));
    draw(DIGITS[Math.floor(bpm / 10) % 10], 2, rgba('#1ec8e8', 1));
    draw(DIGITS[bpm % 10], 5, rgba('#7adcff', 1));
    return out;
  }

  // ---------- Interaction ----------

  padPressed(i) {
    switch (this.ui.view) {
      case 'note': return this.notePadPressed(i);
      case 'velocity': return this.faderPadPressed(i, 'velocity');
      case 'gate': return this.faderPadPressed(i, 'gate');
      case 'probability': return this.probabilityPressed(i);
      case 'microStep': return this.microStepPressed(i);
      case 'patternSettings': return this.patternSettingsPressed(i);
      case 'patterns': return this.patternsPressed(i);
      case 'projects': return this.projectsPressed(i);
      case 'mixer': return this.mixerPressed(i);
      case 'fx': return this.fxPressed(i);
      case 'sidechain': return this.sidechainPressed(i);
      case 'scales': return this.scalesPressed(i);
      case 'preset': return this.presetPressed(i);
    }
  }

  padReleased(i) {
    if (this.ui.view === 'note' && this.pressedNotePads.has(i)) {
      const midi = this.pressedNotePads.get(i);
      this.pressedNotePads.delete(i);
      this.app.liveNoteOff(midi);
      this.render();
      return;
    }
    if (this.ui.view === 'note' && this.samplePressTime.has(i)) {
      // Drum sample pads: a quick tap selects the sample; a long press only
      // auditions it (p.61).
      const t = this.ui.currentTrack;
      const dt = performance.now() - this.samplePressTime.get(i);
      this.samplePressTime.delete(i);
      if (dt < 300 && this.app.trackKind(t) === 'drum') {
        const drumIdx = t - 4;
        const sampleIdx = this.ui.samplePage[drumIdx] * 16 + (i - 16);
        if (sampleIdx < 64) {
          this.project.drumConfigs[drumIdx].patchSelect = sampleIdx;
          this.app.drums.applyConfig(drumIdx, { patchSelect: sampleIdx });
          this.app.lcdMsg(`D${drumIdx + 1}: ${this.app.drums.sampleName(sampleIdx)}`);
          this.app.refreshSidebar();
        }
      }
      this.render();
      return;
    }
    if (this.ui.view === 'preset' && this.presetHeld === i) {
      this.presetHeld = null;
      this.releaseAudition();
      return;
    }
    if (this.ui.view === 'mixer' && i >= 16 && this.heldScenePad === i - 16) {
      this.heldScenePad = null;
      return;
    }
    if (this.ui.view === 'patterns') this.heldPatternPads.delete(i);
    if (i < 16 && this.ui.heldStep === this.ui.stepPage * 16 + i) {
      this.ui.heldStep = null;
      this.releaseAudition();
      this.render();
    }
  }

  // Stop any step-audition notes held by a pressed step pad.
  releaseAudition() {
    for (const a of this.auditionHeld) {
      this.app.synthTracks[a.trackId]?.noteOff(a.midi, this.app.engine.now());
    }
    this.auditionHeld = [];
    this.activeNotes = this.activeNotes.filter((n) => !n.audition);
  }

  stepIdxForPad(i) { return this.ui.stepPage * 16 + i; }

  // Pressing a step pad: select it (and audition/toggle); Clear+pad deletes.
  stepPadPressed(i, { drumToggles = false } = {}) {
    const t = this.ui.currentTrack;
    const pat = this.app.currentEditPattern();
    const stepIdx = this.stepIdxForPad(i);
    this.ui.heldStep = stepIdx;
    this.ui.selectedStep[t] = stepIdx;

    if (this.ui.clearHeld) {
      const step = pat.steps[stepIdx];
      if (pat.kind === 'drum') {
        step.active = false; step.velocity = 0; step.drumChoice = 0xff;
      } else {
        step.mask = 0;
        step.notes.forEach((n) => { n.note = 0; n.gate = 0; });
      }
      step.probability = 7;
      for (const m of Object.values(pat.paramLocks ?? {})) {
        for (const pos of Object.keys(m)) {
          const p = Number(pos);
          if (p >= stepIdx && p < stepIdx + 1) delete m[pos];
        }
      }
      this.app.lcdMsg(`Step ${stepIdx + 1} cleared`);
      this.app.onPatternEdited?.(t);
      this.render();
      return true;
    }

    if (pat.kind === 'drum' && drumToggles) {
      // Drum step pads toggle hits (p.61).
      const step = pat.steps[stepIdx];
      step.active = !step.active;
      step.velocity = step.active ? (this.ui.fixedVelocity ? 96 : 96) : 0;
      this.app.onPatternEdited?.(t);
    } else if (pat.kind === 'drum') {
      // Selecting a hit in Velocity/Gate/Micro/Probability views previews
      // it, as on the hardware.
      const step = pat.steps[stepIdx];
      if (step.active) this.previewDrumStep(t, pat, step);
    } else {
      // Audition the notes on the step, lighting them on the keyboard. The
      // notes hold for as long as the pad is held (guide p.89).
      this.releaseAudition();
      const step = pat.steps[stepIdx];
      if (step.mask) {
        step.notes.forEach((n, idx) => {
          if (step.mask & (1 << idx) && n.note > 0) {
            const midi = this.app.ncsNoteToMidi(n.note);
            this.app.synthTracks[t]?.noteOn(this.app.engine.now(), midi, n.velocity);
            this.auditionHeld.push({ trackId: t, midi });
            this.activeNotes.push({ trackId: t, midi, until: Infinity, audition: true });
          }
        });
      }
    }
    this.render();
    return true;
  }

  notePadPressed(i) {
    const t = this.ui.currentTrack;
    const isDrum = this.app.trackKind(t) === 'drum';

    if (isDrum && this.ui.noteExpanded) {
      if (i >= 28) {
        const d = i - 28;
        this.app.drums.play(d, this.app.engine.now(), 110);
        if (this.ui.recording && this.app.seq.playing) this.app.seq.recordNote(4 + d, 0, 110);
        this.drumFlashes.push({ trackId: 4 + d, until: performance.now() + 150 });
        this.render();
      }
      return;
    }

    if (!this.ui.noteExpanded && i < 16) {
      this.stepPadPressed(i, { drumToggles: isDrum });
      return;
    }

    if (isDrum) {
      // Sample pads: play now; selection happens on quick release. While
      // recording, the tapped sample is recorded to the step (Sample Flip).
      const drumIdx = t - 4;
      const sampleIdx = this.ui.samplePage[drumIdx] * 16 + (i - 16);
      if (sampleIdx < 64) {
        this.app.drums.play(drumIdx, this.app.engine.now(), 110, sampleIdx);
        if (this.ui.recording && this.app.seq.playing) this.app.seq.recordNote(t, 0, 110, sampleIdx);
        this.samplePressTime.set(i, performance.now());
      }
      return;
    }

    // Synth/MIDI keyboard pad.
    const layout = keyboardLayout(
      this.project.scaleRoot, this.project.scaleType,
      this.ui.octave[Math.min(t, 3)], this.ui.noteExpanded,
    );
    const key = layout[i];
    if (!key) return;
    const midi = key.midi;

    // Holding a step pad while pressing notes edits that step (step edit).
    if (this.ui.heldStep != null && !this.ui.noteExpanded) {
      const pat = this.app.currentEditPattern();
      const step = pat.steps[this.ui.heldStep];
      const ncs = midiToNcs(midi, this.project.scaleRoot);
      let removed = false;
      step.notes.forEach((n, idx) => {
        if (step.mask & (1 << idx) && n.note === ncs) {
          step.mask &= ~(1 << idx);
          n.note = 0; n.gate = 0;
          removed = true;
        }
      });
      if (!removed) {
        for (let slot = 0; slot < 6; slot++) {
          if (!(step.mask & (1 << slot))) {
            step.mask |= 1 << slot;
            step.notes[slot] = {
              note: ncs, gate: 6, delay: 0,
              velocity: this.ui.fixedVelocity ? 96 : 100,
            };
            break;
          }
        }
      }
      this.app.onPatternEdited?.(t);
      this.app.liveNoteOn(midi, 100, { recordless: true });
      this.pressedNotePads.set(i, midi);
      this.render();
      return;
    }

    this.pressedNotePads.set(i, midi);
    this.app.liveNoteOn(midi, this.ui.fixedVelocity ? 96 : 100);
    this.render();
  }

  // Preview a drum hit as the sequencer would play it: micro hits roll at
  // the pattern's own tick spacing.
  previewDrumStep(t, pat, step) {
    const now = this.app.engine.now();
    const stepDur = this.app.seq.stepDuration(pat.settings);
    const micro = step.micro?.some(Boolean) ? step.micro : [true, false, false, false, false, false];
    micro.forEach((on, m) => {
      if (on) this.app.drums.play(t - 4, now + (m / 6) * stepDur, step.velocity || 96, step.drumChoice);
    });
  }

  // Toggle a drum hit's micro tick (Gate/Micro Step views for drums).
  drumMicroPressed(i, step) {
    const tick = i - 16;
    if (tick < 0 || tick > 5 || !step?.active) return false;
    if (!step.micro || !step.micro.some(Boolean)) {
      step.micro = [true, false, false, false, false, false];
    }
    const litCount = step.micro.filter(Boolean).length;
    if (step.micro[tick] && litCount === 1) {
      this.app.lcdMsg('At least one micro step must stay lit');
      return true;
    }
    step.micro[tick] = !step.micro[tick];
    if (step.micro[tick]) {
      // Preview the added hit, as on the hardware.
      const t = this.ui.currentTrack;
      this.app.drums.play(t - 4, this.app.engine.now(), step.velocity || 96, step.drumChoice);
    }
    this.app.lcdMsg(`Micro steps: ${step.micro.map((m, k) => m ? k + 1 : null).filter(Boolean).join(' ')}`);
    this.app.onPatternEdited?.(this.ui.currentTrack);
    this.render();
    return true;
  }

  faderPadPressed(i, kind) {
    if (i < 16) { this.stepPadPressed(i); return; }
    const t = this.ui.currentTrack;
    const pat = this.app.currentEditPattern();
    const stepIdx = this.ui.heldStep ?? this.ui.selectedStep[t];
    const step = pat.steps[stepIdx];
    if (!step) return;
    if (kind === 'gate' && pat.kind === 'drum') {
      this.drumMicroPressed(i, step);
      return;
    }
    const seg = i - 16; // 0-15 on the fader

    if (kind === 'velocity') {
      const v = Math.min(127, (seg + 1) * 8);
      if (pat.kind === 'drum') {
        if (step.active) {
          step.velocity = v;
          // Preview the hit at its new velocity, as on the hardware.
          this.previewDrumStep(t, pat, step);
        }
      } else {
        step.notes.forEach((n, idx) => { if (step.mask & (1 << idx)) n.velocity = v; });
      }
      this.app.lcdMsg(`Velocity: ${v}`);
    } else if (kind === 'gate' && pat.kind !== 'drum' && step.mask) {
      // Pressing the highest lit pad again shortens by one tick (p.46).
      const n0 = step.notes.find((n, idx) => step.mask & (1 << idx));
      const cur = n0 ? Math.max(1, n0.gate & 0x7f) : 6;
      const curPad = Math.ceil(cur / 6) - 1;
      let ticks;
      if (seg === curPad && cur > seg * 6 + 1) ticks = cur - 1;
      else ticks = (seg + 1) * 6;
      step.notes.forEach((n, idx) => {
        if (step.mask & (1 << idx)) n.gate = (n.gate & 0x80) | Math.min(96, ticks);
      });
      this.app.lcdMsg(`Gate: ${(ticks / 6).toFixed(ticks % 6 ? 1 : 0)} steps`);
    }
    this.app.onPatternEdited?.(t);
    this.render();
  }

  probabilityPressed(i) {
    if (i < 16) { this.stepPadPressed(i); return; }
    if (i >= 16 && i < 24) {
      const t = this.ui.currentTrack;
      const stepIdx = this.ui.heldStep ?? this.ui.selectedStep[t];
      const step = this.app.currentEditPattern().steps[stepIdx];
      if (step) {
        step.probability = i - 16;
        this.app.lcdMsg(`Probability: ${Math.round(((i - 16 + 1) / 8) * 100)}%`);
        this.render();
      }
    }
  }

  microStepPressed(i) {
    if (i < 16) {
      this.ui.selectedNoteSlots = null; // new step: all notes selected (p.50)
      this.stepPadPressed(i);
      return;
    }
    const t = this.ui.currentTrack;
    const pat = this.app.currentEditPattern();
    const stepIdx = this.ui.heldStep ?? this.ui.selectedStep[t];
    const step = pat.steps[stepIdx];
    if (!step) return;

    if (pat.kind === 'drum') {
      this.drumMicroPressed(i, step);
      return;
    }
    if (!step.mask) return;

    if (i >= 16 && i < 22) {
      // Set the micro step for the selected note(s).
      const tick = i - 16;
      for (const slot of this.selectedSlots(step)) step.notes[slot].delay = tick;
      this.app.lcdMsg(tick === 0 ? 'Micro step: on the beat' : `Micro step: +${tick}/6`);
      this.app.onPatternEdited?.(t);
      this.render();
    } else if (i === 23) {
      // Tie-forward on/off for the selected note(s) (p.51).
      const slots = this.selectedSlots(step);
      const tied = slots.some((s) => (step.notes[s].gate & 0x80) !== 0);
      for (const slot of slots) {
        const n = step.notes[slot];
        n.gate = tied ? (n.gate & 0x7f) : (n.gate | 0x80);
      }
      this.app.lcdMsg(`Tie-forward ${tied ? 'off' : 'on'}`);
      this.app.onPatternEdited?.(t);
      this.render();
    } else if (i >= 24 && i < 30) {
      // Note selector: pick a single note (and audition it); pressing the
      // sole selected note again reselects all.
      const assigned = [];
      for (let s = 0; s < 6; s++) if (step.mask & (1 << s)) assigned.push(s);
      const slot = assigned[i - 24];
      if (slot == null) return;
      if (this.ui.selectedNoteSlots?.size === 1 && this.ui.selectedNoteSlots.has(slot)) {
        this.ui.selectedNoteSlots = null;
        this.app.lcdMsg('All notes selected');
      } else {
        this.ui.selectedNoteSlots = new Set([slot]);
        const midi = this.app.ncsNoteToMidi(step.notes[slot].note);
        this.app.synthTracks[t]?.noteOn(this.app.engine.now(), midi, step.notes[slot].velocity, 0.3);
        this.activeNotes.push({ trackId: t, midi, until: performance.now() + 300 });
        this.app.lcdMsg(`Note ${i - 24 + 1} selected`);
      }
      this.render();
    }
  }

  patternSettingsPressed(i) {
    const pat = this.app.currentEditPattern();
    if (i < 16) {
      const stepIdx = this.stepIdxForPad(i);
      if (this.ui.shift) {
        pat.settings.playbackStart = Math.min(stepIdx, pat.settings.playbackEnd);
        this.app.lcdMsg(`Start: ${pat.settings.playbackStart + 1}`);
      } else {
        pat.settings.playbackEnd = Math.max(stepIdx, pat.settings.playbackStart);
        this.app.lcdMsg(`End: ${pat.settings.playbackEnd + 1}`);
      }
    } else if (i < 24) {
      pat.settings.syncRate = 7 - (i - 16);
      this.app.lcdMsg(`Sync: ${SYNC_RATE_NAMES[pat.settings.syncRate]}`);
    } else if (i < 28) {
      pat.settings.direction = i - 24;
      this.app.lcdMsg(`Direction: ${['Forward', 'Reverse', 'Ping-Pong', 'Random'][i - 24]}`);
    }
    this.render();
  }

  patternsPressed(i) {
    const t = i % 8;
    const patIdx = this.ui.patternPage * 4 + Math.floor(i / 8);

    if (this.ui.clearHeld) {
      this.app.clearPattern(this.project.patterns[t][patIdx]);
      this.app.lcdMsg(`${this.app.trackName(t)} pattern ${patIdx + 1} cleared`);
      this.render();
      return;
    }

    if (this.ui.duplicateHeld) {
      if (!this.ui.copySource) {
        this.ui.copySource = { trackId: t, patIdx };
        this.app.lcdMsg(`Copy ${this.app.trackName(t)} pattern ${patIdx + 1}…`);
      } else {
        const src = this.ui.copySource;
        const srcKind = this.project.patterns[src.trackId][src.patIdx].kind;
        const dstKind = this.project.patterns[t][patIdx].kind;
        if (srcKind === dstKind) {
          this.project.patterns[t][patIdx] =
            JSON.parse(JSON.stringify(this.project.patterns[src.trackId][src.patIdx]));
          this.app.lcdMsg(`Pasted to ${this.app.trackName(t)} pattern ${patIdx + 1}`);
        } else {
          this.app.lcdMsg('Cannot copy between synth and drum tracks');
        }
      }
      this.render();
      return;
    }

    // Hold one pad and press another in the same track column to chain (p.76).
    for (const held of this.heldPatternPads.values()) {
      if (held.trackId === t && held.patIdx !== patIdx) {
        const lo = Math.min(held.patIdx, patIdx);
        const hi = Math.max(held.patIdx, patIdx);
        this.project.patternChains[t] = { start: lo, end: hi };
        this.ui.currentPattern[t] = lo;
        this.app.lcdMsg(`${this.app.trackName(t)} chain ${lo + 1}-${hi + 1}`);
        this.render();
        return;
      }
    }
    this.heldPatternPads.set(i, { trackId: t, patIdx });

    if (this.ui.shift) {
      // Shift+select: switch immediately, keeping the step position (p.74).
      this.ui.currentPattern[t] = patIdx;
      this.project.patternChains[t] = { start: 0, end: 0 };
      this.app.seq.switchPatternNow(t, patIdx);
      this.app.lcdMsg(`${this.app.trackName(t)} pattern ${patIdx + 1} (now)`);
    } else {
      // Plain select: queued to the end of the current pattern.
      this.ui.currentPattern[t] = patIdx;
      this.project.patternChains[t] = { start: 0, end: 0 };
      this.app.lcdMsg(`${this.app.trackName(t)} pattern ${patIdx + 1}`);
    }
    this.render();
  }

  // Scene pads (guide p.81-84). Shift+pad stores the current pattern chains;
  // Clear+pad resets; Duplicate+pad copies (source first, then targets);
  // holding one pad and pressing another defines a scene chain; a plain
  // press selects (queued at the end of the current Drum 1 pattern when
  // playing, otherwise effective for the next Play).
  scenePadPressed(idx) {
    const scenes = this.project.scenes;
    if (this.ui.clearHeld) {
      scenes[idx].trackChains = Array.from({ length: 8 }, () => ({ start: 0, end: 0 }));
      scenes[idx].flags = 0;
      this.app.lcdMsg(`Scene ${idx + 1} cleared`);
      return;
    }
    if (this.ui.duplicateHeld) {
      if (this.sceneCopySource == null) {
        this.sceneCopySource = idx;
        this.app.lcdMsg(`Copy scene ${idx + 1} — press target pads`);
      } else if (this.sceneCopySource !== idx) {
        scenes[idx].trackChains = scenes[this.sceneCopySource].trackChains.map((c) => ({ ...c }));
        scenes[idx].flags = scenes[this.sceneCopySource].flags;
        this.app.lcdMsg(`Scene ${this.sceneCopySource + 1} copied to ${idx + 1}`);
      }
      return;
    }
    if (this.ui.shift) {
      // Store the current pattern chains (a plain selected pattern stores
      // as a one-pattern chain). Does not affect playback or selection.
      scenes[idx].trackChains = Array.from({ length: 8 }, (_, t) => {
        const pc = this.project.patternChains[t] ?? { start: 0, end: 0 };
        if ((pc.start ?? 0) !== 0 || (pc.end ?? 0) !== 0) {
          return { start: pc.start, end: pc.end };
        }
        const p = this.ui.currentPattern[t];
        return { start: p, end: p };
      });
      scenes[idx].flags = 1;
      this.assignFlash = { scene: idx, until: performance.now() + 450 };
      this.app.lcdMsg(`Scene ${idx + 1} stored`);
      return;
    }
    if (this.heldScenePad != null && this.heldScenePad !== idx) {
      // Scene chain: first held pad to this one, inclusive (p.83).
      this.selectScenes(Math.min(this.heldScenePad, idx), Math.max(this.heldScenePad, idx));
      return;
    }
    this.heldScenePad = idx;
    this.selectScenes(idx, idx);
  }

  selectScenes(start, end) {
    this.project.sceneChain = { start, end };
    this.activeScene = start;
    const label = end > start ? `Scenes ${start + 1}-${end + 1}` : `Scene ${start + 1}`;
    if (this.app.seq.playing) {
      this.app.seq.queueSceneChain(start, end);
      this.app.lcdMsg(`${label} queued`);
    } else {
      this.app.seq.applySceneChains(start);
      this.app.lcdMsg(label);
    }
  }

  mixerPressed(i) {
    if (i < 8) {
      this.ui.mutes[i] = !this.ui.mutes[i];
      this.app.engine.tracks[i].setMuted(this.ui.mutes[i]);
      this.app.lcdMsg(`${this.app.trackName(i)} ${this.ui.mutes[i] ? 'muted' : 'unmuted'}`);
    } else if (i >= 16) {
      this.scenePadPressed(i - 16);
    }
    this.render();
  }

  fxPressed(i) {
    const fx = this.project.fx;
    if (i < 16) {
      this.project.delayPreset = i;
      this.app.applyDelayPreset(i);
      this.ui.fxMacroMode = 'delay';
      this.app.lcdMsg(`Delay: ${this.app.delayPresetName(i)}`);
      this.app.updateKnobs();
    } else if (i < 24) {
      this.project.reverbPreset = i - 16;
      this.app.applyReverbPreset(i - 16);
      this.ui.fxMacroMode = 'reverb';
      this.app.lcdMsg(`Reverb: ${this.app.reverbPresetName(i - 16)}`);
      this.app.updateKnobs();
    } else if (i === 24) {
      fx.fxBypass = !fx.fxBypass;
      this.app.engine.setFxBypass(fx.fxBypass);
      this.app.lcdMsg(`FX ${fx.fxBypass ? 'off' : 'on'}`);
      this.app.refreshSidebar();
    }
    this.render();
  }

  sidechainPressed(i) {
    const [a, b] = this.scTracks();
    if (i >= 4 && i < 8) {
      const sc = this.project.sidechain[this.ui.scFocus];
      sc.source = i - 4;
      this.app.engine.configureSidechain(this.ui.scFocus, sc);
      this.app.lcdMsg(`Side chain source: Drum ${i - 3}`);
    } else if (i >= 16 && i < 32) {
      const scIdx = i < 24 ? a : b;
      const preset = i % 8;
      const sc = this.project.sidechain[scIdx];
      sc.preset = preset;
      if (preset > 0) {
        Object.assign(sc, SIDECHAIN_PRESETS[preset]);
        if (sc.source > 3) sc.source = 0;
      }
      this.ui.scFocus = scIdx;
      this.app.engine.configureSidechain(scIdx, sc);
      const names = ['Synth 1', 'Synth 2', 'MIDI 1', 'MIDI 2'];
      this.app.lcdMsg(`${names[scIdx]} side chain: ${preset === 0 ? 'OFF' : `preset ${preset}`}`);
    }
    this.render();
  }

  scalesPressed(i) {
    if (i < 16) {
      const layout = [
        [null, 1, 3, null, 6, 8, 10, null],
        [0, 2, 4, 5, 7, 9, 11, null],
      ];
      const pc = layout[Math.floor(i / 8)][i % 8];
      if (pc != null) {
        this.project.scaleRoot = pc;
        this.app.lcdMsg(`Root: ${SCALE_ROOTS[pc]}`);
      }
    } else {
      this.project.scaleType = i - 16;
      this.app.lcdMsg(`Scale: ${SCALE_TYPES[i - 16].name}`);
    }
    this.app.refreshSidebar();
    this.render();
  }

  presetPressed(i) {
    const t = this.ui.currentTrack;
    const kind = this.app.trackKind(t);
    if (kind === 'drum') {
      const idx = Math.min(1, this.ui.presetPage) * 32 + i;
      if (idx < 64) {
        const drumIdx = t - 4;
        if (this.ui.recording && this.app.seq.playing) {
          // Sample Flip from Preset View (guide p.62).
          this.app.drums.play(drumIdx, this.app.engine.now(), 110, idx);
          this.app.seq.recordNote(t, 0, 110, idx);
        } else {
          this.project.drumConfigs[drumIdx].patchSelect = idx;
          this.app.drums.applyConfig(drumIdx, { patchSelect: idx });
          this.app.drums.play(drumIdx, this.app.engine.now(), 110);
          this.ui.samplePage[drumIdx] = Math.floor(idx / 16);
          this.app.lcdMsg(`D${drumIdx + 1}: ${this.app.drums.sampleName(idx)}`);
          this.app.refreshSidebar();
        }
      }
    } else if (kind === 'midi') {
      if (i < 8) {
        this.ui.midiTemplate[t - 2] = i;
        this.app.lcdMsg(`${this.app.trackName(t)}: Template ${i + 1}`);
      }
    } else {
      const bank = this.app.patchBank;
      const page = Math.min(this.ui.presetPage, Math.max(0, Math.ceil(bank.length / 32) - 1));
      const idx = page * 32 + i;
      if (idx < bank.length) {
        // Audition the new patch for as long as the pad is held. The patch
        // fetch is async: only start (or keep) notes if the pad is still down.
        this.presetHeld = i;
        this.app.loadPatchFromBank(t, idx).then(() => {
          if (this.presetHeld === i) this.auditionPreset(t);
        });
      }
    }
    this.render();
  }

  // Hearing a freshly selected preset: the scale root — a diatonic triad
  // for poly patches, just the root for mono ones. Held until pad release.
  auditionPreset(t) {
    const synth = this.app.synthTracks[t];
    if (!synth) return;
    this.releaseAudition();
    const layout = keyboardLayout(
      this.project.scaleRoot, this.project.scaleType,
      this.ui.octave[Math.min(t, 3)], false,
    );
    const poly = (synth.patch.params.polyphony_mode ?? 2) >= 2;
    const padIdx = poly ? [24, 26, 28] : [24]; // scale degrees 0-2-4
    const now = this.app.engine.now();
    for (const p of padIdx) {
      const key = layout[p];
      if (!key) continue;
      synth.noteOn(now, key.midi, 100);
      this.auditionHeld.push({ trackId: t, midi: key.midi });
    }
  }

  // ---------- Playback visuals ----------

  applyVisualEvents(events) {
    let dirty = false;
    const nowMs = performance.now();
    const stepViews = ['note', 'velocity', 'gate', 'probability', 'microStep'];
    for (const e of events) {
      if (e.type === 'step') {
        if (this.playheadStep[e.trackId] !== e.step || this.playingPattern[e.trackId] !== e.patIdx) {
          this.playheadStep[e.trackId] = e.step;
          this.playingPattern[e.trackId] = e.patIdx;
          if (e.trackId === this.ui.currentTrack || this.ui.view === 'patterns') dirty = true;
          // Auto-follow the playing half of a 32-step pattern (guide p.75).
          if (e.trackId === this.ui.currentTrack && stepViews.includes(this.ui.view)
            && !(this.ui.view === 'note' && this.ui.noteExpanded)) {
            const page = Math.floor(e.step / 16);
            if (page !== this.ui.stepPage) {
              this.ui.stepPage = page;
              this.app.updateStepPageButton();
              dirty = true;
            }
          }
        }
      } else if (e.type === 'note') {
        this.activeNotes.push({ trackId: e.trackId, midi: e.midi, until: nowMs + Math.min(e.dur, 2) * 1000 });
        if (e.trackId === this.ui.currentTrack && this.ui.view === 'note') dirty = true;
      } else if (e.type === 'drumhit') {
        this.drumFlashes.push({ trackId: e.trackId, sample: e.sample, until: nowMs + 130 });
        if (e.trackId === this.ui.currentTrack && this.ui.view === 'note') dirty = true;
      }
    }
    if (dirty) this.render();
  }

  // Called from the rAF loop; expires note/drum highlights.
  tickVisuals() {
    const nowMs = performance.now();
    const beforeN = this.activeNotes.length;
    const beforeD = this.drumFlashes.length;
    this.activeNotes = this.activeNotes.filter((n) => n.until > nowMs);
    this.drumFlashes = this.drumFlashes.filter((f) => f.until > nowMs);
    if ((this.activeNotes.length !== beforeN || this.drumFlashes.length !== beforeD)
      && this.ui.view === 'note') {
      this.render();
    }
    // Flash a queued project switch at ~12 fps.
    if (this.ui.view === 'projects' && this.app.pendingProject
      && nowMs - (this._lastProjectFlash ?? 0) > 80) {
      this._lastProjectFlash = nowMs;
      this.render();
    }
    // Animate the scene pads (pulsing/flashing green) at ~12 fps.
    if (this.ui.view === 'mixer' && nowMs - this._lastScenePulse > 80) {
      const sc = this.app.seq.sceneState;
      const animated = sc || (this.activeScene >= 0 && this.scenesMatchCurrent(this.activeScene));
      if (animated || this.assignFlash) {
        this._lastScenePulse = nowMs;
        this.render();
      }
    }
  }

  clearPlayheads() {
    this.playheadStep.fill(-1);
    this.activeNotes = [];
    this.drumFlashes = [];
    this.render();
  }
}
