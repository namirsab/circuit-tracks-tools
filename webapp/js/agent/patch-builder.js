// Synth patch builder — port of src/circuit_tracks/patch_builder.py plus the
// config-application logic shared by the hardware server's create_synth_patch
// tool and song.py's _build_patch_bytes. For the same SynthSoundConfig
// ({ preset, name, params, mod_matrix, macros }) it produces the same 340
// bytes as Python; webapp/tests/vectors/patches/*.json are the golden vectors.
//
// The name tables are constants.js's (hardware-verified). Mod-depth macro
// destinations 51-70 are spelled the Python way ("mod_matrix_N_depth"), the
// contract for macro names in songs; constants.js's "modN_depth" is accepted
// too, and the Python form is emitted on read-back.
import { PARAM_OFFSETS, PATCH_SIZE, decodePatch } from '../patch.js';
import {
  MOD_MATRIX_SOURCES, MOD_MATRIX_DESTINATIONS, MACRO_DESTINATIONS as UI_MACRO_DESTINATIONS,
} from '../constants.js';
import { suggest } from './schema.js';

export const PRESETS = ['pad', 'bass', 'lead', 'pluck'];

export const MOD_SOURCES = MOD_MATRIX_SOURCES;
export const MOD_DESTINATIONS = MOD_MATRIX_DESTINATIONS;
export const MACRO_DESTINATIONS = Object.fromEntries(Object.entries(UI_MACRO_DESTINATIONS)
  .map(([k, v]) => [k, Number(k) > 50 ? `mod_matrix_${Number(k) - 50}_depth` : v]));

const lowerKeyed = (table) => Object.fromEntries(Object.entries(table).map(([k, v]) => [v.toLowerCase(), Number(k)]));
const MOD_SOURCE_BY_NAME = lowerKeyed(MOD_SOURCES);
const MOD_DEST_BY_NAME = lowerKeyed(MOD_DESTINATIONS);
const MACRO_DEST_BY_NAME = Object.fromEntries(Object.entries(MACRO_DESTINATIONS).map(([k, v]) => [v, Number(k)]));
for (let i = 1; i <= 20; i++) MACRO_DEST_BY_NAME[`mod${i}_depth`] = 50 + i; // constants.js spelling

const NAME_LEN = 16;
const MOD_MATRIX_START = 124;
const MOD_MATRIX_SLOTS = 20;
const MACRO_START = 204;
const MACRO_COUNT = 8;
const MACRO_SIZE = 17;
const MACRO_TARGETS = 4;

// Init patch template: every official default from the Programmer's
// Reference Guide v3, exactly as patch_builder._INIT_PATCH. (This differs from
// the webapp's own initPatch(), which is only the audio engine's starting
// point; hardware exports must use this table.)
const INIT_VALUES = [
  [32, 2], [34, 64], [35, 64], // voice: poly, pre-glide, octave
  [36, 2], [37, 127], [38, 64], [42, 64], [43, 64], [44, 76], // osc1
  [45, 2], [46, 127], [47, 64], [51, 64], [52, 64], [53, 76], // osc2
  [54, 127], [58, 64], [59, 64], // mixer
  [63, 1], [64, 127], [67, 64], [68, 64], // filter: LP24, open
  [69, 64], [70, 2], [71, 90], [72, 127], [73, 40], // env 1 (amp)
  [74, 64], [75, 2], [76, 75], [77, 35], [78, 45], // env 2 (filter)
  [80, 10], [81, 70], [82, 64], [83, 40], // env 3
  [89, 68], [97, 68], // LFO rates
  [105, 64], [106, 64], [107, 64], [108, 64], [109, 125], [110, 64], // EQ
  [117, 100], [118, 1], [119, 20], [121, 74], [122, 64], [123, 64], // dist/chorus
];

// Empty mod slot: no sources, depth 64 (= none), destination 0.
const resetModSlot = (b, addr) => { b[addr] = 0; b[addr + 1] = 0; b[addr + 2] = 64; b[addr + 3] = 0; };
// Unused macro target: destination 0, full 0-127 range, depth 64 (= none).
const resetMacroTarget = (b, tb) => { b[tb] = 0; b[tb + 1] = 0; b[tb + 2] = 127; b[tb + 3] = 64; };

