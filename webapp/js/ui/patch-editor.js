// Patch Builder — a dedicated full-screen synth editor.
//
// Edits the decoded patch on the selected synth track (0 or 1). Every control
// mutates the live patch object (params / modMatrix / macros) and calls
// app.onPatchEdited(synthIdx), which re-applies it to the audio engine,
// regenerates the 340-byte raw for .syx export, and persists via autosave.
// An on-screen keyboard auditions the sound in real time.
import {
  OSC_WAVEFORMS, FILTER_TYPES, DISTORTION_TYPES, LFO_WAVEFORMS,
  MACRO_DESTINATIONS, MOD_MATRIX_SOURCES, MOD_MATRIX_DESTINATIONS, TRACK_COLORS,
} from '../constants.js';
import { initPatch, parseSyxPatch } from '../patch.js';

const clamp127 = (v) => Math.max(0, Math.min(127, Math.round(v)));
const cutoffHz = (v) => 20 * Math.pow(1000, clamp127(v) / 127);

// Format an engineering value for a control's readout.
function fmt(value, p) {
  if (p.hz) {
    const hz = cutoffHz(value);
    return hz >= 1000 ? `${(hz / 1000).toFixed(1)}k` : `${Math.round(hz)}Hz`;
  }
  if (p.center) {
    const d = value - 64;
    return d > 0 ? `+${d}` : `${d}`;
  }
  return `${value}`;
}

