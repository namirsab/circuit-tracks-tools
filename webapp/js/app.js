// Application bootstrap and glue.
import { AudioEngine } from './audio/engine.js';
import { SynthTrack } from './audio/synth.js';
import { DrumEngine } from './audio/drums.js';
import { Sequencer } from './sequencer.js';
import { parseNCS, serializeNCS } from './ncs.js';
import { decodePatch, parseSyxPatch } from './patch.js';
import { defaultProject, UIState } from './state.js';
import { buildPanel, buildSidebar } from './ui/panel.js';
import { Views } from './ui/views.js';
import { KeyboardInput, buildKeyOverlay } from './keyboard.js';
import { readZip, writeZip } from './zip.js';
import { ncsToMidi } from './scales.js';
import {
  TRACKS, TRACK_COLORS, SEND_ORDER, SCALE_TYPES, SCALE_ROOTS,
  REVERB_PRESETS, DELAY_PRESETS, REVERB_TYPES,
} from './constants.js';

const DELAY_PRESET_NAMES = [
  'Slapback Fast', 'Slapback Slow', '32nd Triplets', '32nd', '16th Triplets',
  '16th', '16th Ping Pong', '16th Ping Pong Swung', '8th Triplets',
  '8th dotted Ping Pong', '8th', '8th Ping Pong', '8th Ping Pong Swung',
  '4th Triplets', '4th dotted PP Swung', '4th Triplets PP Wide',
];
const REVERB_PRESET_NAMES = [
  'Small Chamber', 'Small Room 1', 'Small Room 2', 'Large Room',
  'Hall', 'Large Hall', 'Hall – long reflection', 'Large Hall – long refl.',
];

// Views whose button is a different physical button's shift function.
const VIEW_BUTTON = {
  note: 'btn-note', velocity: 'btn-velocity', gate: 'btn-gate',
  microStep: 'btn-gate', probability: 'btn-patternSettings',
  patternSettings: 'btn-patternSettings', patterns: 'btn-patterns',
  mixer: 'btn-mixer', fx: 'btn-fx', sidechain: 'btn-fx',
  scales: 'btn-scales', preset: 'btn-preset', tempo: 'btn-tempo',
};

class CircuitApp {
  constructor() {
    this.project = defaultProject();
    this.ui = new UIState();
    this.engine = new AudioEngine();
    this.synthTracks = [0, 1, 2, 3].map((t) => new SynthTrack(this.engine, t));
    this.drums = new DrumEngine(this.engine);
    this.seq = new Sequencer(this.engine, this.synthTracks, this.drums, this);
    this.lastNote = {};
    this.patchBank = [];
    this.projectBank = new Array(64).fill(null);
    this.packName = null;
    this.pendingProject = null;
    this._slotsDirty = false;
    this.masterVolumeValue = 102;
    this.masterFilterValue = 64;
    this.shiftLatched = false;
    this.shiftMomentary = false;

    const { pads, macroKnobs, trackButtons } = buildPanel(document.getElementById('panel-root'));
    this.pads = pads;
    this.macroKnobs = macroKnobs;
    this.trackButtons = trackButtons;
    buildSidebar(document.getElementById('sidebar'));
    buildKeyOverlay(document.getElementById('key-overlay'));

    this.views = new Views(this);
    new KeyboardInput(this);

    this.bindPads();
    this.bindButtons();
    this.bindKnobs();
    this.bindDragDrop();
    this.bindResume();

    this.samplesReady = this.loadPackFromUrl('pack/')
      .catch((err) => {
        console.warn('Pack loading failed:', err.message);
      })
      .finally(() => this.refreshSidebar());

    this.selectTrack(0);
    this.setView('note');
    this.refreshSidebar();
    this.updateLcd();

    // Music-reactive glow behind the device: one spot per track on the
    // panel's edges (synths/MIDI bleed out the left side, drums out the
    // right, top to bottom in track-button order), lit in the track's
    // colour whenever that track actually sounds (drum hits / synth notes)
    // and decaying over ~250ms. Only opacity is touched per frame, so the
    // effect stays compositor-only.
    const beatBg = document.getElementById('beat-bg');
    const beatSpots = [];
    for (let i = 0; i < 8; i++) {
      const s = document.createElement('span');
      s.className = 'beat-spot';
      s.style.setProperty('--c', TRACK_COLORS[i]);
      s.style.left = i < 4 ? '0%' : '100%';
      s.style.top = `${12.5 + (i % 4) * 25}%`;
      beatBg.appendChild(s);
      beatSpots.push(s);
    }
    const device = document.getElementById('device');
    const sidebar = document.getElementById('sidebar');
    const fitGlowToDevice = () => {
      const d = device.getBoundingClientRect();
      const s = sidebar.getBoundingClientRect();
      // Union of device + sidebar (sidebar collapses to 0 when hidden), so
      // synths glow out the left of the console and drums out the right.
      const left = Math.min(d.left, s.width ? s.left : d.left);
      const right = Math.max(d.right, s.width ? s.right : d.right);
      Object.assign(beatBg.style, {
        left: `${left}px`, top: `${d.top}px`, width: `${right - left}px`, height: `${d.height}px`,
      });
    };
    fitGlowToDevice();
    this.fitGlow = fitGlowToDevice;
    new ResizeObserver(fitGlowToDevice).observe(device);
    new ResizeObserver(fitGlowToDevice).observe(sidebar);
    window.addEventListener('resize', fitGlowToDevice);
    const beatLevels = new Float32Array(8);
    let beatLastMs = performance.now();

    const tick = () => {
      const events = this.seq.drainVisualEvents();
      if (events.length) this.views.applyVisualEvents(events);
      this.views.tickVisuals();
      if (this.pendingProject && this.engine.now() >= this.pendingProject.time) {
        this.loadProjectFromBank(this.pendingProject.idx);
      }
      const nowMs = performance.now();
      const fade = Math.exp(-(nowMs - beatLastMs) / 250);
      beatLastMs = nowMs;
      for (const e of events) {
        if (e.type === 'drumhit' || e.type === 'note') beatLevels[e.trackId] = 1;
      }
      for (let i = 0; i < 8; i++) {
        beatLevels[i] *= fade;
        if (beatLevels[i] < 0.012) beatLevels[i] = 0;
        beatSpots[i].style.opacity = (beatLevels[i] * 0.45).toFixed(3);
      }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  trackKind(t) { return TRACKS[t].kind; }
  trackName(t) { return TRACKS[t].name; }
  delayPresetName(i) { return DELAY_PRESET_NAMES[i] ?? `${i + 1}`; }
  reverbPresetName(i) { return REVERB_PRESET_NAMES[i] ?? `${i + 1}`; }
  ncsNoteToMidi(ncs) { return ncsToMidi(ncs, this.project.scaleRoot, this.project.scaleType); }

  currentEditPattern() {
    const t = this.ui.currentTrack;
    return this.project.patterns[t][this.ui.currentPattern[t]];
  }

  bindResume() {
    const resume = () => { this.engine.resume(); };
    window.addEventListener('pointerdown', resume, { passive: true });
    window.addEventListener('keydown', resume);

    // Scale the whole console — device AND sidebar — up on large screens
    // (zoom keeps layout + events consistent). The sidebar gets the same
    // factor so it doesn't look miniature next to the scaled device; its
    // 100vh max-height is divided back via --ui-zoom in CSS. Never scale
    // below 1 — small screens use the media query.
    const device = document.getElementById('device');
    const sidebar = document.getElementById('sidebar');
    const fit = () => {
      device.style.zoom = 1;
      sidebar.style.zoom = 1;
      document.documentElement.style.setProperty('--ui-zoom', 1);
      if (window.innerWidth >= 1250) {
        const sidebarW = document.body.classList.contains('sidebar-hidden')
          ? 0 : sidebar.offsetWidth + 16; // + flex gap
        const pad = 48;
        const scale = Math.min(
          (window.innerWidth - pad) / (device.offsetWidth + sidebarW),
          (window.innerHeight - 24) / device.offsetHeight,
          2.0,
        );
        if (scale > 1.02) {
          device.style.zoom = scale;
          sidebar.style.zoom = scale;
          document.documentElement.style.setProperty('--ui-zoom', scale);
        }
      }
      this.fitGlow?.();
    };
    this.fitDevice = fit;
    window.addEventListener('resize', fit);
    requestAnimationFrame(fit);
  }

  // ---------- Pads ----------
  bindPads() {
    this.pads.forEach((pad, i) => {
      pad.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        pad.setPointerCapture?.(e.pointerId);
        this.padPressed(i);
      });
      pad.addEventListener('pointerup', () => this.padReleased(i));
      pad.addEventListener('pointercancel', () => this.padReleased(i));
    });
  }

