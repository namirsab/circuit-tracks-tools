// Headless command API over CircuitApp: everything an agent may do, callable
// without DOM events, keeping the audio engine, the project model and the UI
// (pads, knobs, LCD, sidebar) in sync so the human watches the agent work.
// Tool descriptors in tools.js are thin wrappers over these methods.
import { PARAM_OFFSETS, decodePatch } from '../patch.js';
import { parseNCS } from '../ncs.js';
import { emptyPattern } from '../state.js';
import {
  compileSong, projectToSong, patternSlotToSong, slotIsEmpty, replacePatternSlot,
  validateTrackConfig, trackConfigToPattern, TRACK_INDEX, TRACK_NAMES, FX_FIELDS,
} from './song-compiler.js';
import { buildPatchBytes } from './patch-builder.js';
import { suggest } from './schema.js';
import {
  MACRO_DESTINATIONS, MOD_MATRIX_SOURCES, MOD_MATRIX_DESTINATIONS,
  SCALE_ROOTS, SCALE_TYPES, REVERB_TYPES, SIDECHAIN_PRESETS,
} from '../constants.js';

export { TRACK_NAMES };
export const DRUM_NOTE_TO_INDEX = { 60: 0, 62: 1, 64: 2, 65: 3 };
export const CHANNEL_TO_TRACK = { 0: 0, 1: 1, 2: 2, 3: 3, 9: 4 };

export function trackId(name) {
  const i = TRACK_NAMES.indexOf(String(name).toLowerCase());
  if (i < 0) throw new Error(`Unknown track "${name}". Use one of: ${TRACK_NAMES.join(', ')}`);
  return i;
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, Math.round(Number(v))));
const clamp127 = (v) => clamp(v, 0, 127);

const DRUM_PARAM_NAMES = ['level', 'pitch', 'decay', 'distortion', 'eq', 'pan', 'sample', 'patch_select', 'reverb_send', 'delay_send'];

// set_project_params names of the reverb/delay fields (song-format key
// within FX_FIELDS); reverb_type is handled separately for its note.
const PROJECT_FX_PARAMS = {
  reverb_decay: ['reverb', 'decay'], reverb_damping: ['reverb', 'damping'],
  delay_time: ['delay', 'time'], delay_time_sync: ['delay', 'sync'], delay_feedback: ['delay', 'feedback'],
  delay_width: ['delay', 'width'], delay_lr_ratio: ['delay', 'lr_ratio'], delay_slew_rate: ['delay', 'slew'],
};

export const PROJECT_PARAM_HELP = [
  'reverb_<track>_send / delay_<track>_send (track: synth1, synth2, midi1, midi2, drum1-4)',
  '<track>_level / <track>_pan',
  'master_filter_frequency (0-63 low-pass, 64 off, 65-127 high-pass), master_filter_resonance',
  'reverb_type (0-5), reverb_decay, reverb_damping, reverb_preset (0-7)',
  'delay_time, delay_time_sync (0-35), delay_feedback, delay_width, delay_lr_ratio (0-12), delay_slew_rate, delay_preset (0-15)',
  'fx_bypass (0/1)',
  'sidechain_<synth1|synth2|midi1|midi2>_preset (1-7 activates, 0 off), _source (0-3 = drum1-4, 4 = off), _attack, _hold, _decay, _depth',
];

function suggestName(name, candidates) {
  const hit = suggest(name, candidates);
  return hit ? ` Did you mean "${hit}"?` : '';
}

export class AgentApi {
  constructor(app) {
    this.app = app;
    this.history = []; // project snapshots for undo
    this.maxHistory = 12;
    this.patternNames = new Map(); // song pattern name -> slot index (set by load_song / set_pattern)
    this.songOrder = []; // pattern names behind the scene chain (set_song / queue_patterns)
  }

  get project() { return this.app.project; }
  get seq() { return this.app.seq; }
  get ui() { return this.app.ui; }
  get engine() { return this.app.engine; }