// Declarative schema: each section lists its parameters and how to render them.
// type: 'knob' (default), 'select' (enumerated), with optional center/hz display.
const SECTIONS = [
  {
    title: 'Voice', cat: 'voice', params: [
      { name: 'polyphony_mode', label: 'Poly', type: 'select', options: { 0: 'mono', 1: 'mono legato', 2: 'poly' } },
      { name: 'portamento_rate', label: 'Portamento' },
      { name: 'pre_glide', label: 'Pre-glide', center: true },
      { name: 'keyboard_octave', label: 'Kbd Oct', center: true },
    ],
  },
  {
    title: 'Oscillator 1', cat: 'osc', params: [
      { name: 'osc1_wave', label: 'Wave', type: 'select', options: OSC_WAVEFORMS },
      { name: 'osc1_wave_interpolate', label: 'Interp' },
      { name: 'osc1_pulse_width_index', label: 'PW' },
      { name: 'osc1_virtual_sync_depth', label: 'V-Sync' },
      { name: 'osc1_density', label: 'Density' },
      { name: 'osc1_density_detune', label: 'Detune' },
      { name: 'osc1_semitones', label: 'Semis', center: true },
      { name: 'osc1_cents', label: 'Cents', center: true },
      { name: 'osc1_pitchbend', label: 'Bend', center: true },
    ],
  },
  {
    title: 'Oscillator 2', cat: 'osc', params: [
      { name: 'osc2_wave', label: 'Wave', type: 'select', options: OSC_WAVEFORMS },
      { name: 'osc2_wave_interpolate', label: 'Interp' },
      { name: 'osc2_pulse_width_index', label: 'PW' },
      { name: 'osc2_virtual_sync_depth', label: 'V-Sync' },
      { name: 'osc2_density', label: 'Density' },
      { name: 'osc2_density_detune', label: 'Detune' },
      { name: 'osc2_semitones', label: 'Semis', center: true },
      { name: 'osc2_cents', label: 'Cents', center: true },
      { name: 'osc2_pitchbend', label: 'Bend', center: true },
    ],
  },
  {
    title: 'Mixer', cat: 'mix', params: [
      { name: 'osc1_level', label: 'Osc 1' },
      { name: 'osc2_level', label: 'Osc 2' },
      { name: 'ring_mod_level', label: 'Ring' },
      { name: 'noise_level', label: 'Noise' },
      { name: 'pre_fx_level', label: 'Pre FX' },
      { name: 'post_fx_level', label: 'Post FX' },
    ],
  },
  {
    title: 'Filter', cat: 'filter', params: [
      { name: 'filter_type', label: 'Type', type: 'select', options: FILTER_TYPES },
      { name: 'filter_frequency', label: 'Freq', hz: true },
      { name: 'filter_resonance', label: 'Reso' },
      { name: 'filter_tracking', label: 'Track' },
      { name: 'filter_q_normalize', label: 'Q Norm' },
      { name: 'env2_to_filter_freq', label: 'Env→Freq', center: true },
      { name: 'drive', label: 'Drive' },
      { name: 'drive_type', label: 'Drive Type' },
      { name: 'routing', label: 'Routing' },
    ],
  },
  {
    title: 'Amp Envelope (1)', cat: 'env', params: [
      { name: 'env1_attack', label: 'Attack' },
      { name: 'env1_decay', label: 'Decay' },
      { name: 'env1_sustain', label: 'Sustain' },
      { name: 'env1_release', label: 'Release' },
      { name: 'env1_velocity', label: 'Vel', center: true },
    ],
  },
  {
    title: 'Filter Envelope (2)', cat: 'env', params: [
      { name: 'env2_attack', label: 'Attack' },
      { name: 'env2_decay', label: 'Decay' },
      { name: 'env2_sustain', label: 'Sustain' },
      { name: 'env2_release', label: 'Release' },
      { name: 'env2_velocity', label: 'Vel', center: true },
    ],
  },
  {
    title: 'Mod Envelope (3)', cat: 'env', params: [
      { name: 'env3_delay', label: 'Delay' },
      { name: 'env3_attack', label: 'Attack' },
      { name: 'env3_decay', label: 'Decay' },
      { name: 'env3_sustain', label: 'Sustain' },
      { name: 'env3_release', label: 'Release' },
    ],
  },
  {
    title: 'LFO 1', cat: 'lfo', params: [
      { name: 'lfo1_waveform', label: 'Wave', type: 'select', options: LFO_WAVEFORMS },
      { name: 'lfo1_rate', label: 'Rate' },
      { name: 'lfo1_rate_sync', label: 'Rate Sync' },
      { name: 'lfo1_phase_offset', label: 'Phase' },
      { name: 'lfo1_slew_rate', label: 'Slew' },
      { name: 'lfo1_delay', label: 'Delay' },
      { name: 'lfo1_delay_sync', label: 'Dly Sync' },
      { name: 'lfo1_flags', label: 'Flags' },
    ],
  },
  {
    title: 'LFO 2', cat: 'lfo', params: [
      { name: 'lfo2_waveform', label: 'Wave', type: 'select', options: LFO_WAVEFORMS },
      { name: 'lfo2_rate', label: 'Rate' },
      { name: 'lfo2_rate_sync', label: 'Rate Sync' },
      { name: 'lfo2_phase_offset', label: 'Phase' },
      { name: 'lfo2_slew_rate', label: 'Slew' },
      { name: 'lfo2_delay', label: 'Delay' },
      { name: 'lfo2_delay_sync', label: 'Dly Sync' },
      { name: 'lfo2_flags', label: 'Flags' },
    ],
  },
  {
    title: 'Distortion / Chorus', cat: 'fx', params: [
      { name: 'distortion_type', label: 'Dist Type', type: 'select', options: DISTORTION_TYPES },
      { name: 'distortion_level', label: 'Dist Lvl' },
      { name: 'distortion_compensation', label: 'Dist Comp' },
      { name: 'chorus_level', label: 'Chorus Lvl' },
      { name: 'chorus_rate', label: 'Cho Rate' },
      { name: 'chorus_rate_sync', label: 'Cho Sync' },
      { name: 'chorus_feedback', label: 'Cho Fbk' },
      { name: 'chorus_mod_depth', label: 'Cho Depth' },
      { name: 'chorus_delay', label: 'Cho Delay' },
    ],
  },
  {
    title: 'EQ', cat: 'fx', params: [
      { name: 'eq_bass_frequency', label: 'Bass Freq' },
      { name: 'eq_bass_level', label: 'Bass', center: true },
      { name: 'eq_mid_frequency', label: 'Mid Freq' },
      { name: 'eq_mid_level', label: 'Mid', center: true },
      { name: 'eq_treble_frequency', label: 'Treb Freq' },
      { name: 'eq_treble_level', label: 'Treble', center: true },
    ],
  },
];