function initBytes() {
  const b = new Uint8Array(PATCH_SIZE);
  b.set([0x49, 0x6e, 0x69, 0x74]); // "Init"
  b.fill(0x20, 4, NAME_LEN);
  for (const [addr, v] of INIT_VALUES) b[addr] = v;
  for (let s = 0; s < MOD_MATRIX_SLOTS; s++) resetModSlot(b, MOD_MATRIX_START + s * 4);
  for (let k = 0; k < MACRO_COUNT; k++) {
    for (let t = 0; t < MACRO_TARGETS; t++) resetMacroTarget(b, MACRO_START + k * MACRO_SIZE + 1 + t * 4);
  }
  return b;
}

const clamp = (v, lo = 0, hi = 127) => Math.max(lo, Math.min(hi, Math.trunc(Number(v))));
const isInt = (v) => typeof v === 'number' && Number.isInteger(v);
const list = (table) => Object.values(table).map((n) => `"${n}"`).join(', ');
const normaliseModName = (s) => String(s).toLowerCase().replace(/_/g, ' ');

function resolveNamed(value, table, byName, what) {
  if (isInt(value)) {
    if (value < 0 || value > 127) throw new Error(`${what} index ${value} out of range (0-127)`);
    return value;
  }
  const idx = byName[normaliseModName(value)];
  if (idx === undefined) throw new Error(`Unknown ${what} ${JSON.stringify(value)}. Valid: ${list(table)}`);
  return idx;
}

export const resolveModSource = (s) => resolveNamed(s, MOD_SOURCES, MOD_SOURCE_BY_NAME, 'mod source');
export const resolveModDestination = (d) => resolveNamed(d, MOD_DESTINATIONS, MOD_DEST_BY_NAME, 'mod destination');

export function resolveMacroDestination(d) {
  if (isInt(d)) {
    if (d < 0 || d > 127) throw new Error(`Macro destination index ${d} out of range (0-70)`);
    return d;
  }
  const key = String(d);
  const idx = MACRO_DEST_BY_NAME[key] ?? MACRO_DEST_BY_NAME[key.toLowerCase()];
  if (idx === undefined) {
    throw new Error(`Unknown macro destination ${JSON.stringify(d)}. Valid: ${list(MACRO_DESTINATIONS)} (or an index 0-70)`);
  }
  return idx;
}

// Fluent builder mirroring the Python PatchBuilder (the subset the presets
// and the config path use). Per-parameter ranges are the Python clamps.
export class PatchBuilder {
  constructor(name = 'Init') {
    this.bytes = initBytes();
    this.modSlotCursor = 0;
    if (name !== 'Init') this.name(name);
  }

  set(addr, val, lo = 0, hi = 127) {
    this.bytes[addr] = clamp(val, lo, hi);
    return this;
  }

  // ASCII, "?" for anything else (Python encode(errors="replace")), space padded.
  name(n) {
    const s = String(n).slice(0, NAME_LEN);
    for (let i = 0; i < NAME_LEN; i++) {
      const c = i < s.length ? s.charCodeAt(i) : 0x20;
      this.bytes[i] = c > 0x7f ? 0x3f : c;
    }
    return this;
  }

  voice({ polyphony, portamento, pre_glide: preGlide, octave } = {}) {
    if (polyphony != null) this.set(32, polyphony, 0, 2);
    if (portamento != null) this.set(33, portamento);
    if (preGlide != null) this.set(34, preGlide, 52, 76);
    if (octave != null) this.set(35, octave, 58, 69);
    return this;
  }

  osc(n, { wave, interpolate, pulse_width: pulseWidth, sync_depth: syncDepth, density, density_detune: densityDetune, semitones, cents, pitchbend } = {}) {
    const base = n === 1 ? 36 : 45;
    if (wave != null) this.set(base, wave, 0, 29);
    if (interpolate != null) this.set(base + 1, interpolate);
    if (pulseWidth != null) this.set(base + 2, pulseWidth);
    if (syncDepth != null) this.set(base + 3, syncDepth);
    if (density != null) this.set(base + 4, density);
    if (densityDetune != null) this.set(base + 5, densityDetune);
    if (semitones != null) this.set(base + 6, semitones);
    if (cents != null) this.set(base + 7, cents);
    if (pitchbend != null) this.set(base + 8, pitchbend, 52, 76);
    return this;
  }

  mixer({ osc1_level: osc1Level, osc2_level: osc2Level, ring_mod: ringMod, noise, pre_fx: preFx, post_fx: postFx } = {}) {
    if (osc1Level != null) this.set(54, osc1Level);
    if (osc2Level != null) this.set(55, osc2Level);
    if (ringMod != null) this.set(56, ringMod);
    if (noise != null) this.set(57, noise);
    if (preFx != null) this.set(58, preFx, 52, 82);
    if (postFx != null) this.set(59, postFx, 52, 82);
    return this;
  }