  // ---------- audio ----------
  async ensureAudio() {
    const ctx = this.engine.ctx;
    if (ctx.state !== 'running') {
      // resume() only settles after a user gesture in Chrome; do not hang on it.
      await Promise.race([this.engine.resume(), new Promise((r) => setTimeout(r, 400))]);
    }
    if (ctx.state !== 'running') {
      throw new Error('Audio is locked: browsers need one click or key press on the Web Tracks page before it may play sound. Ask the user to click anywhere on the page, then retry.');
    }
    await this.app.samplesReady;
  }

  // ---------- status ----------
  status() {
    const p = this.project;
    const tracks = TRACK_NAMES.map((track, t) => {
      const ts = this.seq.playing ? this.seq.trackState[t] : null;
      const chain = p.patternChains[t] ?? { start: 0, end: 0 };
      const current = ts ? ts.patIdx : this.ui.currentPattern[t];
      const pat = p.patterns[t][current];
      return {
        track,
        muted: !!this.ui.mutes[t],
        pattern: current + 1,
        length: (pat?.settings.playbackEnd ?? 15) + 1,
        step: ts ? ts.step + 1 : null,
        chain: chain.start || chain.end ? { start: chain.start + 1, end: chain.end + 1 } : null,
      };
    });
    const sc = p.sceneChain ?? { start: 0, end: 0 };
    return {
      playing: this.seq.playing,
      bpm: this.seq.bpm,
      swing: this.seq.swing,
      audio: this.engine.ctx.state,
      project: {
        name: p.name,
        slot: this.ui.currentProjectIdx,
        scale: `${SCALE_ROOTS[p.scaleRoot] ?? '?'} ${SCALE_TYPES[p.scaleType]?.name ?? '?'}`,
      },
      tracks,
      scene_chain: sc.start || sc.end ? { start: sc.start + 1, end: sc.end + 1 } : null,
      active_scene: this.seq.sceneState?.current != null && this.seq.sceneState.current >= 0 ? this.seq.sceneState.current + 1 : null,
      synth_patches: [0, 1].map((s) => this.app.synthTracks[s].patch?.name ?? 'Initial Patch'),
      drums: [0, 1, 2, 3].map((d) => {
        const cfg = this.app.drums.tracks[d].config;
        return { drum: d + 1, sample: cfg.patchSelect, name: this.app.drums.sampleName(cfg.patchSelect) };
      }),
      pattern_names: this.namedSlots(),
    };
  }

  // Pattern name -> 1-based slot, as tools report it.
  namedSlots() {
    return Object.fromEntries([...this.patternNames].map(([n, i]) => [n, i + 1]));
  }

  // ---------- transport ----------
  async play({ resume = false } = {}) {
    await this.ensureAudio();
    if (this.seq.playing) return 'Sequencer already running';
    this.seq.start(resume);
    return resume ? 'Sequencer resumed' : 'Sequencer started';
  }

  stop() {
    if (!this.seq.playing) return 'Sequencer already stopped';
    this.seq.stop();
    this.app.pendingProject = null;
    return 'Sequencer stopped';
  }

  setBpm(bpm) {
    this.seq.setBpm(bpm);
    if (this.ui.view === 'tempo') this.app.views.render();
    this.app.refreshSidebar();
    this.app.markProjectDirty();
    return this.seq.bpm;
  }

  setSwing(swing) {
    this.seq.setSwing(swing);
    if (this.ui.view === 'tempo') this.app.views.render();
    this.app.refreshSidebar();
    this.app.markProjectDirty();
    return this.seq.swing;
  }

  mute(track, muted = true) {
    const t = trackId(track);
    this.ui.mutes[t] = !!muted;
    this.engine.tracks[t].setMuted(!!muted);
    this.app.views.render();
    return `${TRACK_NAMES[t]} ${muted ? 'muted' : 'unmuted'}`;
  }

  // ---------- patterns / scenes ----------
  selectPattern(track, pattern, { immediate = false } = {}) {
    const t = trackId(track);
    const idx = clamp(pattern, 1, 8) - 1;
    this.ui.currentPattern[t] = idx;
    this.project.patternChains[t] = { start: 0, end: 0 };
    if (immediate) this.seq.switchPatternNow(t, idx);
    this.app.markProjectDirty();
    this.app.views.render();
    return `${TRACK_NAMES[t]} pattern ${idx + 1}${this.seq.playing ? (immediate ? ' (switched now)' : ' (queued for the end of the current pattern)') : ''}`;
  }