// Macro destination dropdown options (hardware-valid list only, 0-70).
const MACRO_DEST_OPTIONS = Object.entries(MACRO_DESTINATIONS)
  .map(([idx, name]) => ({ idx: Number(idx), name }));

export class PatchEditor {
  constructor(app) {
    this.app = app;
    this.synthIdx = 0;
    this.patch = null;       // decoded patch of the current track (live ref)
    this.keyboardBase = 48;  // C3
    this.heldKeys = new Set();
    this.controls = [];      // {el, sync} for refresh()
    this.root = null;
  }

  get synth() { return this.app.synthTracks[this.synthIdx]; }

  // ---- helpers ----
  el(tag, cls, parent, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    parent?.appendChild(e);
    return e;
  }

  // Commit a change: keep the engine + persistence in sync with the patch.
  commit() { this.app.onPatchEdited(this.synthIdx); }

  // ---- build ----
  buildDom(container) {
    this.root = container;
    container.innerHTML = '';
    container.classList.add('patch-builder');
    // Seed from the default track's patch so initial control rendering (which
    // reads this.patch) has data; load() re-syncs when the screen is opened.
    this.patch = this.synth.patch;

    // Header / toolbar.
    const head = this.el('div', 'pb-head', container);
    const left = this.el('div', 'pb-head-left', head);
    const back = this.el('button', 'fn-btn pb-back', left, '← Tracks');
    back.addEventListener('click', () => this.app.setAppMode('tracks'));
    this.el('span', 'pb-title', left, 'Patch Builder');

    const trackBox = this.el('div', 'pb-track-toggle', left);
    this.trackBtns = [0, 1].map((i) => {
      const b = this.el('button', 'fn-btn', trackBox, `Synth ${i + 1}`);
      b.style.setProperty('--track-color', TRACK_COLORS[i]);
      b.addEventListener('click', () => this.load(i));
      return b;
    });

    const right = this.el('div', 'pb-head-right', head);
    this.nameInput = this.el('input', 'pb-name', right);
    this.nameInput.maxLength = 16;
    this.nameInput.placeholder = 'Patch name';
    this.nameInput.addEventListener('input', () => {
      this.patch.name = this.nameInput.value.slice(0, 16);
      this.commit();
    });
    const initBtn = this.el('button', 'fn-btn', right, 'Init');
    initBtn.addEventListener('click', () => this.initPatch());
    const loadBtn = this.el('button', 'fn-btn', right, 'Load .syx…');
    this.fileInput = this.el('input', 'pb-file', right);
    this.fileInput.type = 'file';
    this.fileInput.accept = '.syx';
    this.fileInput.style.display = 'none';
    loadBtn.addEventListener('click', () => this.fileInput.click());
    this.fileInput.addEventListener('change', (e) => this.loadPresetFile(e.target.files[0]));
    const exportBtn = this.el('button', 'fn-btn pb-export', right, 'Export .syx');
    exportBtn.addEventListener('click', () => this.app.exportPatchSyx(this.synthIdx));

    // Scrollable body with section cards.
    const body = this.el('div', 'pb-body', container);
    this.grid = this.el('div', 'pb-grid', body);
    for (const section of SECTIONS) this.buildSection(this.grid, section);
    this.buildModMatrix(this.grid);
    this.buildMacros(this.grid);

    // On-screen keyboard, pinned at the bottom.
    this.buildKeyboard(container);
  }

  buildSection(parent, section) {
    const card = this.el('div', `pb-card cat-${section.cat}`, parent);
    this.el('div', 'pb-card-title', card, section.title);
    const grid = this.el('div', 'pb-controls', card);
    for (const p of section.params) {
      if (p.type === 'select') this.addSelect(grid, p);
      else this.addKnob(grid, p);
    }
  }