  filter({ frequency, resonance, drive, drive_type: driveType, filter_type: filterType, routing, tracking, q_normalize: qNormalize, env2_to_freq: env2ToFreq } = {}) {
    if (routing != null) this.set(60, routing, 0, 2);
    if (drive != null) this.set(61, drive);
    if (driveType != null) this.set(62, driveType, 0, 6);
    if (filterType != null) this.set(63, filterType, 0, 5);
    if (frequency != null) this.set(64, frequency);
    if (tracking != null) this.set(65, tracking);
    if (resonance != null) this.set(66, resonance);
    if (qNormalize != null) this.set(67, qNormalize);
    if (env2ToFreq != null) this.set(68, env2ToFreq);
    return this;
  }

  envAmp({ attack, decay, sustain, release, velocity } = {}) {
    if (velocity != null) this.set(69, velocity);
    if (attack != null) this.set(70, attack);
    if (decay != null) this.set(71, decay);
    if (sustain != null) this.set(72, sustain);
    if (release != null) this.set(73, release);
    return this;
  }

  envFilter({ attack, decay, sustain, release, velocity } = {}) {
    if (velocity != null) this.set(74, velocity);
    if (attack != null) this.set(75, attack);
    if (decay != null) this.set(76, decay);
    if (sustain != null) this.set(77, sustain);
    if (release != null) this.set(78, release);
    return this;
  }

  env3({ delay, attack, decay, sustain, release } = {}) {
    if (delay != null) this.set(79, delay);
    if (attack != null) this.set(80, attack);
    if (decay != null) this.set(81, decay);
    if (sustain != null) this.set(82, sustain);
    if (release != null) this.set(83, release);
    return this;
  }

  lfo(n, { waveform, rate, phase_offset: phaseOffset, slew_rate: slewRate, delay, delay_sync: delaySync, rate_sync: rateSync, one_shot: oneShot, key_sync: keySync, common_sync: commonSync, delay_trigger: delayTrigger, fade_mode: fadeMode } = {}) {
    const base = n === 1 ? 84 : 92;
    if (waveform != null) this.set(base, waveform, 0, 37);
    if (phaseOffset != null) this.set(base + 1, phaseOffset, 0, 119);
    if (slewRate != null) this.set(base + 2, slewRate);
    if (delay != null) this.set(base + 3, delay);
    if (delaySync != null) this.set(base + 4, delaySync, 0, 35);
    if (rate != null) this.set(base + 5, rate);
    if (rateSync != null) this.set(base + 6, rateSync, 0, 35);
    // Flags byte: bit0 one-shot, bit1 key sync, bit2 common sync,
    // bit3 delay trigger, bits 4-5 fade mode.
    let flags = this.bytes[base + 7];
    if (oneShot != null) flags = (flags & ~0x01) | (oneShot ? 1 : 0);
    if (keySync != null) flags = (flags & ~0x02) | (keySync ? 2 : 0);
    if (commonSync != null) flags = (flags & ~0x04) | (commonSync ? 4 : 0);
    if (delayTrigger != null) flags = (flags & ~0x08) | (delayTrigger ? 8 : 0);
    if (fadeMode != null) flags = (flags & ~0x30) | ((clamp(fadeMode, 0, 3) & 0x03) << 4);
    this.bytes[base + 7] = flags;
    return this;
  }

  distortion({ level, type, compensation } = {}) {
    if (level != null) this.set(100, level);
    if (type != null) this.set(116, type, 0, 6);
    if (compensation != null) this.set(117, compensation);
    return this;
  }

  chorus({ level, type, rate, rate_sync: rateSync, feedback, mod_depth: modDepth, delay } = {}) {
    if (level != null) this.set(102, level);
    if (type != null) this.set(118, type, 0, 1);
    if (rate != null) this.set(119, rate);
    if (rateSync != null) this.set(120, rateSync, 0, 35);
    if (feedback != null) this.set(121, feedback);
    if (modDepth != null) this.set(122, modDepth);
    if (delay != null) this.set(123, delay);
    return this;
  }

  // depth is the raw byte here (64 = none), as in Python's add_mod.
  addMod(source, destination, depth = 80, source2 = 0) {
    if (this.modSlotCursor >= MOD_MATRIX_SLOTS) throw new Error('All 20 mod matrix slots are full');
    const addr = MOD_MATRIX_START + this.modSlotCursor * 4;
    this.bytes[addr] = resolveModSource(source);
    this.bytes[addr + 1] = resolveModSource(source2);
    this.bytes[addr + 2] = clamp(depth);
    this.bytes[addr + 3] = resolveModDestination(destination);
    this.modSlotCursor += 1;
    return this;
  }