  selectNamedPattern(name) {
    if (!this.patternNames.size) throw new Error('No named patterns are loaded. Call load_song first, or omit the pattern to play the current selection.');
    if (!this.patternNames.has(name)) {
      throw new Error(`Unknown pattern "${name}". Loaded patterns: ${[...this.patternNames.keys()].join(', ')}`);
    }
    const idx = this.patternNames.get(name);
    this.selectSlotOnAllTracks(idx);
    this.project.sceneChain = { start: 0, end: 0 };
    this.seq.sceneState = null;
    this.app.views.render();
    return idx;
  }

  // Select one slot on every track and drop the per-track pattern chains.
  selectSlotOnAllTracks(slot) {
    for (let t = 0; t < 8; t++) {
      this.ui.currentPattern[t] = slot;
      this.project.patternChains[t] = { start: 0, end: 0 };
    }
  }

  // ---------- synth ----------
  synthTrack(synth) {
    const s = Number(synth);
    if (s !== 1 && s !== 2) throw new Error('synth must be 1 or 2');
    return this.app.synthTracks[s - 1];
  }

  setSynthParams(synth, params) {
    const st = this.synthTrack(synth);
    const patch = st.patch;
    const applied = {};
    for (const [name, raw] of Object.entries(params)) {
      const knob = /^macro_knob([1-8])$/.exec(name);
      if (knob) {
        st.setMacro(Number(knob[1]) - 1, clamp127(raw));
        applied[name] = clamp127(raw);
        continue;
      }
      if (name === 'name') {
        this.renamePatch(patch, String(raw));
        applied.name = patch.name;
        continue;
      }
      if (!(name in PARAM_OFFSETS)) {
        throw new Error(`Unknown synth parameter "${name}".${suggestName(name, Object.keys(PARAM_OFFSETS))} Call get_parameter_reference("synth") for the list.`);
      }
      const v = clamp127(raw);
      patch.params[name] = v;
      patch.raw[PARAM_OFFSETS[name]] = v;
      const mod = /^mod(\d+)_(source1|source2|depth|destination)$/.exec(name);
      if (mod && patch.modMatrix[Number(mod[1]) - 1]) patch.modMatrix[Number(mod[1]) - 1][mod[2]] = v;
      applied[name] = v;
    }
    st.applyPatchFx();
    st.applyLfos();
    st.updateActiveVoices();
    this.afterPatchChange(synth);
    return applied;
  }

  renamePatch(patch, name) {
    const clean = name.slice(0, 16);
    patch.name = clean.trim();
    for (let i = 0; i < 16; i++) patch.raw[i] = i < clean.length ? clean.charCodeAt(i) & 0x7f : 0x20;
  }

  afterPatchChange(synth) {
    this.app.markProjectDirty();
    this.app.refreshSidebar();
    if (this.ui.currentTrack === synth - 1) this.app.updateKnobs();
  }

  setMacro(synth, macro, value) {
    const st = this.synthTrack(synth);
    const idx = clamp(macro, 1, 8) - 1;
    st.setMacro(idx, clamp127(value));
    this.app.markProjectDirty();
    if (this.ui.currentTrack === synth - 1) this.app.updateKnobs();
    return { synth: Number(synth), macro: idx + 1, value: clamp127(value) };
  }

  // Macro layout of one synth: knob positions and named targets.
  macrosOf(synth) {
    const st = this.synthTrack(synth);
    return st.patch.macros.map((m, k) => ({
      macro: k + 1,
      position: st.macroPositions[k],
      targets: m.targets.map((t) => ({
        param: MACRO_DESTINATIONS[t.destination] ?? `dest_${t.destination}`,
        start: t.start, end: t.end, depth: t.depth,
      })),
    }));
  }

  getMacros() {
    const out = {};
    for (const s of [1, 2]) {
      out[`synth${s}`] = { patch: this.app.synthTracks[s - 1].patch?.name ?? 'Initial Patch', macros: this.macrosOf(s) };
    }
    return out;
  }