  padPressed(i) {
    this.engine.resume();
    this.views.padPressed(i);
  }

  padReleased(i) { this.views.padReleased(i); }

  // ---------- Live note input ----------
  liveNoteOn(midi, velocity = 100, opts = {}) {
    const t = this.ui.currentTrack;
    this.lastNote[t] = midi;
    if (t < 4) {
      this.synthTracks[t].noteOn(this.engine.now(), midi, velocity);
      if (!opts.recordless && this.ui.recording && this.seq.playing) {
        const ncs = Math.max(0, Math.min(127, midi - this.project.scaleRoot + 12));
        const rec = this.seq.recordNote(t, ncs, this.ui.fixedVelocity ? 96 : velocity);
        if (rec) {
          this.liveRecHandles = this.liveRecHandles ?? new Map();
          this.liveRecHandles.set(`${t}:${midi}`, rec);
        }
      }
    }
  }

  liveNoteOff(midi) {
    const t = this.ui.currentTrack;
    if (t < 4) {
      this.synthTracks[t].noteOff(midi);
      const rec = this.liveRecHandles?.get(`${t}:${midi}`);
      if (rec) {
        this.liveRecHandles.delete(`${t}:${midi}`);
        this.seq.finishRecordedNote(rec);
        this.views.render(); // gate view / step display may change
      }
    }
  }

  // ---------- Shift (momentary hold + sticky latch, guide p.17) ----------
  setShiftMomentary(on) {
    this.shiftMomentary = on;
    this.applyShift();
  }

  applyShift() {
    this.ui.shift = this.shiftMomentary || this.shiftLatched;
    document.getElementById('btn-shift').classList.toggle('active', this.ui.shift);
    document.getElementById('btn-shift').classList.toggle('latched', this.shiftLatched);
  }

  // Kept for the keyboard module.
  setShift(on) { this.setShiftMomentary(on); }