  clearMods() {
    for (let s = 0; s < MOD_MATRIX_SLOTS; s++) resetModSlot(this.bytes, MOD_MATRIX_START + s * 4);
    this.modSlotCursor = 0;
    return this;
  }

  // targets: up to 4 × { dest, start = 0, end = 127, depth = 127 }.
  setMacro(macroNum, targets = [], position = 0) {
    if (!(macroNum >= 1 && macroNum <= MACRO_COUNT)) throw new Error(`macro number must be 1-8, got ${macroNum}`);
    if (targets.length > MACRO_TARGETS) throw new Error(`Maximum ${MACRO_TARGETS} targets per macro, got ${targets.length}`);
    const hints = { param: 'dest', parameter: 'dest', destination: 'dest', min: 'start', max: 'end' };
    targets.forEach((t, i) => {
      for (const key of Object.keys(t)) {
        if (!['dest', 'start', 'end', 'depth'].includes(key)) {
          const hint = hints[key] ? `, did you mean "${hints[key]}"?` : '.';
          throw new Error(`Macro ${macroNum} target ${i}: unknown key "${key}"${hint} Valid keys: dest, start, end, depth`);
        }
      }
      if (t.dest === undefined || t.dest === null) {
        throw new Error(`Macro ${macroNum} target ${i}: missing required key "dest". Example: {"dest": "filter_frequency", "start": 0, "end": 127}`);
      }
    });
    const base = MACRO_START + (macroNum - 1) * MACRO_SIZE;
    this.bytes[base] = clamp(position);
    for (let i = 0; i < MACRO_TARGETS; i++) {
      const tb = base + 1 + i * 4;
      const t = targets[i];
      if (t) {
        this.bytes[tb] = resolveMacroDestination(t.dest);
        this.bytes[tb + 1] = clamp(t.start ?? 0);
        this.bytes[tb + 2] = clamp(t.end ?? 127);
        this.bytes[tb + 3] = clamp(t.depth ?? 127);
      } else {
        resetMacroTarget(this.bytes, tb);
      }
    }
    return this;
  }

  build() { return this.bytes.slice(); }
}

// Standard knob layout: 1 Oscillator, 2 Osc Mod, 3 Amp Env, 4 Filter Env,
// 5 Filter Freq, 6 Resonance, 7 Modulation, 8 FX.
function stdMacros(builder) {
  return builder
    .setMacro(1, [{ dest: 'osc1_pulse_width_index', start: 0, end: 127 }, { dest: 'osc2_pulse_width_index', start: 0, end: 127 }])
    .setMacro(2, [{ dest: 'osc1_density', start: 0, end: 80 }, { dest: 'osc1_density_detune', start: 0, end: 60 }])
    .setMacro(3, [{ dest: 'env1_attack', start: 0, end: 127 }, { dest: 'env1_release', start: 0, end: 127 }])
    .setMacro(4, [{ dest: 'env2_attack', start: 0, end: 100 }, { dest: 'env2_decay', start: 0, end: 127 }])
    .setMacro(5, [{ dest: 'filter_frequency', start: 0, end: 127 }])
    .setMacro(6, [{ dest: 'filter_resonance', start: 0, end: 127 }])
    .setMacro(7, [{ dest: 'osc2_cents', start: 52, end: 76 }])
    .setMacro(8, [{ dest: 'distortion_level', start: 0, end: 90 }, { dest: 'chorus_level', start: 0, end: 80 }]);
}