  getSynthPatch(synth) {
    const st = this.synthTrack(synth);
    const p = st.patch;
    const params = {};
    for (const [k, v] of Object.entries(p.params)) if (!/^mod\d+_/.test(k)) params[k] = v;
    const modMatrix = p.modMatrix
      .map((m, i) => ({ slot: i + 1, ...m }))
      .filter((m) => m.depth !== 64 && m.depth !== 0 && (m.source1 || m.source2 || m.destination))
      .map((m) => ({
        slot: m.slot,
        source1: MOD_MATRIX_SOURCES[m.source1] ?? m.source1,
        source2: MOD_MATRIX_SOURCES[m.source2] ?? m.source2,
        destination: MOD_MATRIX_DESTINATIONS[m.destination] ?? m.destination,
        depth: m.depth - 64,
      }));
    return {
      synth: Number(synth),
      name: p.name,
      bank_index: this.ui.patchIndex[synth - 1],
      params,
      mod_matrix: modMatrix,
      macros: this.macrosOf(synth),
    };
  }

  // ---------- drums ----------
  drumIndex(drum) {
    const d = Number(drum);
    if (!(d >= 1 && d <= 4)) throw new Error('drum must be 1-4');
    return d - 1;
  }

  setDrumParams(drum, params) {
    const d = this.drumIndex(drum);
    const t = 4 + d;
    const cfg = {};
    const applied = {};
    for (const [name, raw] of Object.entries(params)) {
      switch (name) {
        case 'level': case 'pitch': case 'decay': case 'distortion': case 'eq': case 'pan':
          cfg[name] = clamp127(raw); break;
        case 'sample': case 'patch_select':
          cfg.patchSelect = clamp(raw, 0, 63); break;
        case 'reverb_send': this.app.setTrackSend(t, 'reverb', clamp127(raw)); break;
        case 'delay_send': this.app.setTrackSend(t, 'delay', clamp127(raw)); break;
        default:
          throw new Error(`Unknown drum parameter "${name}".${suggestName(name, DRUM_PARAM_NAMES)} Available: ${DRUM_PARAM_NAMES.join(', ')}`);
      }
      applied[name] = name === 'sample' || name === 'patch_select' ? cfg.patchSelect : clamp127(raw);
    }
    if (Object.keys(cfg).length) {
      this.app.drums.applyConfig(d, cfg);
      Object.assign(this.project.drumConfigs[d], cfg);
    }
    this.app.markProjectDirty();
    this.app.refreshSidebar();
    if (this.ui.currentTrack === t) this.app.updateKnobs();
    this.app.views.render();
    if (cfg.patchSelect !== undefined) applied.sample_name = this.app.drums.sampleName(cfg.patchSelect);
    return applied;
  }

  listDrumSamples(page = null) {
    const names = this.app.drums.names;
    const rows = [];
    for (let i = 0; i < 64; i++) {
      if (page && Math.floor(i / 16) !== page - 1) continue;
      rows.push({ index: i, page: Math.floor(i / 16) + 1, name: names[i] || `sample_${i}` });
    }
    return { pack: this.app.packName, samples: rows };
  }