  addKnob(parent, p) {
    const wrap = this.el('div', 'pb-ctl', parent);
    const knob = this.el('div', 'pb-knob', wrap);
    this.el('div', 'pb-knob-grip', knob);
    this.el('div', 'pb-knob-ind', knob);
    this.el('div', 'pb-ctl-label', wrap, p.label);
    const val = this.el('div', 'pb-ctl-val', wrap);

    const get = () => this.patch.params[p.name] ?? 0;
    const setVisual = (v) => {
      const deg = -135 + (clamp127(v) / 127) * 270;
      knob.querySelector('.pb-knob-grip').style.transform = `rotate(${deg}deg)`;
      knob.querySelector('.pb-knob-ind').style.transform = `rotate(${deg}deg)`;
      val.textContent = fmt(clamp127(v), p);
    };
    const apply = (v) => {
      this.patch.params[p.name] = clamp127(v);
      setVisual(v);
      this.commit();
    };
    this.attachDrag(wrap, get, apply);
    this.controls.push({ sync: () => setVisual(get()) });
    setVisual(get());
  }

  addSelect(parent, p) {
    const wrap = this.el('div', 'pb-ctl pb-ctl-wide', parent);
    this.el('div', 'pb-ctl-label', wrap, p.label);
    const sel = this.el('select', 'pb-select', wrap);
    for (const [v, name] of Object.entries(p.options)) {
      const opt = this.el('option', null, sel, name);
      opt.value = v;
    }
    sel.addEventListener('change', () => {
      this.patch.params[p.name] = clamp127(Number(sel.value));
      this.commit();
    });
    this.controls.push({ sync: () => { sel.value = String(this.patch.params[p.name] ?? 0); } });
    sel.value = String(this.patch.params[p.name] ?? 0);
  }