export const PRESET_BUILDERS = {
  // Warm pad: detuned saws, slow attack/release, LP filter, chorus, LFO->filter.
  pad: (name = 'Pad') => stdMacros(new PatchBuilder(name)
    .voice({ polyphony: 2 })
    .osc(1, { wave: 2, density: 10, density_detune: 20 })
    .osc(2, { wave: 2, semitones: 64, cents: 70 })
    .mixer({ osc1_level: 100, osc2_level: 90 })
    .filter({ frequency: 65, resonance: 15, filter_type: 1, env2_to_freq: 75 })
    .envAmp({ attack: 60, decay: 90, sustain: 127, release: 80 })
    .envFilter({ attack: 30, decay: 80, sustain: 50, release: 70 })
    .chorus({ level: 0, rate: 30, feedback: 60, mod_depth: 70 })
    .addMod('LFO 1+', 'filter frequency', 75)
    .lfo(1, { waveform: 0, rate: 40 })),
  // Mono bass: saw, LP24 with resonance, fast envelope, sub osc.
  bass: (name = 'Bass') => stdMacros(new PatchBuilder(name)
    .voice({ polyphony: 0, octave: 62 })
    .osc(1, { wave: 2 })
    .osc(2, { wave: 2, semitones: 52 })
    .mixer({ osc1_level: 110, osc2_level: 80 })
    .filter({ frequency: 50, resonance: 10, filter_type: 1, env2_to_freq: 90 })
    .envAmp({ attack: 0, decay: 70, sustain: 100, release: 20 })
    .envFilter({ attack: 0, decay: 60, sustain: 20, release: 20 })),
  // Mono lead: bright, portamento, distortion, LFO vibrato.
  lead: (name = 'Lead') => stdMacros(new PatchBuilder(name)
    .voice({ polyphony: 0, portamento: 30 })
    .osc(1, { wave: 2 })
    .osc(2, { wave: 13, semitones: 76 })
    .mixer({ osc1_level: 100, osc2_level: 60 })
    .filter({ frequency: 70, resonance: 10, filter_type: 1, env2_to_freq: 70 })
    .envAmp({ attack: 2, decay: 80, sustain: 100, release: 30 })
    .envFilter({ attack: 2, decay: 70, sustain: 40, release: 30 })
    .distortion({ level: 40, type: 0 })
    .addMod('LFO 1+/-', 'osc 1 & 2 pitch', 67)
    .lfo(1, { waveform: 0, rate: 75, delay: 40 })),
  // Pluck: fast attack, short decay, filter envelope sweep.
  pluck: (name = 'Pluck') => stdMacros(new PatchBuilder(name)
    .voice({ polyphony: 2 })
    .osc(1, { wave: 2 })
    .osc(2, { wave: 1, cents: 68 })
    .mixer({ osc1_level: 100, osc2_level: 70 })
    .filter({ frequency: 40, resonance: 15, filter_type: 1, env2_to_freq: 100 })
    .envAmp({ attack: 0, decay: 80, sustain: 0, release: 40 })
    .envFilter({ attack: 0, decay: 60, sustain: 0, release: 30 })),
};

const paramHint = (name) => {
  const hit = suggest(name, Object.keys(PARAM_OFFSETS));
  return hit ? ` Did you mean "${hit}"?` : '';
};

// Build the 340 patch bytes for a SynthSoundConfig. Steps mirror song.py's
// _build_patch_bytes: preset (or init) template, then raw params, then a
// cleared mod matrix filled from mod_matrix (signed depth -64..63 -> byte),
// then macros. One deliberate deviation: unknown param names raise instead of
// being ignored silently.
export function buildPatchBytes(config = {}) {
  const { preset = null, name = null, params = null, mod_matrix: modMatrix = null, macros = null } = config;
  let builder;
  if (preset != null && preset !== '') {
    const make = PRESET_BUILDERS[String(preset).toLowerCase()];
    if (!make) throw new Error(`Unknown preset ${JSON.stringify(preset)}. Valid presets: ${PRESETS.join(', ')}`);
    builder = make(name ?? preset);
  } else {
    builder = new PatchBuilder(name ?? 'Init');
  }
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (!(key in PARAM_OFFSETS)) {
        throw new Error(`Unknown synth parameter ${JSON.stringify(key)}.${paramHint(key)} Call get_parameter_reference("patch") for the list.`);
      }
      builder.bytes[PARAM_OFFSETS[key]] = clamp(value);
    }
  }
  if (modMatrix) {
    builder.clearMods();
    modMatrix.forEach((entry, i) => {
      const dest = entry.dest ?? entry.destination;
      if (dest === undefined || dest === null) throw new Error(`mod_matrix[${i}]: missing "dest"`);
      let depth = entry.depth ?? 0;
      if (depth >= -64 && depth <= 63) depth += 64; // signed schema depth -> raw byte
      builder.addMod(entry.source1 ?? entry.source ?? 'direct', dest, depth, entry.source2 ?? 'direct');
    });
  }
  if (macros) {
    for (const [num, cfg] of Object.entries(macros)) {
      builder.setMacro(Number(num), cfg?.targets ?? [], cfg?.position ?? 0);
    }
  }
  return builder.build();
}

export function buildPatch(config = {}) {
  return decodePatch(buildPatchBytes(config));
}