  // ---------- project-level ----------
  setProjectParams(params) {
    const fx = this.project.fx;
    const applied = {};
    const notes = [];
    let reverbDirty = false;
    let delayDirty = false;
    const scUpdates = new Map(); // track -> {preset?, source?, attack?, ...}
    for (const [name, raw] of Object.entries(params)) {
      const v = clamp127(raw);
      let m;
      if ((m = /^(reverb|delay)_(synth1|synth2|midi1|midi2|drum[1-4])_send$/.exec(name))) {
        this.app.setTrackSend(trackId(m[2]), m[1], v);
      } else if ((m = /^(synth1|synth2|midi1|midi2|drum[1-4])_(level|pan)$/.exec(name))) {
        if (m[2] === 'level') this.app.setTrackLevel(trackId(m[1]), v);
        else this.app.setTrackPan(trackId(m[1]), v);
      } else if (name === 'master_filter_frequency') {
        this.app.setMasterFilter(v);
      } else if (name === 'master_filter_resonance') {
        notes.push('master_filter_resonance is accepted but has no audible effect in Web Tracks');
      } else if (name === 'reverb_type') {
        fx.reverbType = clamp(raw, 0, 5); reverbDirty = true;
        notes.push(`reverb_type ${fx.reverbType} (${REVERB_TYPES[fx.reverbType] ?? '?'}) is stored for export; the web reverb has one algorithm`);
      } else if (name in PROJECT_FX_PARAMS) {
        const [group, key] = PROJECT_FX_PARAMS[name];
        const [field, max] = FX_FIELDS[group][key];
        fx[field] = clamp(raw, 0, max);
        if (group === 'reverb') reverbDirty = true; else delayDirty = true;
      } else if (name === 'reverb_preset') { this.project.reverbPreset = clamp(raw, 0, 7); this.app.applyReverbPreset(this.project.reverbPreset); }
      else if (name === 'delay_preset') { this.project.delayPreset = clamp(raw, 0, 15); this.app.applyDelayPreset(this.project.delayPreset); }
      else if (name === 'fx_bypass') { fx.fxBypass = Number(raw) !== 0; this.engine.setFxBypass(fx.fxBypass); }
      else if ((m = /^sidechain_(synth1|synth2|midi1|midi2)_(preset|source|attack|hold|decay|depth)$/.exec(name))) {
        const i = trackId(m[1]);
        const upd = scUpdates.get(i) ?? {};
        upd[m[2]] = m[2] === 'preset' ? clamp(raw, 0, 7) : m[2] === 'source' ? clamp(raw, 0, 4) : v;
        scUpdates.set(i, upd);
      } else {
        throw new Error(`Unknown project parameter "${name}". Available:\n- ${PROJECT_PARAM_HELP.join('\n- ')}`);
      }
      applied[name] = raw;
    }
    if (reverbDirty) this.engine.reverb.setParams(fx.reverbDecay, fx.reverbDamping);
    if (delayDirty) this.app.applyDelayParams();
    for (const [i, upd] of scUpdates) {
      const sc = this.project.sidechain[i];
      // Like the FX view: picking a preset loads its curve into the model,
      // then explicit attack/hold/decay/depth in the same call override it.
      // The engine plays the model's values; the preset index is kept for
      // hardware export.
      const { preset, ...fields } = upd;
      if (preset !== undefined) {
        sc.preset = preset;
        if (preset > 0) Object.assign(sc, SIDECHAIN_PRESETS[preset]);
      }
      Object.assign(sc, fields);
      if (sc.source > 3 && sc.preset > 0 && fields.source === undefined) sc.source = 0;
      this.engine.configureSidechain(i, sc);
      if (sc.preset === 0 && sc.source <= 3) notes.push(`${TRACK_NAMES[i]} sidechain stays off until sidechain_${TRACK_NAMES[i]}_preset is 1-7`);
    }
    this.app.markProjectDirty();
    this.app.refreshSidebar();
    this.app.updateKnobs();
    this.app.views.render();
    return notes.length ? { applied, notes } : { applied };
  }

  // ---------- audition ----------
  async playNotes({ channel = null, track = null, notes, velocity = 100, duration_ms = 500 }) {
    await this.ensureAudio();
    let t = track != null ? trackId(track) : CHANNEL_TO_TRACK[channel];
    if (t == null) throw new Error('Give a track name (synth1, synth2, midi1, midi2, drum1-4) or a channel: 0 = synth1, 1 = synth2, 2 = midi1, 3 = midi2, 9 = drums');
    const now = this.engine.now();
    const vel = clamp127(velocity);
    const played = [];
    if (t >= 4) {
      // A named drum track is one hit; channel 9 maps notes 60/62/64/65 to drums 1-4.
      const drums = track != null ? [t - 4] : notes.map((n) => DRUM_NOTE_TO_INDEX[n]).filter((d) => d != null);
      if (!drums.length) throw new Error('Drum notes are 60, 62, 64, 65 for drum 1-4 (or pass track: "drum1")');
      for (const d of drums) {
        this.app.drums.play(d, now, vel);
        this.seq.visualEvents.push({ type: 'drumhit', time: now, trackId: 4 + d, sample: this.app.drums.tracks[d].config.patchSelect });
        played.push(`drum${d + 1}`);
      }
      return `Hit ${played.join(', ')}`;
    }
    const dur = Math.max(0.03, duration_ms / 1000);
    for (const n of notes) {
      const midi = clamp127(n);
      this.app.synthTracks[t].noteOn(now, midi, vel, dur);
      this.seq.visualEvents.push({ type: 'note', time: now, trackId: t, midi, dur });
      played.push(midi);
    }
    return `Playing ${played.join(', ')} on ${TRACK_NAMES[t]} for ${duration_ms} ms`;
  }