  // ---------- Buttons ----------
  bindButtons() {
    const byId = (id) => document.getElementById(id);

    byId('btn-note').addEventListener('click', () => {
      if (this.ui.shift || this.ui.view === 'note') {
        this.ui.noteExpanded = this.ui.view === 'note' ? !this.ui.noteExpanded : true;
      } else {
        this.ui.noteExpanded = false;
      }
      this.setView('note');
    });
    byId('btn-velocity').addEventListener('click', () => {
      if (this.ui.shift) {
        this.ui.fixedVelocity = !this.ui.fixedVelocity;
        this.lcdMsg(`Fixed velocity ${this.ui.fixedVelocity ? 'on (96)' : 'off'}`);
        byId('btn-velocity').classList.toggle('fixed-vel', this.ui.fixedVelocity);
        return;
      }
      this.setView('velocity');
    });
    byId('btn-gate').addEventListener('click', () => {
      this.setView(this.ui.shift || this.ui.view === 'gate' ? 'microStep' : 'gate');
    });
    byId('btn-patternSettings').addEventListener('click', () => {
      this.setView(this.ui.shift || this.ui.view === 'patternSettings' ? 'probability' : 'patternSettings');
    });
    byId('btn-fx').addEventListener('click', () => {
      this.setView(this.ui.shift || this.ui.view === 'fx' ? 'sidechain' : 'fx');
    });
    byId('btn-patterns').addEventListener('click', () => this.setView('patterns'));
    byId('btn-mixer').addEventListener('click', () => this.setView('mixer'));
    byId('btn-scales').addEventListener('click', () => this.setView('scales'));
    byId('btn-preset').addEventListener('click', () => {
      const t = this.ui.currentTrack;
      if (this.trackKind(t) === 'drum') {
        this.ui.presetPage = Math.floor(this.project.drumConfigs[t - 4].patchSelect / 32);
      }
      this.setView('preset');
    });
    byId('btn-tempo').addEventListener('click', () => {
      if (this.ui.shift) this.seq.tapTempo();
      else this.setView('tempo');
    });

    this.trackButtons.forEach((b) => {
      b.addEventListener('click', () => {
        this.selectTrack(Number(b.dataset.track));
        this.ui.noteExpanded = false;
        this.setView('note'); // track buttons always open Note View
      });
    });

    byId('btn-play').addEventListener('click', async () => {
      await this.engine.resume();
      await this.samplesReady;
      if (this.seq.playing) {
        this.seq.stop();
        this.pendingProject = null; // stopping cancels a queued project switch
      } else {
        this.seq.start(this.ui.shift); // Shift+Play resumes from last stop
      }
    });
    byId('btn-rec').addEventListener('click', () => {
      if (this.ui.shift) {
        // Shift+Record toggles Rec Quantise (guide p.64).
        this.ui.recQuantise = !this.ui.recQuantise;
        byId('btn-rec').classList.toggle('unquantised', !this.ui.recQuantise);
        this.lcdMsg(`Rec quantise ${this.ui.recQuantise ? 'on' : 'off (micro steps)'}`);
        return;
      }
      this.ui.recording = !this.ui.recording;
      byId('btn-rec').classList.toggle('active', this.ui.recording);
      this.lcdMsg(this.ui.recording ? 'Recording armed' : 'Recording off');
    });

    // Shift: hold = momentary, quick press = sticky latch toggle.
    const shiftBtn = byId('btn-shift');
    let shiftDownAt = 0;
    shiftBtn.addEventListener('pointerdown', () => {
      shiftDownAt = performance.now();
      this.setShiftMomentary(true);
    });
    const shiftUp = () => {
      if (!this.shiftMomentary) return;
      if (performance.now() - shiftDownAt < 300) this.shiftLatched = !this.shiftLatched;
      this.setShiftMomentary(false);
    };
    shiftBtn.addEventListener('pointerup', shiftUp);
    shiftBtn.addEventListener('pointerleave', () => {
      if (this.shiftMomentary && performance.now() - shiftDownAt >= 300) this.setShiftMomentary(false);
    });

    // Clear: hold modifier; Shift+Clear toggles the click track (p.87).
    const clearBtn = byId('btn-clear');
    clearBtn.addEventListener('pointerdown', () => {
      if (this.ui.shift) {
        this.seq.clickEnabled = !this.seq.clickEnabled;
        this.lcdMsg(`Click ${this.seq.clickEnabled ? 'on' : 'off'}`);
        clearBtn.classList.toggle('click-on', this.seq.clickEnabled);
        return;
      }
      this.ui.clearHeld = true;
      clearBtn.classList.add('active');
    });
    const clearUp = () => {
      this.ui.clearHeld = false;
      clearBtn.classList.remove('active');
    };
    clearBtn.addEventListener('pointerup', clearUp);
    clearBtn.addEventListener('pointerleave', clearUp);

    // Duplicate: hold for copy/paste in Patterns View; Shift+Duplicate = Mutate.
    const dupBtn = byId('btn-duplicate');
    dupBtn.addEventListener('pointerdown', () => {
      if (this.ui.shift) {
        this.seq.mutate(this.ui.currentTrack);
        this.lcdMsg('Mutated');
        this.views.render();
        return;
      }
      this.ui.duplicateHeld = true;
      this.ui.copySource = { trackId: this.ui.currentTrack, patIdx: this.ui.currentPattern[this.ui.currentTrack] };
      dupBtn.classList.add('active');
      this.lcdMsg(`Copy source: ${this.trackName(this.ui.currentTrack)} pattern ${this.ui.copySource.patIdx + 1}`);
    });
    const dupUp = () => {
      this.ui.duplicateHeld = false;
      this.ui.copySource = null;
      this.views.sceneCopySource = null;
      dupBtn.classList.remove('active');
    };
    dupBtn.addEventListener('pointerup', dupUp);
    dupBtn.addEventListener('pointerleave', dupUp);

    byId('btn-up').addEventListener('click', () => this.arrowPressed(1));
    byId('btn-down').addEventListener('click', () => this.arrowPressed(-1));

    // Step Page (p.75): on a 16-step pattern, extends it to 32; on a 32-step
    // pattern, toggles the displayed page. Clear+StepPage reverts to 16;
    // Duplicate+StepPage extends and copies steps 1-16 to 17-32.
    byId('btn-page').addEventListener('click', () => {
      const pat = this.currentEditPattern();
      if (this.ui.clearHeld) {
        pat.settings.playbackEnd = Math.min(15, pat.settings.playbackEnd);
        this.ui.stepPage = 0;
        this.lcdMsg('Pattern reverted to 16 steps');
      } else if (this.ui.duplicateHeld) {
        pat.settings.playbackEnd = 31;
        for (let i = 0; i < 16; i++) {
          pat.steps[16 + i] = JSON.parse(JSON.stringify(pat.steps[i]));
        }
        this.lcdMsg('Extended to 32 steps (1-16 copied)');
      } else if (pat.settings.playbackEnd <= 15) {
        pat.settings.playbackEnd = 31;
        this.ui.stepPage = 0;
        this.lcdMsg('Pattern extended to 32 steps');
      } else {
        this.ui.stepPage = this.ui.stepPage ? 0 : 1;
        this.lcdMsg(this.ui.stepPage ? 'Steps 17-32' : 'Steps 1-16');
      }
      this.updateStepPageButton();
      this.views.render();
    });

    const setKeyOverlay = (visible) => {
      this.ui.keyOverlay = visible;
      byId('key-overlay').classList.toggle('visible', visible);
    };
    byId('btn-keys').addEventListener('click', () => setKeyOverlay(!this.ui.keyOverlay));
    byId('key-overlay-close').addEventListener('click', () => setKeyOverlay(false));
    byId('key-overlay').addEventListener('click', (e) => {
      if (e.target.id === 'key-overlay') setKeyOverlay(false); // backdrop click
    });
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.ui.keyOverlay) setKeyOverlay(false);
    });

    const applySidebar = (hidden) => {
      document.body.classList.toggle('sidebar-hidden', hidden);
      byId('btn-sidebar').classList.toggle('active', hidden);
      this.fitDevice?.();
    };
    byId('btn-sidebar').addEventListener('click', () => {
      const hidden = !document.body.classList.contains('sidebar-hidden');
      try { localStorage.setItem('ct-sidebar-hidden', hidden ? '1' : ''); } catch { /* private mode */ }
      applySidebar(hidden);
    });
    try {
      if (localStorage.getItem('ct-sidebar-hidden') === '1') applySidebar(true);
    } catch { /* private mode */ }

    byId('btn-save').addEventListener('click', () => this.savePressed());
    byId('btn-export-project').addEventListener('click', () => this.exportNcs());
    byId('btn-export-patches').addEventListener('click', () => this.exportPatchesSyx());
    byId('btn-export-pack').addEventListener('click', () => this.exportPack());

    const openPicker = () => byId('file-input').click();
    // Projects opens the project grid; Shift+Projects = Packs (load a pack).
    byId('btn-projects').addEventListener('click', () => {
      if (this.ui.shift) byId('pack-file-input').click();
      else this.setView('projects');
    });
    byId('btn-load-file').addEventListener('click', openPicker);
    byId('file-input').addEventListener('change', async (e) => {
      await this.loadFiles([...e.target.files]);
      e.target.value = '';
    });

    byId('btn-load-pack').addEventListener('click', () => byId('pack-file-input').click());
    byId('pack-file-input').addEventListener('change', async (e) => {
      const file = e.target.files[0];
      if (file) await this.loadPackFromZip(await file.arrayBuffer(), file.name);
      e.target.value = '';
    });
    byId('btn-load-pack-folder').addEventListener('click', () => byId('pack-input').click());
    byId('pack-input').addEventListener('change', async (e) => {
      await this.loadPackFromFiles(e.target.files);
      e.target.value = '';
    });

    // Drag the LCD BPM readout to change tempo.
    const bpmEl = byId('lcd-bpm');
    let dragStart = null;
    bpmEl.addEventListener('pointerdown', (e) => {
      dragStart = { y: e.clientY, bpm: this.seq.bpm };
      bpmEl.setPointerCapture(e.pointerId);
    });
    bpmEl.addEventListener('pointermove', (e) => {
      if (!dragStart) return;
      this.seq.setBpm(dragStart.bpm + Math.round((dragStart.y - e.clientY) / 3));
      if (this.ui.view === 'tempo') this.views.render();
    });
    bpmEl.addEventListener('pointerup', () => { dragStart = null; });
  }

  updateStepPageButton() {
    const pat = this.currentEditPattern();
    const btn = document.getElementById('btn-page');
    btn.classList.toggle('len32-p1', pat.settings.playbackEnd > 15 && this.ui.stepPage === 0);
    btn.classList.toggle('len32-p2', pat.settings.playbackEnd > 15 && this.ui.stepPage === 1);
  }

  // ▼/▲ are context-sensitive (guide p.15).
  // dir: +1 = ▲ pressed, -1 = ▼ pressed. As on the hardware, pagination runs
  // downwards (▼ goes from page 1 to page 2); octaves run upwards (▲ raises).
  arrowPressed(dir) {
    const t = this.ui.currentTrack;
    const pageStep = dir > 0 ? -1 : 1;
    switch (this.ui.view) {
      case 'preset': {
        const kind = this.trackKind(t);
        const maxPage = kind === 'drum' ? 1 : Math.max(0, Math.ceil(this.patchBank.length / 32) - 1);
        this.ui.presetPage = Math.max(0, Math.min(maxPage, this.ui.presetPage + pageStep));
        this.lcdMsg(`Preset page ${this.ui.presetPage + 1}`);
        break;
      }
      case 'patterns':
        this.ui.patternPage = dir < 0 ? 1 : 0;
        this.lcdMsg(this.ui.patternPage ? 'Patterns 5-8' : 'Patterns 1-4');
        break;
      case 'projects': {
        const maxPage = Math.max(0, Math.ceil(this.projectBank.length / 32) - 1);
        this.ui.projectPage = Math.max(0, Math.min(maxPage, this.ui.projectPage + pageStep));
        this.lcdMsg(`Projects ${this.ui.projectPage * 32 + 1}-${this.ui.projectPage * 32 + 32}`);
        break;
      }
      case 'mixer':
        // ▼ switches Macros to pan, ▲ back to levels (guide p.89).
        this.ui.mixerPanMode = dir < 0;
        this.lcdMsg(this.ui.mixerPanMode ? 'Mixer: pan' : 'Mixer: levels');
        this.updateKnobs();
        break;
      case 'sidechain':
        this.ui.scPage = dir < 0 ? 1 : 0;
        this.ui.scFocus = this.ui.scPage === 0 ? 0 : 2;
        this.lcdMsg(this.ui.scPage ? 'Side chain: MIDI 1/2' : 'Side chain: Synth 1/2');
        break;
      default:
        if (this.trackKind(t) === 'drum') {
          const d = t - 4;
          this.ui.samplePage[d] = Math.max(0, Math.min(3, this.ui.samplePage[d] + pageStep));
          this.lcdMsg(`Sample page ${this.ui.samplePage[d] + 1}/4`);
        } else {
          const idx = Math.min(t, 3);
          this.ui.octave[idx] = Math.max(0, Math.min(8, this.ui.octave[idx] + dir));
          this.lcdMsg(`Octave ${this.ui.octave[idx]}`);
        }
    }
    this.views.render();
  }

  // How many pages/steps remain in each arrow's direction (for the LEDs).
  arrowCounts() {
    const t = this.ui.currentTrack;
    switch (this.ui.view) {
      case 'preset': {
        const kind = this.trackKind(t);
        const maxPage = kind === 'drum' ? 1 : Math.max(0, Math.ceil(this.patchBank.length / 32) - 1);
        return { up: this.ui.presetPage, down: maxPage - this.ui.presetPage, max: Math.max(1, maxPage) };
      }
      case 'patterns': return { up: this.ui.patternPage, down: 1 - this.ui.patternPage, max: 1 };
      case 'projects': {
        const maxPage = Math.max(0, Math.ceil(this.projectBank.length / 32) - 1);
        return { up: this.ui.projectPage, down: maxPage - this.ui.projectPage, max: Math.max(1, maxPage) };
      }
      case 'mixer': {
        const p = this.ui.mixerPanMode ? 1 : 0;
        return { up: p, down: 1 - p, max: 1 };
      }
      case 'sidechain': return { up: this.ui.scPage, down: 1 - this.ui.scPage, max: 1 };
      default:
        if (this.trackKind(t) === 'drum') {
          const p = this.ui.samplePage[t - 4];
          return { up: p, down: 3 - p, max: 3 };
        }
        const o = this.ui.octave[Math.min(t, 3)];
        return { up: 8 - o, down: o, max: 8 };
    }
  }

  // Arrow LEDs: brightness proportional to the pages remaining in that
  // direction; unlit when there is nowhere further to go (hardware p.63).
  updateArrowLeds() {
    const { up, down, max } = this.arrowCounts();
    const paint = (id, count) => {
      const btn = document.getElementById(id);
      if (!btn) return;
      if (count <= 0) {
        btn.style.color = '#3c3c44';
        btn.style.textShadow = 'none';
      } else {
        const b = 0.45 + 0.55 * Math.min(1, count / Math.max(1, max));
        btn.style.color = `rgba(240, 240, 248, ${b})`;
        btn.style.textShadow = `0 0 ${Math.round(5 * b)}px rgba(240, 240, 248, ${(b * 0.7).toFixed(2)})`;
      }
    };
    paint('btn-up', up);
    paint('btn-down', down);
  }

  clearPattern(pat) {
    for (const s of pat.steps) {
      if (pat.kind === 'drum') {
        s.active = false; s.velocity = 0; s.probability = 7; s.drumChoice = 0xff;
      } else {
        s.mask = 0; s.probability = 7;
        s.notes.forEach((n) => { n.note = 0; n.gate = 0; n.delay = 0; n.velocity = 96; });
      }
    }
    pat.paramLocks = {};
  }

  exportProject() {
    if (this.ui.shift) return this.exportJson();
    return this.exportNcs();
  }

  // Serialize the live project to hardware-ready .ncs bytes.
  async buildProjectBytes() {
    let base = this.projectRawBytes;
    const fresh = !base;
    if (fresh) {
      // Fresh in-app project: the blank template supplies the unmodeled bytes.
      const res = await fetch('data/Empty.ncs');
      if (!res.ok) throw new Error('No NCS template available');
      base = await res.arrayBuffer();
    }
    // Sync live state that isn't written through to the model.
    this.project.tempo = this.seq.bpm;
    this.project.swing = this.seq.swing;
    for (const s of [0, 1]) {
      const patch = this.synthTracks[s].patch;
      const raw = new Uint8Array(patch.raw);
      // Store the current macro knob positions, as the hardware does.
      for (let k = 0; k < 8; k++) raw[204 + k * 17] = this.synthTracks[s].macroPositions[k] & 0x7f;
      this.project[s === 0 ? 'synth1Patch' : 'synth2Patch'] = raw;
    }
    return serializeNCS(this.project, base, { freshScenes: fresh });
  }

  downloadBlob(blob, filename) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  // Export as a hardware-ready .ncs (send to a Circuit via Components or
  // the circuit-tracks MCP send_project_file tool).
  async exportNcs() {
    try {
      const bytes = await this.buildProjectBytes();
      this.downloadBlob(new Blob([bytes], { type: 'application/octet-stream' }),
        `${(this.project.name || 'project').trim()}.ncs`);
      this.lcdMsg('Project exported as .ncs');
    } catch (err) {
      this.lcdMsg(`Export failed: ${err.message}`);
    }
  }

  exportJson() {
    const blob = new Blob([JSON.stringify(this.project, (k, v) => (v instanceof Uint8Array ? [...v] : v), 1)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${(this.project.name || 'project').trim()}.circuit.json`;
    a.click();
    URL.revokeObjectURL(a.href);
    this.lcdMsg('Project exported as JSON');
  }

  setView(view) {
    if (this.ui.saveArmed && view !== 'saveColor') {
      // Navigating away cancels an armed save.
      this.ui.saveArmed = false;
      document.getElementById('btn-save').classList.remove('armed');
    }
    this.ui.view = view;
    for (const [v, btnId] of Object.entries(VIEW_BUTTON)) {
      if (v === view) document.getElementById(btnId)?.classList.add('active');
    }
    for (const btnId of new Set(Object.values(VIEW_BUTTON))) {
      const active = Object.entries(VIEW_BUTTON).some(([v, id]) => id === btnId && v === view);
      document.getElementById(btnId)?.classList.toggle('active', active);
    }
    document.getElementById('btn-note').classList.toggle('expanded', view === 'note' && this.ui.noteExpanded);
    const labels = {
      microStep: 'micro step', patternSettings: 'pattern settings',
      sidechain: 'side chain', noteExpanded: 'note (expanded)',
      saveColor: 'save: colour',
    };
    document.getElementById('lcd-view').textContent =
      view === 'note' && this.ui.noteExpanded ? 'note (expanded)' : (labels[view] ?? view);
    this.updateStepPageButton();
    this.views.render();
    this.updateKnobs();
  }

  selectTrack(t) {
    this.ui.currentTrack = t;
    this.trackButtons.forEach((b, i) => b.classList.toggle('active', i === t));
    document.getElementById('device').style.setProperty('--accent', this.views.trackColor(t));
    this.views.render();
    this.updateKnobs();
  }

  // ---------- Knobs ----------
  bindKnobs() {
    const attachKnob = (knob, getValue, setValue) => {
      const target = knob.parentElement; // include the label for a bigger hit area
      let drag = null;
      target.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        drag = { lastY: e.clientY, v: getValue(), locked: false };
        // Lock the mouse to the knob while dragging so leaving and
        // re-entering the area can't move it. Touch/pen fall back to capture.
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
        // Fine control: 0.4 value-units per pixel; Shift = extra fine.
        const scale = this.ui.shift ? 0.1 : 0.4;
        const dy = drag.locked ? -e.movementY : (drag.lastY - e.clientY);
        drag.lastY = e.clientY;
        drag.v = Math.max(0, Math.min(127, drag.v + dy * scale));
        setValue(Math.round(drag.v));
        this.rotateKnob(knob, drag.v);
      });
      const end = () => {
        if (drag?.locked && document.pointerLockElement === target) document.exitPointerLock();
        drag = null;
      };
      target.addEventListener('pointerup', end);
      target.addEventListener('pointercancel', end);
      document.addEventListener('pointerlockchange', () => {
        // Lock dropped (e.g. Esc) — finish the drag.
        if (drag?.locked && document.pointerLockElement !== target) drag = null;
      });
      // Mouse wheel for precise stepping.
      target.addEventListener('wheel', (e) => {
        e.preventDefault();
        const v = Math.max(0, Math.min(127, getValue() - Math.sign(e.deltaY) * (this.ui.shift ? 1 : 4)));
        setValue(v);
        this.rotateKnob(knob, v);
      }, { passive: false });
    };

    attachKnob(document.getElementById('knob-volume'),
      () => this.masterVolumeValue,
      (v) => {
        this.masterVolumeValue = v;
        this.engine.setMasterVolume(v);
        this.lcdMsg(`Master volume: ${v}`);
      });

    attachKnob(document.getElementById('knob-filter'),
      () => this.masterFilterValue,
      (v) => {
        this.masterFilterValue = v;
        this.engine.setMasterFilter(v);
        this.updateFilterLed();
        const desc = v < 64 ? `LP ${v}` : v === 64 ? 'OFF' : `HP ${v}`;
        this.lcdMsg(`Master filter: ${desc}`);
      });

    this.macroKnobs.forEach((knob, i) => {
      attachKnob(knob, () => this.knobValue(i), (v) => this.knobChanged(i, v));
    });

    this.rotateKnob(document.getElementById('knob-volume'), this.masterVolumeValue);
    this.rotateKnob(document.getElementById('knob-filter'), this.masterFilterValue);
    this.updateFilterLed();
  }

  // Master Filter LED: pale at the centre detent, blue either side with
  // brightness following how far the filter is pushed (as on the hardware).
  updateFilterLed() {
    const led = document.getElementById('knob-filter')?.parentElement.querySelector('.knob-led');
    if (!led) return;
    const d = Math.abs(this.masterFilterValue - 64) / 63;
    if (d < 0.03) {
      led.style.background = '#8e8a9a';
      led.style.boxShadow = 'none';
      led.style.opacity = 1;
    } else {
      led.style.background = '#3ea0ff';
      led.style.opacity = 0.45 + 0.55 * d;
      led.style.boxShadow = `0 0 ${Math.round(3 + 5 * d)}px #3ea0ff`;
    }
  }

  rotateKnob(knob, v) {
    const deg = -135 + (v / 127) * 270;
    knob.querySelector('.knob-indicator').style.transform = `rotate(${deg}deg)`;
    const grip = knob.querySelector('.knob-grip');
    if (grip) grip.style.transform = `rotate(${deg}deg)`;
  }

  trackSendValue(t, mode) {
    const ncsIdx = SEND_ORDER.indexOf(t);
    if (this.trackKind(t) === 'drum') {
      const cfg = this.drums.tracks[t - 4].config;
      return mode === 'reverb' ? cfg.reverbSend : cfg.delaySend;
    }
    return mode === 'reverb' ? this.project.fx.reverbSends[ncsIdx] : this.project.fx.delaySends[ncsIdx];
  }

  setTrackSend(t, mode, v) {
    const ncsIdx = SEND_ORDER.indexOf(t);
    if (this.trackKind(t) === 'drum') {
      const key = mode === 'reverb' ? 'reverbSend' : 'delaySend';
      this.drums.applyConfig(t - 4, { [key]: v });
      this.project.drumConfigs[t - 4][key] = v;
    }
    if (mode === 'reverb') {
      this.project.fx.reverbSends[ncsIdx] = v;
      this.engine.tracks[t].setReverbSend(v);
    } else {
      this.project.fx.delaySends[ncsIdx] = v;
      this.engine.tracks[t].setDelaySend(v);
    }
  }

  knobValue(i) {
    const t = this.ui.currentTrack;
    switch (this.ui.view) {
      case 'tempo':
        if (i === 0) return ((this.seq.bpm - 40) / 200) * 127;
        if (i === 1) return ((this.seq.swing - 20) / 60) * 127;
        if (i === 4) return this.seq.clickLevel;
        return 0;
      case 'mixer':
        return this.ui.mixerPanMode ? this.trackPan(i) : this.trackLevel(i);
      case 'fx':
      case 'sidechain':
        return this.trackSendValue(i, this.ui.fxMacroMode);
      default:
        if (this.trackKind(t) === 'drum') {
          // Macro 2 = Pitch, 4 = Decay, 6 = Distortion, 8 = EQ; odd macros
          // are inactive for drums (guide p.63).
          const c = this.drums.tracks[t - 4].config;
          return { 1: c.pitch, 3: c.decay, 5: c.distortion, 7: c.eq }[i] ?? 0;
        }
        return this.synthTracks[t].macroPositions[i];
    }
  }

  // Which pattern parameter a macro knob automates in the current view
  // (null = knob is not automatable, e.g. tempo).
  knobLockTarget(i) {
    const t = this.ui.currentTrack;
    switch (this.ui.view) {
      case 'tempo':
      case 'scales':
      case 'preset':
      case 'patterns':
        return null;
      case 'mixer':
        return { trackId: i, key: this.ui.mixerPanMode ? 'pan' : 'level' };
      case 'fx':
      case 'sidechain':
        return { trackId: i, key: this.ui.fxMacroMode === 'reverb' ? 'reverb_send' : 'delay_send' };
      default:
        if (this.trackKind(t) === 'drum') {
          // Drums use the even-numbered macros only (guide p.63).
          const key = { 1: 'pitch', 3: 'decay', 5: 'distortion', 7: 'eq' }[i];
          return key ? { trackId: t, key } : null;
        }
        return { trackId: t, key: `macro${i + 1}` };
    }
  }

  // Record a knob movement as a per-step parameter lock (guide p.36/71/92).
  recordKnobLock(i, v) {
    const target = this.knobLockTarget(i);
    if (!target) return;
    const pos = this.seq.currentSoundingStep(target.trackId);
    if (!pos) return;
    const pat = this.project.patterns[target.trackId][pos.patIdx];
    pat.paramLocks = pat.paramLocks ?? {};
    // Record at micro resolution (same fractional-position keys the NCS
    // automation lanes use), so fast knob sweeps stay smooth.
    const key = pos.tick ? Math.round((pos.step + pos.tick / 6) * 1000) / 1000 : pos.step;
    (pat.paramLocks[target.key] = pat.paramLocks[target.key] ?? {})[key] = v;
  }

  // Hold Clear + turn a knob: delete that control's automation (p.89/92).
  clearKnobLock(i) {
    const target = this.knobLockTarget(i);
    if (!target) return;
    const t = target.trackId;
    const patIdx = this.seq.playing
      ? this.seq.trackState[t]?.patIdx ?? this.ui.currentPattern[t]
      : this.ui.currentPattern[t];
    const pat = this.project.patterns[t][patIdx];
    if (pat.paramLocks?.[target.key]) {
      delete pat.paramLocks[target.key];
      this.lcdMsg(`${target.key} automation cleared`);
    } else {
      this.lcdMsg('No automation to clear');
    }
  }

  knobChanged(i, v) {
    if (this.ui.clearHeld) {
      this.clearKnobLock(i);
      return;
    }
    // Hold a step + turn a knob = lock that value to the held step without
    // changing the live sound (guide p.36/71) — synth macros and drum knobs.
    if (this.ui.heldStep != null) {
      const target = this.knobLockTarget(i);
      if (target) {
        const pat = this.project.patterns[target.trackId][this.ui.currentPattern[target.trackId]];
        pat.paramLocks = pat.paramLocks ?? {};
        (pat.paramLocks[target.key] = pat.paramLocks[target.key] ?? {})[this.ui.heldStep] = v;
        this.lcdMsg(`Step ${this.ui.heldStep + 1} ${target.key}: ${v}`);
        return;
      }
    }
    const t = this.ui.currentTrack;
    switch (this.ui.view) {
      case 'tempo':
        // Macro 1 = tempo, Macro 2 = swing, Macro 5 = click level (p.85-87).
        if (i === 0) {
          this.seq.setBpm(40 + Math.round((v / 127) * 200));
          this.tempoDisplay = { mode: 'bpm' };
          this.lcdMsg(`Tempo: ${this.seq.bpm}`);
          this.views.render();
        } else if (i === 1) {
          this.seq.setSwing(20 + Math.round((v / 127) * 60));
          // Show the Swing value on the grid while turning (reverts to BPM).
          this.tempoDisplay = { mode: 'swing', until: performance.now() + 1200 };
          clearTimeout(this._swingTimer);
          this._swingTimer = setTimeout(() => {
            if (this.ui.view === 'tempo') this.views.render();
          }, 1300);
          this.lcdMsg(`Swing: ${this.seq.swing}`);
          this.views.render();
          this.refreshSidebar();
        } else if (i === 4) {
          this.seq.clickLevel = v;
          this.lcdMsg(`Click level: ${v}`);
        }
        break;
      case 'mixer':
        if (this.ui.mixerPanMode) this.setTrackPan(i, v);
        else this.setTrackLevel(i, v);
        this.lcdMsg(`${this.trackName(i)} ${this.ui.mixerPanMode ? 'pan' : 'level'}: ${v}`);
        this.views.render();
        break;
      case 'fx':
      case 'sidechain':
        // Macros are per-track send levels for the last-chosen FX (p.91).
        this.setTrackSend(i, this.ui.fxMacroMode, v);
        this.lcdMsg(`${this.trackName(i)} ${this.ui.fxMacroMode} send: ${v}`);
        this.refreshSidebar();
        break;
      default:
        if (this.trackKind(t) === 'drum') {
          const key = { 1: 'pitch', 3: 'decay', 5: 'distortion', 7: 'eq' }[i];
          if (!key) {
            this.lcdMsg('Macro inactive for drum tracks');
            return;
          }
          const cfg = { [key]: v };
          this.drums.applyConfig(t - 4, cfg);
          Object.assign(this.project.drumConfigs[t - 4], cfg);
          this.lcdMsg(`${this.trackName(t)} ${key}: ${v}`);
          this.refreshSidebar();
        } else {
          this.synthTracks[t].setMacro(i, v);
          this.lcdMsg(`Macro ${i + 1}: ${v}`);
        }
    }
    // Knob movements are recorded as per-step locks while in Record Mode.
    if (this.ui.recording && this.seq.playing) this.recordKnobLock(i, v);
    this.updateKnobLeds();
  }

  updateKnobs() {
    this.macroKnobs.forEach((knob, i) => this.rotateKnob(knob, this.knobValue(i)));
    this.updateKnobLeds();
  }

  // Macro encoder LEDs: track colour (per-track in mixer/FX views), with
  // brightness following the knob value, as on the hardware.
  updateKnobLeds() {
    const view = this.ui.view;
    const perTrack = view === 'mixer' || view === 'fx' || view === 'sidechain';
    this.macroKnobs.forEach((knob, i) => {
      const led = knob.parentElement.querySelector('.knob-led');
      if (!led) return;
      const t = perTrack ? i : this.ui.currentTrack;
      let active = true;
      if (view === 'tempo') active = i === 0 || i === 1 || i === 4;
      else if (view === 'scales' || view === 'preset' || view === 'patterns') active = false;
      else if (!perTrack && this.trackKind(this.ui.currentTrack) === 'drum') active = i % 2 === 1;
      if (!active) {
        led.style.background = '#8e8a9a'; // unlit pale plastic window
        led.style.boxShadow = 'none';
        led.style.opacity = 1;
        return;
      }
      const color = this.views.trackColor(t);
      const bright = 0.3 + 0.7 * (this.knobValue(i) / 127);
      led.style.background = color;
      led.style.opacity = bright;
      led.style.boxShadow = `0 0 ${Math.round(3 + 5 * bright)}px ${color}`;
    });
  }

  // ---------- Mixer helpers ----------
  trackLevel(t) {
    if (this.trackKind(t) === 'drum') return this.drums.tracks[t - 4].config.level;
    return this.project.mixerLevels[t] ?? this.engine.tracks[t].levelValue;
  }

  trackPan(t) {
    if (this.trackKind(t) === 'drum') return this.drums.tracks[t - 4].config.pan;
    return this.project.mixerPans[t] ?? 64;
  }

  setTrackLevel(t, v) {
    if (this.trackKind(t) === 'drum') {
      this.drums.applyConfig(t - 4, { level: v });
      this.project.drumConfigs[t - 4].level = v;
    } else {
      this.project.mixerLevels[t] = v;
      this.engine.tracks[t].setLevel(v);
    }
    this.refreshSidebar();
  }

  setTrackPan(t, v) {
    if (this.trackKind(t) === 'drum') {
      this.drums.applyConfig(t - 4, { pan: v });
      this.project.drumConfigs[t - 4].pan = v;
    } else {
      this.project.mixerPans[t] = v;
      this.engine.tracks[t].setPan(v);
    }
    this.refreshSidebar();
  }

  // ---------- FX ----------
  applyReverbPreset(idx) {
    const p = REVERB_PRESETS[idx];
    if (!p) return;
    Object.assign(this.project.fx, { reverbType: p.type, reverbDecay: p.decay, reverbDamping: p.damping });
    this.engine.reverb.setParams(p.decay, p.damping);
    this.refreshSidebar();
  }

  applyDelayPreset(idx) {
    const p = DELAY_PRESETS[idx];
    if (!p) return;
    Object.assign(this.project.fx, {
      delayTime: p.time, delaySync: p.sync, delayFeedback: p.feedback,
      delayWidth: p.width, delayLrRatio: p.lr_ratio, delaySlew: p.slew,
    });
    this.applyDelayParams();
    this.refreshSidebar();
  }

  applyDelayParams() {
    const fx = this.project.fx;
    this.engine.delay.setParams(
      { time: fx.delayTime, sync: fx.delaySync, feedback: fx.delayFeedback, width: fx.delayWidth, lrRatio: fx.delayLrRatio },
      this.seq.bpm,
    );
  }

  // ---------- Project / patch loading ----------
  loadProjectFromArrayBuffer(buf, filename = '') {
    const proj = parseNCS(buf);
    // Keep the raw bytes: NCS export writes the modeled fields over them so
    // unmodeled regions round-trip exactly.
    this.projectRawBytes = new Uint8Array(buf).slice();
    this.ui.currentProjectIdx = null; // standalone load; bank loads set it after
    this.applyProject(proj);
    this.lcdMsg(`Loaded ${filename || proj.name || 'project'}`);
    return proj;
  }

  applyProject(proj) {
    const wasPlaying = this.seq.playing;
    if (wasPlaying) this.seq.stop();
    this.project = proj;

    this.seq.setBpm(proj.tempo);
    this.seq.setSwing(proj.swing);

    try {
      if (proj.synth1Patch?.length >= 340) this.synthTracks[0].setPatch(decodePatch(proj.synth1Patch));
      if (proj.synth2Patch?.length >= 340) this.synthTracks[1].setPatch(decodePatch(proj.synth2Patch));
    } catch (err) {
      console.warn('Patch decode failed:', err.message);
    }

    this.engine.reverb.setParams(proj.fx.reverbDecay, proj.fx.reverbDamping);
    this.applyDelayParams();
    this.engine.setFxBypass(proj.fx.fxBypass);

    SEND_ORDER.forEach((trackId, i) => {
      this.engine.tracks[trackId].setReverbSend(proj.fx.reverbSends[i]);
      this.engine.tracks[trackId].setDelaySend(proj.fx.delaySends[i]);
    });

    proj.mixerLevels.forEach((v, i) => this.engine.tracks[i].setLevel(v));
    proj.mixerPans.forEach((v, i) => this.engine.tracks[i].setPan(v));

    // Hardware stores drum sends in the FX arrays (indices 2-5), not the
    // per-drum config bytes — merge both.
    proj.drumConfigs.forEach((cfg, i) => {
      cfg.reverbSend = Math.max(cfg.reverbSend, proj.fx.reverbSends[2 + i]);
      cfg.delaySend = Math.max(cfg.delaySend, proj.fx.delaySends[2 + i]);
      this.drums.applyConfig(i, cfg);
    });

    proj.sidechain.forEach((sc, i) => this.engine.configureSidechain(i, sc));

    this.ui.currentPattern = proj.patternChains.map((c) => Math.max(0, Math.min(7, c.start ?? 0)));
    this.ui.stepPage = 0;
    this.ui.mutes.fill(false);
    this.ui.patchIndex = [null, null];
    this.engine.tracks.forEach((ch) => ch.setMuted(false));
    this.views.activeScene = -1;
    this.views.clearPlayheads();
    this.seq.stoppedPositions = null;

    this.updateLcd();
    this.refreshSidebar();
    this.views.render();
    this.updateKnobs();
    this.updateStepPageButton();
    if (wasPlaying) this.seq.start();
  }

  loadPatchFromArrayBuffer(synthIdx, buf, filename = '') {
    const patch = parseSyxPatch(buf);
    this.synthTracks[synthIdx].setPatch(patch);
    const raw = patch.raw.slice(0, 340);
    if (synthIdx === 0) this.project.synth1Patch = raw;
    else if (synthIdx === 1) this.project.synth2Patch = raw;
    this.lcdMsg(`${this.trackName(synthIdx)}: ${patch.name || filename}`);
    this.refreshSidebar();
    this.updateKnobs();
    return patch;
  }

  async loadFiles(files, syxTarget = null) {
    for (const file of files) {
      try {
        const buf = await file.arrayBuffer();
        if (/\.ncs$/i.test(file.name)) {
          this.loadProjectFromArrayBuffer(buf, file.name);
        } else if (/\.(circuittrackspack|zip)$/i.test(file.name)) {
          await this.loadPackFromZip(buf, file.name);
        } else if (/\.syx$/i.test(file.name)) {
          let target = syxTarget ?? this.ui.currentTrack;
          if (target > 1) target = 0;
          this.ui.patchIndex[target] = null;
          this.loadPatchFromArrayBuffer(target, buf, file.name);
        } else {
          this.lcdMsg(`Unsupported file: ${file.name}`);
        }
      } catch (err) {
        console.warn(`Failed to load ${file.name}:`, err.message);
        this.lcdMsg(`Error loading ${file.name}: ${err.message}`);
      }
    }
  }

  bindDragDrop() {
    const overlay = document.getElementById('drop-overlay');
    document.addEventListener('dragenter', (e) => {
      e.preventDefault();
      overlay.classList.add('visible');
    }, true);
    document.addEventListener('dragover', (e) => {
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    }, true);
    document.addEventListener('dragleave', (e) => {
      e.preventDefault();
      if (!e.relatedTarget) overlay.classList.remove('visible');
    }, true);
    document.addEventListener('drop', (e) => {
      e.preventDefault();
      overlay.classList.remove('visible');
      const files = [...(e.dataTransfer?.files ?? [])];
      if (!files.length) {
        this.lcdMsg('Drop contained no files');
        return;
      }
      // In Projects view, dropping a .ncs on a pad fills that slot
      // without switching to it.
      const pad = e.target.closest?.('.pad');
      if (pad && this.ui.view === 'projects') {
        const padIdx = this.pads.indexOf(pad);
        const ncsFile = files.find((f) => /\.ncs$/i.test(f.name));
        if (padIdx >= 0 && ncsFile) {
          const slot = this.ui.projectPage * 32 + padIdx;
          ncsFile.arrayBuffer().then((buf) => this.loadProjectIntoSlot(slot, buf, ncsFile.name));
          return;
        }
      }
      const btn = e.target.closest?.('.track-btn');
      const syxTarget = btn ? Number(btn.dataset.track) : null;
      this.loadFiles(files, syxTarget);
    }, true);

    // Saved slots live in memory only — warn before the page unloads if
    // there is unexported slot data.
    window.addEventListener('beforeunload', (e) => {
      if (this._slotsDirty) {
        e.preventDefault();
        e.returnValue = '';
      }
    });
  }

  // ---------- Pack loading ----------
  // A pack is a Components export: index.json + samples/ + patches/ +
  // projects/. Three transports feed the same applyPackIndex(): the bundled
  // pack (fetch), a local folder pick, and a .circuittrackspack/.zip file.
  // Local files are read in the browser; nothing is uploaded.

  async loadPackFromUrl(base) {
    const res = await fetch(base + 'index.json');
    if (!res.ok) throw new Error(`No pack index at ${base}`);
    const index = await res.json();
    await this.applyPackIndex(index, async (url) => {
      const r = await fetch(base + url);
      if (!r.ok) throw new Error(`Missing ${url}`);
      return r.arrayBuffer();
    }, { quiet: true });
  }

  async loadPackFromFiles(fileList) {
    const files = [...fileList];
    const indexFile = files.find((f) => f.name === 'index.json');
    if (!indexFile) {
      this.lcdMsg('Not a pack: no index.json in folder');
      return;
    }
    try {
      const index = JSON.parse(await indexFile.text());
      const dir = indexFile.webkitRelativePath.slice(0, -'index.json'.length);
      const byPath = new Map(files.map((f) => [f.webkitRelativePath, f]));
      await this.applyPackIndex(index, async (url) => {
        const f = byPath.get(dir + decodeURI(url));
        if (!f) throw new Error(`Missing ${url}`);
        return f.arrayBuffer();
      });
    } catch (err) {
      this.lcdMsg(`Pack load failed: ${err.message}`);
      console.warn('Pack load failed:', err);
    }
  }

  async loadPackFromZip(buf, filename = '') {
    try {
      const entries = readZip(buf);
      const indexEntry = entries.get('index.json');
      if (!indexEntry) throw new Error('no index.json in archive');
      const index = JSON.parse(new TextDecoder().decode(await indexEntry()));
      await this.applyPackIndex(index, async (url) => {
        const e = entries.get(decodeURI(url));
        if (!e) throw new Error(`Missing ${url}`);
        return e();
      });
    } catch (err) {
      this.lcdMsg(`Pack load failed: ${err.message}`);
      console.warn(`Pack load failed (${filename}):`, err);
    }
  }

  async applyPackIndex(index, getBuf, { quiet = false } = {}) {
    await this.drums.loadSampleBankFrom(index, getBuf);

    const fetchAll = (list) => Promise.all((list ?? []).map(async (item) => {
      try { return { ...item, buf: await getBuf(item.url) }; } catch { return null; }
    }));
    const [patches, projects] = await Promise.all([
      fetchAll(index.patches), fetchAll(index.projects),
    ]);

    for (const url of this._packPatchUrls ?? []) URL.revokeObjectURL(url);
    this.patchBank = patches.filter(Boolean)
      .map((p) => ({ name: p.name, bytes: p.buf, url: URL.createObjectURL(new Blob([p.buf])) }));
    this._packPatchUrls = this.patchBank.map((p) => p.url);
    this.ui.patchIndex = [null, null];

    this.projectBank = new Array(64).fill(null);
    let cursor = 0;
    for (const p of projects.filter(Boolean)) {
      // Honour explicit slot numbers (project_<N>.ncs) so exported banks
      // round-trip into the same slots; fall back to the next free slot.
      const m = /project_(\d+)\.ncs$/i.exec(p.url ?? '');
      let slot = m && Number(m[1]) < 64 && !this.projectBank[Number(m[1])] ? Number(m[1]) : -1;
      if (slot < 0) {
        while (cursor < 64 && this.projectBank[cursor]) cursor++;
        if (cursor >= 64) break;
        slot = cursor;
      }
      this.projectBank[slot] = {
        name: p.name,
        buf: p.buf,
        // Colour index lives at byte 12 of the .ncs header.
        color: p.buf.byteLength >= 16 ? new DataView(p.buf).getUint32(12, true) : 0,
      };
    }
    this.packName = index.name || 'unnamed';
    this.pendingProject = null;
    this.ui.currentProjectIdx = null;
    this.ui.projectPage = 0;
    this._slotsDirty = false; // the bank now mirrors a pack the user has on disk

    if (!quiet) this.lcdMsg(`Pack: ${this.packName}`);
    this.refreshSidebar();
    this.views.render();
  }

  // ---------- Project bank (Projects view) ----------
  // 64 fixed slots, like the hardware. Pressing a pad mirrors the hardware:
  // when stopped the project loads immediately; while playing the switch is
  // queued for the end of the current pattern (same Drum 1 boundary scene
  // queueing uses). Shift+pad switches immediately. Empty slots load an
  // init project into that slot context.
  selectProjectFromBank(idx) {
    if (this.seq.playing) {
      this.pendingProject = { idx, time: this.seq.nextDrum1Boundary() };
      this.lcdMsg(`Next: ${this.projectBank[idx]?.name ?? 'Init project'}`);
      this.views.render();
    } else {
      this.loadProjectFromBank(idx);
    }
  }

  loadProjectFromBank(idx) {
    this.pendingProject = null;
    const entry = this.projectBank[idx];
    if (entry) {
      // Hand a copy to the loader: exports write modeled fields over the raw
      // bytes, and the bank entry must stay pristine for the next switch.
      // applyProject resumes playback itself if the sequencer was running.
      this.loadProjectFromArrayBuffer(entry.buf.slice(0), entry.name);
    } else {
      this.projectRawBytes = null;
      this.applyProject(defaultProject());
      this.lcdMsg('Init project');
    }
    this.ui.currentProjectIdx = idx;
    this.views.render();
  }

  // Load a .ncs into a slot without switching to it (drag-drop on a pad).
  loadProjectIntoSlot(idx, buf, filename = '') {
    const name = parseNCS(buf).name?.trim() || filename.replace(/\.ncs$/i, '') || `Project ${idx + 1}`;
    this.projectBank[idx] = {
      name,
      buf: buf.slice(0),
      color: buf.byteLength >= 16 ? new DataView(buf).getUint32(12, true) : 0,
    };
    this._slotsDirty = true;
    this.lcdMsg(`${name} → slot ${idx + 1}`);
    this.views.render();
  }

  // ---------- Save (hardware flow) ----------
  // First press arms save: the button blinks and the grid shows the 14
  // project colours (press one to recolour). Second press writes the live
  // project into the current slot. Shift+Save exports a .ncs download.
  async savePressed() {
    if (this.ui.shift) return this.exportProject();
    if (!this.ui.saveArmed) {
      this.ui.saveArmed = true;
      this._saveReturnView = this.ui.view;
      document.getElementById('btn-save').classList.add('armed');
      this.setView('saveColor');
      this.lcdMsg('Pick a colour · Save again to confirm');
      return;
    }
    await this.commitSave();
  }

  async commitSave() {
    try {
      let slot = this.ui.currentProjectIdx ?? this.projectBank.findIndex((e) => !e);
      if (slot < 0) slot = 0;
      const bytes = await this.buildProjectBytes();
      this.projectBank[slot] = {
        name: (this.project.name || 'Project').trim() || `Project ${slot + 1}`,
        buf: bytes.slice().buffer,
        color: this.project.color ?? 0,
      };
      this.ui.currentProjectIdx = slot;
      this._slotsDirty = true;
      this.lcdMsg(`Saved to slot ${slot + 1}`);
    } catch (err) {
      this.lcdMsg(`Save failed: ${err.message}`);
    }
    this.exitSaveMode();
  }

  exitSaveMode() {
    if (!this.ui.saveArmed) return;
    this.ui.saveArmed = false;
    document.getElementById('btn-save').classList.remove('armed');
    if (this.ui.view === 'saveColor') this.setView(this._saveReturnView ?? 'note');
  }

  // ---------- Exports ----------
  exportPatchesSyx() {
    for (const s of [0, 1]) {
      const patch = this.synthTracks[s].patch;
      if (!patch?.raw) continue;
      const raw = new Uint8Array(patch.raw).slice(0, 340);
      const syx = new Uint8Array([0xf0, 0x00, 0x20, 0x29, 0x01, 0x64, 0x00, s, 0x00, ...raw, 0xf7]);
      const name = (patch.name || `synth${s + 1}`).trim() || `synth${s + 1}`;
      this.downloadBlob(new Blob([syx], { type: 'application/octet-stream' }), `${name}.syx`);
    }
    this.lcdMsg('Patches exported as .syx');
  }

  // Bundle the current bank — samples, patches, and every stored project
  // slot — into a .circuittrackspack (zipped Components pack).
  exportPack() {
    const entries = [];
    const index = {
      name: this.packName || 'Web Tracks Pack',
      product: 'circuit-tracks',
      version: '1.0',
      projects: [], samples: [], patches: [],
    };
    this.projectBank.forEach((e, i) => {
      if (!e) return;
      index.projects.push({ name: e.name, url: `projects/project_${i}.ncs` });
      entries.push({ name: `projects/project_${i}.ncs`, data: new Uint8Array(e.buf) });
    });
    this.drums.rawBuffers.forEach((raw, i) => {
      if (!raw) return;
      index.samples.push({ name: this.drums.names[i] || `sample_${i}`, url: `samples/sample_${i}.wav` });
      entries.push({ name: `samples/sample_${i}.wav`, data: new Uint8Array(raw) });
    });
    this.patchBank.forEach((p, i) => {
      if (!p.bytes) return;
      index.patches.push({ name: p.name, url: `patches/patch_${i}.syx` });
      entries.push({ name: `patches/patch_${i}.syx`, data: new Uint8Array(p.bytes) });
    });
    entries.push({ name: 'index.json', data: new TextEncoder().encode(JSON.stringify(index, null, 2)) });
    this.downloadBlob(writeZip(entries), `${index.name.replace(/\s+/g, '')}.circuittrackspack`);
    this._slotsDirty = false;
    this.lcdMsg('Pack exported');
  }

  async loadPatchFromBank(synthIdx, bankIdx) {
    const entry = this.patchBank[bankIdx];
    if (!entry) return;
    try {
      const res = await fetch(entry.url);
      const buf = await res.arrayBuffer();
      this.ui.patchIndex[synthIdx] = bankIdx;
      this.loadPatchFromArrayBuffer(synthIdx, buf, entry.name);
      this.views.render();
    } catch (err) {
      this.lcdMsg(`Error: ${err.message}`);
    }
  }

  // ---------- Displays ----------
  lcdMsg(msg) {
    document.getElementById('lcd-msg').textContent = msg;
    clearTimeout(this._lcdTimer);
    this._lcdTimer = setTimeout(() => {
      document.getElementById('lcd-msg').textContent = '';
    }, 2500);
  }

  updateLcd() {
    document.getElementById('lcd-name').textContent = this.project.name || 'Untitled';
    document.getElementById('lcd-bpm').textContent = `♩ ${this.seq.bpm}`;
  }

  onTempoChanged() {
    this.updateLcd();
    document.getElementById('status-bpm').textContent = this.seq.bpm;
  }

  onTransport(playing) {
    document.getElementById('btn-play').classList.toggle('active', playing);
    if (!playing) this.views.clearPlayheads();
  }

  onPatternEdited() { /* hook for future undo/save */ }

  // A scene became current during playback (queued switch or chain advance).
  onSceneChanged(sceneIdx) {
    this.views.activeScene = sceneIdx;
    if (this.ui.view === 'mixer' || this.ui.view === 'patterns') this.views.render();
  }

  refreshSidebar() {
    const $ = (id) => document.getElementById(id);
    $('status-name').textContent = this.project.name || 'Untitled';
    $('status-bpm').textContent = this.project.tempo;
    $('status-swing').textContent = this.project.swing;
    $('status-scale').textContent =
      `${SCALE_ROOTS[this.project.scaleRoot] ?? '?'} ${SCALE_TYPES[this.project.scaleType]?.name ?? '?'}`;
    for (let i = 0; i < 2; i++) {
      const patch = this.synthTracks[i].patch;
      $(`status-patch-${i}`).textContent = patch?.name || 'Initial Patch';
    }
    const fx = this.project.fx;
    const byp = $('status-fx-bypass');
    byp.textContent = fx.fxBypass ? 'BYPASSED' : 'ON';
    byp.classList.toggle('bypassed', fx.fxBypass);
    $('status-reverb').textContent =
      `${REVERB_TYPES[fx.reverbType] ?? '?'} · decay ${fx.reverbDecay} · damp ${fx.reverbDamping} (preset ${this.project.reverbPreset + 1})`;
    $('status-delay').textContent =
      `${this.delayPresetName(this.project.delayPreset)} · fb ${fx.delayFeedback} (preset ${this.project.delayPreset + 1})`;

    const tbody = $('mixer-table').querySelector('tbody');
    tbody.innerHTML = '';
    for (let t = 0; t < 8; t++) {
      const row = document.createElement('tr');
      row.innerHTML = `<td>${this.trackName(t)}</td>` +
        `<td id="mix-level-${t}">${this.trackLevel(t)}</td>` +
        `<td id="mix-pan-${t}">${this.trackPan(t)}</td>` +
        `<td id="mix-rev-${t}">${this.trackSendValue(t, 'reverb')}</td>` +
        `<td id="mix-dly-${t}">${this.trackSendValue(t, 'delay')}</td>`;
      tbody.appendChild(row);
    }

    const dbody = $('drum-table').querySelector('tbody');
    dbody.innerHTML = '';
    for (let d = 0; d < 4; d++) {
      const cfg = this.drums.tracks[d].config;
      const row = document.createElement('tr');
      row.innerHTML = `<td>Drum ${d + 1}</td>` +
        `<td id="drum-sample-${d}">${cfg.patchSelect}: ${this.drums.sampleName(cfg.patchSelect)}</td>` +
        `<td>${cfg.pitch}</td><td>${cfg.decay}</td>`;
      dbody.appendChild(row);
    }
  }
}

window.addEventListener('DOMContentLoaded', () => {
  window.circuitApp = new CircuitApp();
});