  // Vertical-drag + wheel handler, matching the device's macro knobs exactly:
  // the mouse is pointer-locked while turning (so leaving/re-entering the knob
  // can't move it), with touch/pen falling back to pointer capture.
  attachDrag(target, getValue, setValue) {
    let drag = null;
    target.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      drag = { lastY: e.clientY, v: getValue(), locked: false };
      if (e.pointerType === 'mouse' && target.requestPointerLock) {
        try {
          target.requestPointerLock();
          drag.locked = true;
        } catch { /* pointer lock unavailable — capture instead */ }
      }
      if (!drag.locked) {
        try { target.setPointerCapture(e.pointerId); } catch { /* synthetic pointer */ }
      }
    });
    target.addEventListener('pointermove', (e) => {
      if (!drag) return;
      if (drag.locked && document.pointerLockElement !== target) return;
      const scale = this.app.ui.shift ? 0.1 : 0.4;
      const dy = drag.locked ? -e.movementY : (drag.lastY - e.clientY);
      drag.lastY = e.clientY;
      drag.v = Math.max(0, Math.min(127, drag.v + dy * scale));
      setValue(Math.round(drag.v));
    });
    const end = () => {
      if (drag?.locked && document.pointerLockElement === target) document.exitPointerLock();
      drag = null;
    };
    target.addEventListener('pointerup', end);
    target.addEventListener('pointercancel', end);
    document.addEventListener('pointerlockchange', () => {
      if (drag?.locked && document.pointerLockElement !== target) drag = null;
    });
    target.addEventListener('wheel', (e) => {
      e.preventDefault();
      const step = this.app.ui.shift ? 1 : 4;
      setValue(Math.max(0, Math.min(127, getValue() - Math.sign(e.deltaY) * step)));
    }, { passive: false });
  }

  buildModMatrix(parent) {
    const card = this.el('div', 'pb-card cat-mod pb-card-wide', parent);
    this.el('div', 'pb-card-title', card, 'Mod Matrix — source × source → destination');
    const table = this.el('div', 'pb-modmatrix', card);
    const header = this.el('div', 'pb-mod-row pb-mod-head', table);
    ['#', 'Source 1', 'Source 2', 'Destination', 'Depth'].forEach((h) => this.el('span', null, header, h));
    this.modInputs = [];

    // Set a named modMatrix field + keep the mirrored mod*_* param in sync
    // (encode treats modMatrix as authoritative, but the engine reads
    // modN_depth as a macro base value).
    const setField = (s, key, v) => {
      const cv = clamp127(v);
      this.patch.modMatrix[s][key] = cv;
      this.patch.params[`mod${s + 1}_${key}`] = cv;
      this.commit();
    };

    for (let s = 0; s < 20; s++) {
      const row = this.el('div', 'pb-mod-row', table);
      this.el('span', 'pb-mod-idx', row, `${s + 1}`);

      const src1 = this.mkSelect(row, MOD_MATRIX_SOURCES, (v) => setField(s, 'source1', v));
      const src2 = this.mkSelect(row, MOD_MATRIX_SOURCES, (v) => setField(s, 'source2', v));
      const dest = this.mkSelect(row, MOD_MATRIX_DESTINATIONS, (v) => setField(s, 'destination', v));

      // Depth is bipolar: 0-127, 64 = none/centre.
      const depth = this.el('input', 'pb-mod-input', row);
      depth.type = 'number'; depth.min = -63; depth.max = 63;
      depth.addEventListener('change', () => {
        const d = Math.max(-63, Math.min(63, Number(depth.value) || 0));
        setField(s, 'depth', d + 64);
        depth.value = d;
      });

      this.modInputs.push({ s, src1, src2, dest, depth });
    }
    // Select an option, adding a fallback "#<n>" entry if the stored byte isn't
    // a documented source/destination (keeps an unknown value visible, not lost).
    const setSel = (sel, v) => {
      sel.value = String(v);
      if (sel.selectedIndex === -1) {
        const opt = this.el('option', null, sel, `#${v}`);
        opt.value = String(v);
        sel.value = String(v);
      }
    };
    this.controls.push({
      sync: () => {
        for (const { s, src1, src2, dest, depth } of this.modInputs) {
          const slot = this.patch.modMatrix[s];
          setSel(src1, slot.source1);
          setSel(src2, slot.source2);
          setSel(dest, slot.destination);
          depth.value = (slot.depth ?? 64) - 64;
        }
      },
    });
  }

  // A <select> populated from an index→name map; calls onChange(numericValue).
  mkSelect(parent, optionsMap, onChange) {
    const sel = this.el('select', 'pb-select pb-mod-select', parent);
    for (const [v, name] of Object.entries(optionsMap)) {
      const opt = this.el('option', null, sel, name);
      opt.value = v;
    }
    sel.addEventListener('change', () => onChange(Number(sel.value)));
    return sel;
  }

  buildMacros(parent) {
    const card = this.el('div', 'pb-card cat-macro pb-card-wide', parent);
    this.el('div', 'pb-card-title', card, 'Macros — knob assignments');
    const grid = this.el('div', 'pb-macros', card);
    this.macroUis = [];
    for (let k = 0; k < 8; k++) this.buildMacro(grid, k);
  }

  buildMacro(parent, k) {
    const box = this.el('div', 'pb-macro', parent);
    const head = this.el('div', 'pb-macro-head', box);

    // Position knob — turning it drives the live macro, so assignments are audible.
    const knob = this.el('div', 'pb-knob pb-macro-knob', head);
    this.el('div', 'pb-knob-grip', knob);
    this.el('div', 'pb-knob-ind', knob);
    this.el('div', 'pb-ctl-label', head, `Macro ${k + 1}`);
    const get = () => this.patch.macros[k].position ?? 0;
    const setVisual = (v) => {
      const deg = -135 + (clamp127(v) / 127) * 270;
      knob.querySelector('.pb-knob-grip').style.transform = `rotate(${deg}deg)`;
      knob.querySelector('.pb-knob-ind').style.transform = `rotate(${deg}deg)`;
    };
    const apply = (v) => {
      this.patch.macros[k].position = clamp127(v);
      setVisual(v);
      this.commit();
    };
    this.attachDrag(knob, get, apply);
    setVisual(get());

    // Four target rows. The engine iterates macro.targets with for..of, so the
    // array must stay compact (no holes): on any change we rebuild it from the
    // non-empty rows, in row order.
    const rows = this.el('div', 'pb-macro-targets', box);
    // Column header so the three number fields aren't unlabelled.
    const colHead = this.el('div', 'pb-macro-row pb-macro-colhead', rows);
    ['Destination', 'Start', 'End', 'Depth'].forEach((h, i) => {
      const span = this.el('span', null, colHead, h);
      span.title = [
        'Engine parameter this knob drives',
        'Knob value where the sweep begins (0–127)',
        'Knob value where the sweep ends (0–127)',
        'Amount added at full sweep (−63…+63)',
      ][i];
    });
    const rowUis = [];
    const rebuild = () => {
      this.patch.macros[k].targets = rowUis.map((r) => r.read()).filter(Boolean);
      this.commit();
      this.refresh();
    };
    for (let tIdx = 0; tIdx < 4; tIdx++) rowUis.push(this.buildMacroTarget(rows, k, tIdx, rebuild));

    const sync = () => {
      setVisual(get());
      const targets = this.patch.macros[k].targets;
      rowUis.forEach((r, i) => r.sync(targets[i] ?? null));
    };
    this.macroUis.push({ k, sync });
    this.controls.push({ sync });
  }

  buildMacroTarget(parent, k, tIdx, rebuild) {
    const row = this.el('div', 'pb-macro-row', parent);
    const destSel = this.el('select', 'pb-select pb-macro-dest', row);
    this.el('option', null, destSel, '— none —').value = '-1';
    for (const d of MACRO_DEST_OPTIONS) {
      const opt = this.el('option', null, destSel, d.name);
      opt.value = String(d.idx);
    }
    const mkNum = (cls, ph) => {
      const inp = this.el('input', `pb-mod-input ${cls}`, row);
      inp.type = 'number'; inp.min = cls === 'pb-depth' ? -63 : 0;
      inp.max = cls === 'pb-depth' ? 63 : 127;
      inp.title = ph;
      return inp;
    };
    const startInp = mkNum('pb-start', 'start');
    const endInp = mkNum('pb-end', 'end');
    const depthInp = mkNum('pb-depth', 'depth');

    // Read this row's current UI into a target object (or null if no dest set).
    const read = () => {
      const destIdx = Number(destSel.value);
      if (destIdx < 0) return null;
      let start = clamp127(Number(startInp.value || 0));
      let end = clamp127(Number(endInp.value || 127));
      if (end <= start) end = Math.min(127, start + 1); // encode needs end > start
      let depth = Math.max(-63, Math.min(63, Number(depthInp.value || 0)));
      if (depth === 0) depth = 32; // depth 0 would not round-trip as active
      return { destination: destIdx, start, end, depth };
    };
    destSel.addEventListener('change', rebuild);
    [startInp, endInp, depthInp].forEach((inp) => inp.addEventListener('change', rebuild));

    const sync = (t) => {
      if (t) {
        destSel.value = String(t.destination);
        startInp.value = t.start; endInp.value = t.end; depthInp.value = t.depth;
        startInp.disabled = endInp.disabled = depthInp.disabled = false;
      } else {
        destSel.value = '-1';
        startInp.value = 0; endInp.value = 127; depthInp.value = 0;
        startInp.disabled = endInp.disabled = depthInp.disabled = true;
      }
    };
    return { read, sync };
  }

  buildKeyboard(container) {
    const bar = this.el('div', 'pb-keyboard', container);
    const ctrls = this.el('div', 'pb-kb-ctrls', bar);
    const down = this.el('button', 'fn-btn', ctrls, 'Oct −');
    const up = this.el('button', 'fn-btn', ctrls, 'Oct +');
    this.octLabel = this.el('span', 'pb-kb-oct', ctrls);
    const hold = this.el('button', 'fn-btn pb-hold', ctrls, 'Hold');
    down.addEventListener('click', () => { this.keyboardBase = Math.max(12, this.keyboardBase - 12); this.renderKeys(); });
    up.addEventListener('click', () => { this.keyboardBase = Math.min(108, this.keyboardBase + 12); this.renderKeys(); });
    hold.addEventListener('click', () => {
      this.holdMode = !this.holdMode;
      hold.classList.toggle('active', this.holdMode);
      if (!this.holdMode) this.allNotesOff();
    });
    this.keysEl = this.el('div', 'pb-keys', bar);
    this.renderKeys();
  }

  renderKeys() {
    this.allNotesOff();
    this.keysEl.innerHTML = '';
    const oct = Math.round(this.keyboardBase / 12) - 1;
    this.octLabel.textContent = `C${oct}`;
    const isBlack = (n) => [1, 3, 6, 8, 10].includes(n % 12);

    const range = 25; // two octaves + top C
    const midis = Array.from({ length: range }, (_, i) => this.keyboardBase + i);
    const whites = midis.filter((m) => !isBlack(m));
    const whiteCount = whites.length;
    const whiteIndex = (m) => whites.indexOf(m);

    const whiteLayer = this.el('div', 'pb-keys-white', this.keysEl);
    const blackLayer = this.el('div', 'pb-keys-black', this.keysEl);
    const blackW = (100 / whiteCount) * 0.62;

    const bindKey = (key, midi) => {
      key.dataset.midi = midi;
      const press = (e) => {
        e.preventDefault();
        if (this.holdMode && this.heldKeys.has(midi)) this.noteOff(midi, key);
        else this.noteOn(midi, key);
      };
      const release = () => { if (!this.holdMode) this.noteOff(midi, key); };
      key.addEventListener('pointerdown', press);
      key.addEventListener('pointerup', release);
      key.addEventListener('pointerleave', release);
      key.addEventListener('pointercancel', release);
    };

    for (const m of midis) {
      if (isBlack(m)) {
        const key = this.el('div', 'pb-key black', blackLayer);
        // Sits on the boundary after the preceding white key (midi m-1).
        const left = ((whiteIndex(m - 1) + 1) / whiteCount) * 100 - blackW / 2;
        key.style.left = `${left}%`;
        key.style.width = `${blackW}%`;
        bindKey(key, m);
      } else {
        const key = this.el('div', 'pb-key white', whiteLayer);
        bindKey(key, m);
      }
    }
  }

  noteOn(midi, key) {
    this.app.engine.resume?.();
    this.synth.noteOn(this.app.engine.now(), midi, 100);
    this.heldKeys.add(midi);
    key?.classList.add('on');
  }

  noteOff(midi, key) {
    this.synth.noteOff(midi, this.app.engine.now());
    this.heldKeys.delete(midi);
    key?.classList.remove('on');
  }

  allNotesOff() {
    for (const midi of [...this.heldKeys]) this.noteOff(midi);
    this.keysEl?.querySelectorAll('.pb-key.on').forEach((k) => k.classList.remove('on'));
    this.heldKeys.clear();
  }

  // ---- load / refresh ----
  load(synthIdx) {
    this.synthIdx = synthIdx;
    this.patch = this.synth.patch;
    this.trackBtns?.forEach((b, i) => b.classList.toggle('active', i === synthIdx));
    this.root?.style.setProperty('--pb-accent', TRACK_COLORS[synthIdx]);
    this.refresh();
  }

  // Re-sync every control's displayed value/position from the current patch.
  refresh() {
    if (!this.patch) return;
    if (this.nameInput) this.nameInput.value = this.patch.name || '';
    for (const c of this.controls) c.sync();
  }

  initPatch() {
    const patch = initPatch();
    this.synth.setPatch(patch);
    this.patch = patch;
    this.commit();
    this.refresh();
    this.app.lcdMsg?.('Patch reset to Init');
  }

  async loadPresetFile(file) {
    if (!file) return;
    try {
      const buf = await file.arrayBuffer();
      const patch = parseSyxPatch(buf);
      this.synth.setPatch(patch);
      this.patch = patch;
      this.commit();
      this.refresh();
      this.app.lcdMsg?.(`Loaded ${patch.name || file.name}`);
    } catch (err) {
      this.app.lcdMsg?.(`Load failed: ${err.message}`);
    }
    this.fileInput.value = '';
  }
}