  async playDrum(drum, velocity = 100) {
    const d = this.drumIndex(drum);
    await this.ensureAudio();
    const now = this.engine.now();
    this.app.drums.play(d, now, clamp127(velocity));
    this.seq.visualEvents.push({ type: 'drumhit', time: now, trackId: 4 + d, sample: this.app.drums.tracks[d].config.patchSelect });
    return `Drum ${d + 1} (${this.app.drums.sampleName(this.app.drums.tracks[d].config.patchSelect)})`;
  }

  // ---------- banks ----------
  listPatches() {
    return { pack: this.app.packName, patches: this.app.patchBank.map((p, i) => ({ index: i, name: p.name })) };
  }

  async selectPatch(synth, index) {
    this.synthTrack(synth);
    const entry = this.app.patchBank[index];
    if (!entry) throw new Error(`No patch ${index}; the loaded pack has ${this.app.patchBank.length} patches (0-${this.app.patchBank.length - 1}). Call list_patches.`);
    await this.app.loadPatchFromBank(synth - 1, index);
    return { synth: Number(synth), index, name: entry.name };
  }

  listProjects() {
    return {
      pack: this.app.packName,
      current_slot: this.ui.currentProjectIdx,
      projects: this.app.projectBank.map((e, slot) => (e ? { slot, name: e.name } : null)).filter(Boolean),
    };
  }

  selectProject(slot, { queued = false } = {}) {
    const idx = clamp(slot, 0, 63);
    const name = this.app.projectBank[idx]?.name ?? 'Init project';
    if (queued && this.seq.playing) {
      this.app.selectProjectFromBank(idx);
      return `Queued "${name}" (slot ${idx}) for the end of the current pattern`;
    }
    this.app.loadProjectFromBank(idx);
    return `Loaded "${name}" (slot ${idx})`;
  }

  async saveToSlot(slot = -1, name = '') {
    let idx = Number(slot);
    if (idx < 0) idx = this.ui.currentProjectIdx ?? this.app.projectBank.findIndex((e) => !e);
    if (idx < 0) idx = 0;
    const saved = await this.app.saveToSlot(idx, name || null);
    return { slot: idx, name: saved };
  }

  async downloadProject() {
    await this.app.exportNcs();
    return `Downloading ${(this.project.name || 'project').trim()}.ncs`;
  }

  // ---------- song format ----------
  async loadSong(song) {
    // The blank template as base gives hardware parity (template patches when
    // "sounds" is absent, the same unmodelled bytes the Python export uses);
    // compileSong clones it, so the cached parse stays pristine.
    const { project, patternNames, warnings } = compileSong(song, { baseProject: await this.app.emptyProject() });
    this.app.projectRawBytes = null;
    this.app.applyProject(project);
    this.ui.currentProjectIdx = null;
    this.patternNames = patternNames;
    this.songOrder = Array.isArray(song.song) ? [...song.song] : [];
    this.app.markProjectDirty();
    const names = [...patternNames.keys()];
    return {
      loaded: project.name,
      bpm: project.tempo,
      patterns: this.namedSlots(),
      song_order: this.songOrder,
      warnings,
      next: `start_sequencer${names.length > 1 && !this.songOrder.length ? ` (pattern: one of ${names.join(', ')})` : ''} to listen; export_song_to_project to save`,
    };
  }

  async readProject() {
    const bytes = await this.app.buildProjectBytes(); // syncs live tempo/swing/patches
    const proj = parseNCS(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
    return projectToSong(proj, { patternNames: this.patternNames });
  }

  patternLengthInUse(exceptSlot = null) {
    for (let s = 0; s < 8; s++) {
      if (s === exceptSlot || slotIsEmpty(this.project, s)) continue;
      return (this.project.patterns[0][s].settings.playbackEnd ?? 15) + 1;
    }
    return null;
  }

  slotForName(name) {
    if (!this.patternNames.has(name)) {
      const known = [...this.patternNames.keys()];
      throw new Error(`Unknown pattern "${name}". ${known.length ? `Known patterns: ${known.join(', ')}.` : 'No named patterns yet.'} Create it with set_pattern or load_song.`);
    }
    return this.patternNames.get(name);
  }

  allocateSlot(name) {
    if (this.patternNames.has(name)) return this.patternNames.get(name);
    const used = new Set(this.patternNames.values());
    for (let s = 0; s < 8; s++) if (!used.has(s) && slotIsEmpty(this.project, s)) return s;
    for (let s = 0; s < 8; s++) if (!used.has(s)) return s;
    throw new Error('All 8 pattern slots already have names. Reuse a name to replace that pattern, or clear_pattern one.');
  }

  setPattern(name, tracks, length = 16) {
    const slot = this.allocateSlot(name);
    const inUse = this.patternLengthInUse(slot);
    if (inUse != null && inUse !== length) {
      throw new Error(`All patterns in a project must share one length: the existing patterns use ${inUse} steps but length is ${length}. Use length ${inUse}.`);
    }
    const warnings = [];
    const opts = { scaleRoot: this.project.scaleRoot, scaleType: this.project.scaleType, warnings };
    const built = {};
    for (const [track, cfg] of Object.entries(tracks)) {
      validateTrackConfig(track, cfg, length, `tracks.${track}`);
      built[track] = trackConfigToPattern(track, cfg, length, { ...opts, where: `tracks.${track}` });
    }
    for (const track of TRACK_NAMES) {
      const t = TRACK_INDEX[track];
      replacePatternSlot(this.project, t, slot, built[track] ?? emptyPattern(t >= 4 ? 'drum' : 'synth'), length);
    }
    this.patternNames.set(name, slot);
    if (!this.seq.playing) this.selectSlotOnAllTracks(slot);
    this.ui.stepPage = 0;
    this.app.updateStepPageButton();
    this.app.markProjectDirty();
    this.app.views.render();
    return { pattern: name, slot: slot + 1, length, tracks: Object.keys(built), warnings, selected: !this.seq.playing };
  }

  setTrack(patternName, track, steps, clearExisting = true) {
    const slot = this.slotForName(patternName);
    const t = trackId(track);
    const length = (this.project.patterns[t][slot].settings.playbackEnd ?? 15) + 1;
    const existing = patternSlotToSong(this.project, slot).tracks?.[track] ?? {};
    const cfg = clearExisting
      ? { ...existing, steps }
      : { ...existing, steps: { ...(existing.steps ?? {}), ...steps } };
    validateTrackConfig(track, cfg, length, track);
    const warnings = [];
    const compiled = trackConfigToPattern(track, cfg, length, {
      scaleRoot: this.project.scaleRoot, scaleType: this.project.scaleType, warnings, where: track,
    });
    replacePatternSlot(this.project, t, slot, compiled, length);
    this.app.markProjectDirty();
    this.app.views.render();
    return { pattern: patternName, track, steps: Object.keys(cfg.steps ?? {}).length, length, warnings };
  }

  getPattern(name) {
    const slot = this.slotForName(name);
    return { name, slot: slot + 1, ...patternSlotToSong(this.project, slot) };
  }

  listPatterns() {
    const named = new Set(this.patternNames.values());
    const unnamed = [];
    for (let s = 0; s < 8; s++) if (!named.has(s) && !slotIsEmpty(this.project, s)) unnamed.push(s + 1);
    return {
      patterns: [...this.patternNames].map(([name, slot]) => ({ name, slot: slot + 1, empty: slotIsEmpty(this.project, slot) })),
      unnamed_slots_with_data: unnamed,
      song_order: this.songOrder,
      length: this.patternLengthInUse(),
    };
  }

  clearNamedPattern(name) {
    const slot = this.slotForName(name);
    for (let t = 0; t < 8; t++) this.app.clearPattern(this.project.patterns[t][slot]);
    this.app.markProjectDirty();
    this.app.views.render();
    return `Pattern "${name}" (slot ${slot + 1}) cleared`;
  }

  // Song order = scenes 1..n each holding one pattern slot on every track,
  // chained; like the hardware, the chain loops as a whole.
  setSongOrder(names, { append = false } = {}) {
    const order = append ? [...this.songOrder, ...names] : [...names];
    if (order.length > 16) throw new Error(`A song can have at most 16 scenes; got ${order.length}`);
    const slots = order.map((n) => this.slotForName(n));
    this.project.scenes.forEach((scene, i) => {
      if (i < slots.length) {
        scene.trackChains = Array.from({ length: 8 }, () => ({ start: slots[i], end: slots[i] }));
        scene.flags = 1;
      } else {
        scene.trackChains = Array.from({ length: 8 }, () => ({ start: 0, end: 0 }));
        scene.flags = 0;
      }
    });
    this.songOrder = order;
    if (!order.length) return this.clearQueue();
    this.project.sceneChain = { start: 0, end: order.length - 1 };
    if (this.seq.playing) this.seq.queueSceneChain(0, order.length - 1);
    else this.seq.applySceneChains(0);
    this.app.views.activeScene = 0;
    this.app.markProjectDirty();
    this.app.views.render();
    return {
      song: order,
      scenes: order.length,
      status: this.seq.playing ? 'queued for the end of the current Drum 1 pattern' : 'plays from the top on the next start',
    };
  }

  clearQueue() {
    this.songOrder = [];
    this.project.sceneChain = { start: 0, end: 0 };
    this.seq.sceneState = null;
    this.app.views.activeScene = -1;
    this.app.markProjectDirty();
    this.app.views.render();
    return 'Song order cleared; the current pattern selection loops';
  }

  // ---------- patch building ----------
  createSynthPatch(synth, { name, params, mod_matrix, macros, preset }) {
    const st = this.synthTrack(synth);
    const bytes = buildPatchBytes({ preset, name, params, mod_matrix, macros });
    const patch = decodePatch(bytes);
    st.setPatch(patch);
    this.project[Number(synth) === 1 ? 'synth1Patch' : 'synth2Patch'] = bytes;
    this.ui.patchIndex[synth - 1] = null;
    this.afterPatchChange(synth);
    this.app.views.render();
    return {
      synth: Number(synth),
      name: patch.name,
      preset: preset ?? null,
      macros: this.macrosOf(synth).filter((m) => m.targets.length),
    };
  }

  savePatchToBank(synth, slot) {
    this.synthTrack(synth);
    const idx = clamp(slot, 0, 127);
    if (idx > this.app.patchBank.length) throw new Error(`Slot ${idx} is beyond the bank (${this.app.patchBank.length} patches); use 0-${this.app.patchBank.length}`);
    const { entry, replaced } = this.app.storePatchInBank(synth - 1, idx);
    this.app.views.render();
    return { synth: Number(synth), slot: idx, name: entry.name, replaced };
  }

  // ---------- undo ----------
  // Returns true when a snapshot was pushed (so a failed call can drop it).
  async snapshot(label) {
    try {
      const bytes = await this.app.buildProjectBytes();
      this.history.push({ label, bytes, slot: this.ui.currentProjectIdx, time: Date.now() });
      if (this.history.length > this.maxHistory) this.history.shift();
      return true;
    } catch (err) {
      console.warn('agent snapshot failed:', err);
      return false;
    }
  }

  async undo() {
    const h = this.history.pop();
    if (!h) throw new Error('Nothing to undo');
    const buf = h.bytes.buffer.slice(h.bytes.byteOffset, h.bytes.byteOffset + h.bytes.byteLength);
    this.app.loadProjectFromArrayBuffer(buf, 'previous state');
    this.ui.currentProjectIdx = h.slot;
    this.app.views.render();
    return `Undid "${h.label}"; ${this.history.length} earlier state(s) remain`;
  }
}
