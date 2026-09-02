// Song compiler — port of the song -> NCS half of src/circuit_tracks/song.py
// (parse_song / _schema_to_song_data / _quantize_song_notes / song_to_ncs and
// the _write_* / _apply_fx_to_ncs / _resolve_*_preset helpers) targeting the
// webapp project model (state.js defaultProject(), ncs.js parseNCS /
// serializeNCS) instead of raw bytes, plus the read-back direction
// (ncs_to_song / _song_data_to_dict) as projectToSong(). Golden vectors in
// webapp/tests/vectors/ pin both directions to the Python output.
//
// Input songs are assumed schema-valid (the tool registry validates against
// data/song.schema.json first); this module still checks what the JSON
// schema cannot express (track kind vs. config shape, equal pattern lengths,
// the 8-pattern limit, song order references, step key format).
import { defaultProject, emptyPattern } from '../state.js';
import { PARAM_OFFSETS } from '../patch.js';
import { REVERB_PRESETS, DELAY_PRESETS, SIDECHAIN_PRESETS } from '../constants.js';
import {
  buildPatchBytes, MOD_SOURCES, MOD_DESTINATIONS, MACRO_DESTINATIONS,
} from './patch-builder.js';

export const STEPS_PER_PATTERN = 32;
export const SLOTS = 8;
const NOTES_PER_STEP = 6;
const LANE_POSITIONS = 192; // 6 micro ticks × 32 steps per automation lane
const DRUM_AUTOMATION_REGION = 1520; // 8 lanes would need 1536: the pan lane is cut short
const NO_SAMPLE_FLIP = 0xff;
const ENGINE_PARAM_MAX_OFFSET = 123; // params above this are mod matrix slots

// Track name -> webapp track id (S1, S2, M1, M2, D1..D4).
export const TRACK_INDEX = { synth1: 0, synth2: 1, midi1: 2, midi2: 3, drum1: 4, drum2: 5, drum3: 6, drum4: 7 };
const TRACK_NAMES = Object.keys(TRACK_INDEX);
const SYNTH_TRACKS = new Set(['synth1', 'synth2', 'midi1', 'midi2']);
const DRUM_TRACKS = new Set(['drum1', 'drum2', 'drum3', 'drum4']);
// NCS send-array order (differs from track id order).
const SEND_INDEX = { synth1: 0, synth2: 1, drum1: 2, drum2: 3, drum3: 4, drum4: 5, midi1: 6, midi2: 7 };
const SEND_NAMES = Object.keys(SEND_INDEX);
const SIDECHAIN_INDEX = { synth1: 0, synth2: 1, midi1: 2, midi2: 3 };
const SIDECHAIN_TRACKS = Object.keys(SIDECHAIN_INDEX);
const SC_SOURCE = { drum1: 0, drum2: 1, drum3: 2, drum4: 3, off: 4 };
const SC_SOURCE_NAMES = ['drum1', 'drum2', 'drum3', 'drum4', 'off'];
const SC_DEFAULTS = { attack: 0, hold: 50, decay: 70, depth: 0 };

const SYNTH_STEP_KEYS = ['note', 'notes', 'velocity', 'gate', 'tie', 'enabled', 'probability', 'macros'];
const DRUM_STEP_KEYS = ['velocity', 'enabled', 'probability', 'sample', 'micro_step'];
const SYNTH_TRACK_KEYS = ['steps', 'macros', 'mixer'];
const DRUM_TRACK_KEYS = ['steps', 'params'];
const MIXER_LANES = ['reverb_send', 'delay_send', 'level', 'pan'];
const DRUM_LANES = ['pitch', 'decay', 'distortion', 'eq', 'reverb_send', 'delay_send', 'level', 'pan'];
const SYNTH_SOUND_KEYS = ['preset', 'name', 'params', 'mod_matrix', 'macros'];
const DRUM_SOUND_KEYS = ['sample', 'level', 'pitch', 'decay', 'distortion', 'eq', 'pan'];

export const SCALE_ROOT_INDEX = {
  C: 0, 'C#': 1, Db: 1, D: 2, 'D#': 3, Eb: 3, E: 4, F: 5, 'F#': 6, Gb: 6,
  G: 7, 'G#': 8, Ab: 8, A: 9, 'A#': 10, Bb: 10, B: 11,
};
const SCALE_ROOT_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];

// Python spellings ("ukranian dorian" included); "minor" aliases 0.
export const SCALE_TYPE_INDEX = {
  'natural minor': 0, minor: 0, major: 1, dorian: 2, phrygian: 3, mixolydian: 4,
  'melodic minor': 5, 'harmonic minor': 6, 'bebop dorian': 7, blues: 8,
  'minor pentatonic': 9, 'hungarian minor': 10, 'ukranian dorian': 11, marva: 12,
  todi: 13, 'whole tone': 14, chromatic: 15,
};
const SCALE_TYPE_NAMES = Object.entries(SCALE_TYPE_INDEX).filter(([k]) => k !== 'minor')
  .reduce((acc, [k, v]) => { acc[v] = k; return acc; }, []);

// Interval table from song.py. Note: constants.js lists Bebop Dorian as
// [0,3,4,5,7,9,10] (no major second); the hardware path uses the 8-note
// scale below, so exports follow Python here.
const SCALE_INTERVALS = [
  [0, 2, 3, 5, 7, 8, 10], [0, 2, 4, 5, 7, 9, 11], [0, 2, 3, 5, 7, 9, 10], [0, 1, 3, 5, 7, 8, 10],
  [0, 2, 4, 5, 7, 9, 10], [0, 2, 3, 5, 7, 9, 11], [0, 2, 3, 5, 7, 8, 11], [0, 2, 3, 4, 5, 7, 9, 10],
  [0, 3, 5, 6, 7, 10], [0, 3, 5, 7, 10], [0, 2, 3, 6, 7, 8, 11], [0, 2, 3, 6, 7, 9, 10],
  [0, 1, 4, 6, 7, 9, 11], [0, 1, 3, 6, 7, 8, 11], [0, 2, 4, 6, 8, 10],
  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
];

// UI names of the factory FX presets (Python has no name table yet, so names
// are a Web Tracks extra; indices behave exactly like Python).
const REVERB_PRESET_NAMES = [
  'Small Chamber', 'Small Room 1', 'Small Room 2', 'Large Room',
  'Hall', 'Large Hall', 'Hall – long reflection', 'Large Hall – long refl.',
];
const DELAY_PRESET_NAMES = [
  'Slapback Fast', 'Slapback Slow', '32nd Triplets', '32nd', '16th Triplets',
  '16th', '16th Ping Pong', '16th Ping Pong Swung', '8th Triplets',
  '8th dotted Ping Pong', '8th', '8th Ping Pong', '8th Ping Pong Swung',
  '4th Triplets', '4th dotted PP Swung', '4th Triplets PP Wide',
];
const REVERB_RANGES = { type: 5, decay: 127, damping: 127 };
const DELAY_RANGES = { time: 127, sync: 35, feedback: 127, width: 127, lr_ratio: 12, slew: 127 };

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const toInt = (v) => Math.trunc(Number(v));
const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const pyMod = (a, n) => ((a % n) + n) % n;

// Python's round(): ties go to the even neighbour (JS Math.round goes up).
export function roundHalfEven(x) {
  const f = Math.floor(x);
  const diff = x - f;
  if (diff === 0.5) return f % 2 === 0 ? f : f + 1;
  return Math.round(x);
}

const round3 = (x) => Math.round(x * 1000) / 1000;

// Exact port of song.quantize_to_scale: nearest scale note, ties round up,
// candidates limited to 0-127. Root shifts the interval set (scales.js
// quantizeToScale is the root-0 special case).
export function quantizeToScaleRoot(note, root, scaleType) {
  if (scaleType === 15) return note;
  const intervals = SCALE_INTERVALS[scaleType];
  if (!intervals) return note;
  let best = note;
  let bestDist = 128;
  const oct = Math.floor(note / 12);
  for (let octave = oct - 1; octave <= oct + 1; octave++) {
    for (const interval of intervals) {
      const candidate = octave * 12 + root + interval;
      if (candidate < 0 || candidate > 127) continue;
      const dist = Math.abs(candidate - note);
      if (dist < bestDist || (dist === bestDist && candidate > best)) {
        bestDist = dist;
        best = candidate;
      }
    }
  }
  return best;
}

// Song note -> stored NCS note: quantise to the scale (root-aware, as
// parse_song does in place), then store relative to C (+12), as the device
// plays quantize(ncs, 0, type) + root - 12.
export function songNoteToNcs(note, scaleRoot, scaleType) {
  const inScale = quantizeToScaleRoot(note, scaleRoot, scaleType);
  const cRelative = quantizeToScaleRoot(inScale - scaleRoot, 0, scaleType);
  return clamp(cRelative + 12, 0, 127);
}

export function ncsNoteToSong(ncsNote, scaleRoot, scaleType) {
  return quantizeToScaleRoot(ncsNote, 0, scaleType) - 12 + scaleRoot;
}

function cloneValue(v) {
  if (v instanceof Uint8Array) return v.slice();
  if (Array.isArray(v)) return v.map(cloneValue);
  if (isObj(v)) {
    const out = {};
    for (const [k, val] of Object.entries(v)) out[k] = cloneValue(val);
    return out;
  }
  return v;
}

export function cloneProject(project) {
  return cloneValue(project);
}

function fail(where, message) {
  throw new Error(`${where}: ${message}`);
}

function checkKeys(obj, allowed, where, what) {
  for (const key of Object.keys(obj)) {
    if (!allowed.includes(key)) {
      fail(where, `${JSON.stringify(key)} is not a ${what} key (allowed: ${allowed.join(', ')})`);
    }
  }
}

function checkPositions(lane, where) {
  for (const pos of Object.keys(lane)) {
    if (!/^\d+(\.\d+)?$/.test(pos)) fail(where, `automation positions must be step numbers like "4" or "4.5", got ${JSON.stringify(pos)}`);
  }
}

// Per-track shape checks (what pydantic enforces by coercing the config to
// SynthTrackConfig or DrumTrackConfig from the track name). Also used on
// their own by set_pattern / set_track.
export function validateTrackConfig(trackName, track, length = 16, where = trackName) {
  if (!(trackName in TRACK_INDEX)) fail(where, `unknown track; use one of ${TRACK_NAMES.join(', ')}`);
  if (!isObj(track)) fail(where, 'must be an object');
  const drum = DRUM_TRACKS.has(trackName);
  checkKeys(track, drum ? DRUM_TRACK_KEYS : SYNTH_TRACK_KEYS, where, drum ? 'drum track' : 'synth track');
  if (track.steps != null && !isObj(track.steps)) fail(`${where}.steps`, 'must map step indices to step objects');
  for (const [idx, step] of Object.entries(track.steps ?? {})) {
    const swhere = `${where}.steps.${idx}`;
    if (!/^\d+$/.test(idx)) fail(`${where}.steps`, `step keys must be whole numbers ("0"-"${length - 1}"), got ${JSON.stringify(idx)}`);
    if (!isObj(step)) fail(swhere, 'must be an object');
    checkKeys(step, drum ? DRUM_STEP_KEYS : SYNTH_STEP_KEYS, swhere, drum ? 'drum step' : 'synth step');
    if (!drum && step.macros && !isObj(step.macros)) fail(`${swhere}.macros`, 'must map macro numbers "1"-"8" to values');
  }
  for (const laneGroup of ['macros', 'mixer', 'params']) {
    if (!track[laneGroup]) continue;
    if (!isObj(track[laneGroup])) fail(`${where}.${laneGroup}`, 'must be an object of automation lanes');
    for (const [laneKey, lane] of Object.entries(track[laneGroup])) {
      if (!isObj(lane)) fail(`${where}.${laneGroup}.${laneKey}`, 'must map step positions to values');
      checkPositions(lane, `${where}.${laneGroup}.${laneKey}`);
    }
  }
}

// Structural checks pydantic does through per-track model coercion and the
// SongSchema validators, which a JSON-schema union cannot express.
export function validateSong(song) {
  if (!isObj(song)) throw new Error('song must be an object');
  const patterns = song.patterns;
  if (!isObj(patterns) || !Object.keys(patterns).length) throw new Error('song.patterns must define at least one pattern');
  const lengths = new Map();
  for (const [patName, pat] of Object.entries(patterns)) {
    const where = `patterns.${patName}`;
    if (!isObj(pat)) fail(where, 'must be an object');
    const length = pat.length ?? 16;
    if (!Number.isInteger(length) || length < 1 || length > STEPS_PER_PATTERN) fail(where, `length must be 1-${STEPS_PER_PATTERN}`);
    lengths.set(patName, length);
    for (const [trackName, track] of Object.entries(pat.tracks ?? {})) {
      validateTrackConfig(trackName, track, length, `${where}.tracks.${trackName}`);
    }
  }
  const distinct = [...new Set(lengths.values())];
  if (distinct.length > 1) {
    const detail = [...lengths].map(([n, l]) => `${n}: ${l}`).join(', ');
    throw new Error(`Patterns have different lengths (${detail}). All patterns in a project must use the same length (all 16 or all 32).`);
  }
  if (song.song != null) {
    if (!Array.isArray(song.song)) throw new Error('song.song must be a list of pattern names');
    if (song.song.length > 16) throw new Error(`song.song has ${song.song.length} entries; the Circuit Tracks has 16 scenes`);
    for (const name of song.song) {
      if (!(name in patterns)) throw new Error(`Song references unknown pattern ${JSON.stringify(name)}. Defined patterns: ${Object.keys(patterns).join(', ')}`);
    }
  }
  const unique = new Set(song.song?.length ? song.song : Object.keys(patterns));
  if (unique.size > SLOTS) throw new Error(`Too many unique patterns (${unique.size}). Circuit Tracks supports max ${SLOTS}.`);
  for (const [trackName, sound] of Object.entries(song.sounds ?? {})) {
    const where = `sounds.${trackName}`;
    if (!(trackName in TRACK_INDEX)) fail(where, `unknown track; use one of ${TRACK_NAMES.join(', ')}`);
    if (!isObj(sound)) fail(where, 'must be an object');
    const drum = DRUM_TRACKS.has(trackName);
    checkKeys(sound, drum ? DRUM_SOUND_KEYS : SYNTH_SOUND_KEYS, where, drum ? 'drum sound' : 'synth sound');
  }
  const fx = song.fx ?? {};
  for (const group of ['reverb_sends', 'delay_sends']) {
    for (const t of Object.keys(fx[group] ?? {})) if (!(t in SEND_INDEX)) fail(`fx.${group}`, `unknown track ${JSON.stringify(t)}; use ${SEND_NAMES.join(', ')}`);
  }
  for (const [t, sc] of Object.entries(fx.sidechain ?? {})) {
    if (!(t in SIDECHAIN_INDEX)) fail('fx.sidechain', `${JSON.stringify(t)} cannot be sidechained; use ${SIDECHAIN_TRACKS.join(', ')}`);
    if (sc?.source != null && !(sc.source in SC_SOURCE)) fail(`fx.sidechain.${t}.source`, `must be one of ${SC_SOURCE_NAMES.join(', ')}`);
  }
  for (const t of Object.keys(song.mixer ?? {})) if (t !== 'synth1' && t !== 'synth2') fail('mixer', `only synth1 and synth2 have mixer settings, got ${JSON.stringify(t)}`);
  if (song.scale?.root != null && !(song.scale.root in SCALE_ROOT_INDEX)) fail('scale.root', `unknown root ${JSON.stringify(song.scale.root)}`);
  if (song.scale?.type != null && !(String(song.scale.type).toLowerCase() in SCALE_TYPE_INDEX)) fail('scale.type', `unknown scale type ${JSON.stringify(song.scale.type)}`);
}

// Python Step.from_dict defaults: one note 60, velocity 100, half-step gate.
function stepFromConfig(d) {
  const step = { notes: [60], velocity: 100, gate: 0.5, tie: false, enabled: true, probability: 1, sample: null };
  if (d.note != null) step.notes = [d.note];
  if (d.notes != null) step.notes = [...d.notes];
  if (d.velocity != null) step.velocity = toInt(d.velocity);
  if (d.gate != null) step.gate = Number(d.gate);
  if (d.tie != null) step.tie = Boolean(d.tie);
  if (d.enabled != null) step.enabled = Boolean(d.enabled);
  if (d.probability != null) step.probability = Number(d.probability);
  if (d.sample != null) step.sample = toInt(d.sample);
  return step;
}

// Automation lanes are collected by flat lane index (Python writes one byte
// per position and the last write wins), then keyed like ncs.js parseLocks
// so the compiled model equals a parsed export.
class LaneWriter {
  constructor(length, { drum = false, warnings = [], where = '' } = {}) {
    this.perStep = length > 0 ? Math.floor(LANE_POSITIONS / length) : 6;
    this.lanes = new Map();
    this.drum = drum;
    this.warnings = warnings;
    this.where = where;
  }

  // Positions a lane can hold: 192, except the drum region is 16 bytes short
  // of eight full lanes, so drum pan automation stops at flat index 175.
  limit(key) {
    if (!this.drum) return LANE_POSITIONS;
    const slot = DRUM_LANES.indexOf(key);
    return Math.min(LANE_POSITIONS, DRUM_AUTOMATION_REGION - slot * LANE_POSITIONS);
  }

  set(key, position, value) {
    const flat = roundHalfEven(position * this.perStep);
    if (flat < 0 || flat >= LANE_POSITIONS) return;
    if (flat >= this.limit(key)) {
      this.warnings.push(`${this.where}.${key}: position ${position} does not fit the hardware's drum automation region (max step ${round3(this.limit(key) / this.perStep)}); skipped`);
      return;
    }
    if (!this.lanes.has(key)) this.lanes.set(key, new Map());
    this.lanes.get(key).set(flat, clamp(toInt(value), 0, 127));
  }

  toParamLocks() {
    const out = {};
    for (const [key, lane] of this.lanes) {
      if (!lane.size) continue;
      const locks = {};
      for (const [flat, v] of lane) {
        const step = Math.floor(flat / this.perStep);
        const sub = flat % this.perStep;
        const pos = sub === 0 ? step : round3(step + sub / this.perStep);
        locks[pos] = v;
      }
      out[key] = locks;
    }
    return out;
  }
}

function writeLaneGroup(writer, group, prefix, length, allowed, warnings, where) {
  for (const [rawKey, positions] of Object.entries(group ?? {})) {
    const key = prefix ? `${prefix}${rawKey}` : rawKey;
    if (allowed && !allowed.includes(rawKey)) continue; // schema already rejects; mirror Python's skip
    for (const [posStr, value] of Object.entries(positions ?? {})) {
      const pos = Number(posStr);
      if (pos >= 0 && pos < length) writer.set(key, pos, value);
      else warnings.push(`${where}.${rawKey}: position ${posStr} is outside the ${length}-step pattern; skipped`);
    }
  }
}

// Build one pattern (the webapp's per-track pattern object) from a song
// track config. scaleRoot/scaleType are integers; drumSample is the track's
// global sample (sounds.drumN.sample) which the hardware export writes into
// every hit — omit it and hits use 0xFF (= the track's active sample).
export function trackConfigToPattern(trackName, trackConfig = {}, length = 16, { scaleRoot = 0, scaleType = 15, drumSample = null, warnings = [], where = trackName } = {}) {
  if (!(trackName in TRACK_INDEX)) throw new Error(`Unknown track ${JSON.stringify(trackName)}; use one of ${TRACK_NAMES.join(', ')}`);
  const drum = DRUM_TRACKS.has(trackName);
  const len = clamp(toInt(length), 1, STEPS_PER_PATTERN);
  const pattern = emptyPattern(drum ? 'drum' : 'synth');
  pattern.settings.playbackEnd = len - 1;
  const writer = new LaneWriter(len, { drum, warnings, where: `${where}.params` });
  const steps = trackConfig.steps ?? {};

  for (const [idxStr, stepData] of Object.entries(steps)) {
    const idx = Number(idxStr);
    if (idx >= len || idx >= STEPS_PER_PATTERN) {
      warnings.push(`${where}.steps.${idxStr}: beyond the ${len}-step pattern; skipped`);
      continue;
    }
    const step = stepFromConfig(stepData);
    if (!step.enabled) continue;
    const target = pattern.steps[idx];

    if (drum) {
      target.active = true;
      target.micro = null;
      target.velocity = clamp(step.velocity, 0, 127);
      target.probability = clamp(roundHalfEven(step.probability * 7), 0, 7);
      target.drumChoice = step.sample ?? drumSample ?? NO_SAMPLE_FLIP;
      if (stepData.micro_step != null) {
        warnings.push(`${where}.steps.${idxStr}: micro_step is not written by the hardware exporter; the hit lands on the beat`);
      }
      continue;
    }

    const notes = step.notes.slice(0, NOTES_PER_STEP);
    if (step.notes.length > NOTES_PER_STEP) warnings.push(`${where}.steps.${idxStr}: only the first ${NOTES_PER_STEP} notes of a chord fit a step`);
    let mask = 0;
    let gateTicks = clamp(roundHalfEven(step.gate * 6), 1, 96);
    if (step.tie) gateTicks |= 0x80;
    notes.forEach((n, i) => {
      mask |= 1 << i;
      target.notes[i] = {
        note: songNoteToNcs(toInt(n), scaleRoot, scaleType),
        gate: gateTicks,
        delay: 0,
        velocity: clamp(step.velocity, 0, 127),
      };
    });
    target.mask = mask;
    target.probability = clamp(roundHalfEven(step.probability * 7), 0, 7);
    for (const [m, value] of Object.entries(stepData.macros ?? {})) {
      const macro = Number(m);
      if (macro >= 1 && macro <= 8) writer.set(`macro${macro}`, idx, value);
    }
  }

  if (drum) {
    writeLaneGroup(writer, trackConfig.params, '', len, DRUM_LANES, warnings, `${where}.params`);
  } else {
    writeLaneGroup(writer, trackConfig.macros, 'macro', len, null, warnings, `${where}.macros`);
    writeLaneGroup(writer, trackConfig.mixer, '', len, MIXER_LANES, warnings, `${where}.mixer`);
  }
  pattern.paramLocks = writer.toParamLocks();
  return pattern;
}

function stripNull(obj) {
  const out = {};
  for (const [k, v] of Object.entries(obj ?? {})) if (v != null) out[k] = v;
  return out;
}

function presetByName(name, names, kind) {
  const norm = (s) => String(s).toLowerCase().replace(/[\s–—-]+/g, ' ').trim();
  const idx = names.findIndex((n) => norm(n) === norm(name));
  if (idx < 0) throw new Error(`Unknown ${kind} preset ${JSON.stringify(name)}. Use an index 0-${names.length - 1} or one of: ${names.join(', ')}`);
  return idx;
}

function closestPreset(params, presets, ranges) {
  let best = 0;
  let bestDist = Infinity;
  presets.forEach((preset, idx) => {
    let dist = 0;
    for (const [k, range] of Object.entries(ranges)) {
      const v = params[k] ?? preset[k];
      dist += ((v - preset[k]) / range) ** 2;
    }
    if (dist < bestDist) { best = idx; bestDist = dist; }
  });
  return best;
}

function resolvePreset(explicit, params, presets, names, ranges, kind, warnings) {
  if (explicit != null) {
    if (typeof explicit === 'string' && !/^\d+$/.test(explicit)) return presetByName(explicit, names, kind);
    const idx = toInt(explicit);
    if (idx < 0 || idx >= presets.length) {
      warnings.push(`fx.${kind}_preset ${idx} is out of range 0-${presets.length - 1}; clamped`);
      return clamp(idx, 0, presets.length - 1);
    }
    return idx;
  }
  if (Object.keys(params).length) return closestPreset(params, presets, ranges);
  return 0;
}

function applyFx(project, fxIn, warnings) {
  const fx = fxIn ?? {};
  const reverb = stripNull(fx.reverb);
  const delay = stripNull(fx.delay);
  project.reverbPreset = resolvePreset(fx.reverb_preset, reverb, REVERB_PRESETS, REVERB_PRESET_NAMES, REVERB_RANGES, 'reverb', warnings);
  project.delayPreset = resolvePreset(fx.delay_preset, delay, DELAY_PRESETS, DELAY_PRESET_NAMES, DELAY_RANGES, 'delay', warnings);
  // An explicit preset supplies defaults for the params not given.
  if (fx.reverb_preset != null) for (const [k, v] of Object.entries(REVERB_PRESETS[project.reverbPreset])) reverb[k] ??= v;
  if (fx.delay_preset != null) for (const [k, v] of Object.entries(DELAY_PRESETS[project.delayPreset])) delay[k] ??= v;

  for (const [track, v] of Object.entries(fx.reverb_sends ?? {})) project.fx.reverbSends[SEND_INDEX[track]] = clamp(toInt(v), 0, 127);
  for (const [track, v] of Object.entries(fx.delay_sends ?? {})) project.fx.delaySends[SEND_INDEX[track]] = clamp(toInt(v), 0, 127);

  if ('type' in reverb) project.fx.reverbType = toInt(reverb.type);
  if ('decay' in reverb) project.fx.reverbDecay = toInt(reverb.decay);
  if ('damping' in reverb) project.fx.reverbDamping = toInt(reverb.damping);
  if ('time' in delay) project.fx.delayTime = toInt(delay.time);
  if ('sync' in delay) project.fx.delaySync = toInt(delay.sync);
  if ('feedback' in delay) project.fx.delayFeedback = toInt(delay.feedback);
  if ('width' in delay) project.fx.delayWidth = toInt(delay.width);
  if ('lr_ratio' in delay) project.fx.delayLrRatio = toInt(delay.lr_ratio);
  if ('slew' in delay) project.fx.delaySlew = toInt(delay.slew);

  for (const [track, scIn] of Object.entries(fx.sidechain ?? {})) {
    const sc = stripNull(scIn);
    const preset = sc.preset != null ? toInt(sc.preset) : 0;
    const defaults = SIDECHAIN_PRESETS[preset] ?? SC_DEFAULTS;
    project.sidechain[SIDECHAIN_INDEX[track]] = {
      preset,
      source: SC_SOURCE[sc.source ?? 'off'] ?? 4,
      attack: toInt(sc.attack ?? defaults.attack),
      hold: toInt(sc.hold ?? defaults.hold),
      decay: toInt(sc.decay ?? defaults.decay),
      depth: toInt(sc.depth ?? defaults.depth),
    };
  }
}

function asciiName(name, warnings) {
  let out = '';
  for (const ch of String(name)) {
    if (ch.charCodeAt(0) > 0x7e || ch.charCodeAt(0) < 0x20) { out += '?'; } else out += ch;
  }
  if (out !== String(name)) warnings.push(`name: non-ASCII characters replaced with "?" (the hardware stores ASCII names)`);
  return out;
}

// Compile a song into a project. baseProject supplies everything the song
// does not set (synth patches when "sounds" is absent, template bytes);
// pass parseNCS(Empty.ncs) for hardware parity, or leave it for
// defaultProject().
export function compileSong(song, { baseProject = null } = {}) {
  validateSong(song);
  const warnings = [];
  const project = baseProject ? cloneProject(baseProject) : defaultProject();

  const rawName = song.name == null || song.name === '' ? 'Song' : String(song.name);
  project.name = asciiName(rawName, warnings).slice(0, 32).replace(/\s+$/, '');
  project.color = clamp(toInt(song.color ?? 8), 0, 13);
  project.tempo = clamp(toInt(song.bpm ?? 120), 40, 240);
  project.swing = clamp(toInt(song.swing ?? 50), 20, 80);
  const scaleRoot = SCALE_ROOT_INDEX[song.scale?.root ?? 'C'] ?? 0;
  const scaleType = SCALE_TYPE_INDEX[String(song.scale?.type ?? 'chromatic').toLowerCase()] ?? 15;
  project.scaleRoot = scaleRoot;
  project.scaleType = scaleType;

  // Pattern slots: song order (first appearance) or definition order.
  const slots = new Map();
  if (song.song?.length) {
    for (const name of song.song) if (!slots.has(name)) slots.set(name, slots.size);
    for (const name of Object.keys(song.patterns)) {
      if (!slots.has(name)) warnings.push(`patterns.${name} is not used by the song order and was not written`);
    }
  } else {
    Object.keys(song.patterns).forEach((name, i) => slots.set(name, i));
  }
  const bySlot = new Map([...slots].map(([name, slot]) => [slot, name]));
  const defaultLength = Math.max(...Object.values(song.patterns).map((p) => p.length ?? 16));

  // Every slot of every track gets its length, used or not (as Python does).
  for (let slot = 0; slot < SLOTS; slot++) {
    const length = bySlot.has(slot) ? (song.patterns[bySlot.get(slot)].length ?? 16) : defaultLength;
    for (let t = 0; t < 8; t++) project.patterns[t][slot].settings.playbackEnd = Math.min(length, STEPS_PER_PATTERN) - 1;
  }

  for (const [name, slot] of slots) {
    const pat = song.patterns[name];
    const length = Math.min(pat.length ?? 16, STEPS_PER_PATTERN);
    for (const [trackName, trackConfig] of Object.entries(pat.tracks ?? {})) {
      const t = TRACK_INDEX[trackName];
      const compiled = trackConfigToPattern(trackName, trackConfig, length, {
        scaleRoot, scaleType, warnings,
        drumSample: song.sounds?.[trackName]?.sample ?? null,
        where: `patterns.${name}.tracks.${trackName}`,
      });
      const existing = project.patterns[t][slot];
      compiled.settings = { ...existing.settings, playbackEnd: length - 1 };
      if (existing.rawHeader) compiled.rawHeader = existing.rawHeader;
      project.patterns[t][slot] = compiled;
    }
  }

  // Sounds
  for (const synth of ['synth1', 'synth2']) {
    const cfg = song.sounds?.[synth];
    if (cfg) project[synth === 'synth1' ? 'synth1Patch' : 'synth2Patch'] = buildPatchBytes(cfg);
  }
  for (const drum of ['drum1', 'drum2', 'drum3', 'drum4']) {
    const cfg = song.sounds?.[drum];
    if (!cfg) continue;
    const target = project.drumConfigs[TRACK_INDEX[drum] - 4];
    if (cfg.sample != null) target.patchSelect = toInt(cfg.sample);
    for (const k of ['level', 'pitch', 'decay', 'distortion', 'eq', 'pan']) if (cfg[k] != null) target[k] = toInt(cfg[k]);
  }

  applyFx(project, song.fx, warnings);

  for (const [i, synth] of [[0, 'synth1'], [1, 'synth2']]) {
    const mix = song.mixer?.[synth];
    if (!mix) continue;
    project.mixerLevels[i] = toInt(mix.level ?? 100);
    project.mixerPans[i] = toInt(mix.pan ?? 64);
  }

  // Song order -> scenes (every track plays the pattern's slot) + scene chain.
  if (song.song?.length) {
    song.song.forEach((name, sceneIdx) => {
      const slot = slots.get(name);
      const scene = project.scenes[sceneIdx];
      scene.flags = 1;
      for (let t = 0; t < 8; t++) {
        const entry = scene.trackChains[t] ?? (scene.trackChains[t] = {});
        entry.start = slot;
        entry.end = slot;
      }
    });
    project.sceneChain = { start: 0, end: song.song.length - 1 };
  }

  return { project, patternNames: slots, warnings };
}

export function songPatchBytes(soundConfig) {
  return buildPatchBytes(soundConfig);
}

// ---------- read-back (ncs_to_song / _song_data_to_dict) ----------

function hasLocks(pattern) {
  return Object.values(pattern.paramLocks ?? {}).some((lane) => Object.keys(lane).length > 0);
}

function slotHasData(project, slot) {
  for (let t = 0; t < 8; t++) {
    const pat = project.patterns[t][slot];
    if (!pat) continue;
    if (pat.kind === 'drum' ? pat.steps.some((s) => s.active) : pat.steps.some((s) => s.mask !== 0)) return true;
    if (hasLocks(pat)) return true;
  }
  return false;
}

function readLanes(pattern, keys, rename = (k) => k) {
  const out = {};
  for (const key of keys) {
    const lane = pattern.paramLocks?.[key];
    if (!lane || !Object.keys(lane).length) continue;
    const lockDict = {};
    for (const [pos, v] of Object.entries(lane)) lockDict[String(Number(pos))] = v;
    out[rename(key)] = lockDict;
  }
  return out;
}

function readSynthTrack(pattern, scaleRoot, scaleType) {
  const steps = {};
  pattern.steps.forEach((step, i) => {
    if (!step.mask) return;
    const active = step.notes.filter((_, slot) => step.mask & (1 << slot));
    const d = {};
    if (active.length) {
      const midi = active.map((n) => ncsNoteToSong(n.note, scaleRoot, scaleType));
      if (active.length === 1) d.note = midi[0];
      else d.notes = midi;
      d.velocity = active[0].velocity;
      d.gate = round3((active[0].gate & 0x7f) / 6);
      if (active[0].gate & 0x80) d.tie = true;
    }
    d.probability = round3(step.probability / 7);
    steps[String(i)] = d;
  });
  const macros = readLanes(pattern, [1, 2, 3, 4, 5, 6, 7, 8].map((m) => `macro${m}`), (k) => k.slice(5));
  const mixer = readLanes(pattern, MIXER_LANES);
  if (!Object.keys(steps).length && !Object.keys(macros).length && !Object.keys(mixer).length) return null;
  const result = {};
  if (Object.keys(steps).length) result.steps = steps;
  if (Object.keys(macros).length) result.macros = macros;
  if (Object.keys(mixer).length) result.mixer = mixer;
  return result;
}

function readDrumTrack(pattern) {
  const steps = {};
  pattern.steps.forEach((step, i) => {
    if (!step.active) return;
    const d = { velocity: step.velocity, probability: round3(step.probability / 7) };
    if (step.drumChoice !== NO_SAMPLE_FLIP) d.sample = step.drumChoice;
    steps[String(i)] = d;
  });
  const params = readLanes(pattern, DRUM_LANES);
  if (!Object.keys(steps).length && !Object.keys(params).length) return null;
  const result = {};
  if (Object.keys(steps).length) result.steps = steps;
  if (Object.keys(params).length) result.params = params;
  return result;
}

// One pattern slot (0-7) in song form: { length, tracks } across all tracks
// that hold data there (Python _read_pattern_slot). Used by get_pattern.
export function patternSlotToSong(project, slot) {
  const scaleRoot = project.scaleRoot ?? 0;
  const scaleType = project.scaleType ?? 15;
  const tracks = {};
  let maxLength = 1;
  for (const trackName of ['synth1', 'synth2', 'drum1', 'drum2', 'drum3', 'drum4', 'midi1', 'midi2']) {
    const pat = project.patterns[TRACK_INDEX[trackName]][slot];
    if (!pat) continue;
    const data = DRUM_TRACKS.has(trackName) ? readDrumTrack(pat) : readSynthTrack(pat, scaleRoot, scaleType);
    if (data) {
      tracks[trackName] = data;
      maxLength = Math.max(maxLength, (pat.settings?.playbackEnd ?? 15) + 1);
    }
  }
  const out = { length: maxLength };
  if (Object.keys(tracks).length) out.tracks = tracks;
  return out;
}

export function slotIsEmpty(project, slot) {
  return !slotHasData(project, slot);
}

// Port of _parse_embedded_patch: engine params, named mod matrix slots
// (signed depth), macro targets with raw depth.
export function patchBytesToSound(bytes) {
  if (!bytes || bytes.length < 340) return null;
  let name = '';
  for (let i = 0; i < 16; i++) if (bytes[i] >= 32 && bytes[i] <= 126) name += String.fromCharCode(bytes[i]);
  name = name.trim();
  const params = {};
  for (const [param, offset] of Object.entries(PARAM_OFFSETS)) {
    if (offset > ENGINE_PARAM_MAX_OFFSET) continue;
    params[param] = bytes[offset];
  }
  const modMatrix = [];
  for (let i = 0; i < 20; i++) {
    const addr = 124 + i * 4;
    const [source, source2, rawDepth, dest] = [bytes[addr], bytes[addr + 1], bytes[addr + 2], bytes[addr + 3]];
    if (rawDepth === 64 && source === 0 && dest === 0) continue;
    const entry = {
      source: MOD_SOURCES[source] ?? source,
      dest: MOD_DESTINATIONS[dest] ?? dest,
      depth: rawDepth - 64,
    };
    if (source2 !== 0) entry.source2 = MOD_SOURCES[source2] ?? source2;
    modMatrix.push(entry);
  }
  const macros = {};
  for (let m = 0; m < 8; m++) {
    const base = 204 + m * 17;
    const targets = [];
    for (let t = 0; t < 4; t++) {
      const tb = base + 1 + t * 4;
      const [destIdx, start, end, depth] = [bytes[tb], bytes[tb + 1], bytes[tb + 2], bytes[tb + 3]];
      if (destIdx === 0 && start === 0 && end === 127 && depth === 64) continue;
      targets.push({ dest: MACRO_DESTINATIONS[destIdx] ?? destIdx, start, end, depth });
    }
    if (targets.length) {
      const cfg = { targets };
      if (bytes[base] !== 0) cfg.position = bytes[base];
      macros[String(m + 1)] = cfg;
    }
  }
  const sound = {};
  if (name) sound.name = name;
  sound.params = params;
  if (modMatrix.length) sound.mod_matrix = modMatrix;
  if (Object.keys(macros).length) sound.macros = macros;
  return sound;
}

// Project -> song JSON, matching Python's _song_data_to_dict(ncs_to_song()).
// patternNames (name -> slot, as returned by compileSong) restores the
// caller's names; otherwise slots are called pattern_<slot>.
export function projectToSong(project, { patternNames = null } = {}) {
  const scaleRoot = project.scaleRoot ?? 0;
  const scaleType = project.scaleType ?? 15;
  const song = {
    name: (project.name ?? '').trim(),
    bpm: project.tempo ?? 120,
    swing: project.swing ?? 50,
    color: project.color ?? 8,
    scale: { root: SCALE_ROOT_NAMES[scaleRoot] ?? 'C', type: SCALE_TYPE_NAMES[scaleType] ?? 'chromatic' },
  };
  const nameOf = new Map();
  if (patternNames) for (const [name, slot] of patternNames) nameOf.set(slot, name);

  const patterns = {};
  const slotNames = new Map();
  for (let slot = 0; slot < SLOTS; slot++) {
    if (!slotHasData(project, slot)) continue;
    const name = nameOf.get(slot) ?? `pattern_${slot}`;
    patterns[name] = patternSlotToSong(project, slot);
    slotNames.set(slot, name);
  }

  const sounds = {};
  for (const [synth, bytes] of [['synth1', project.synth1Patch], ['synth2', project.synth2Patch]]) {
    const sound = patchBytesToSound(bytes);
    if (sound) sounds[synth] = sound;
  }
  (project.drumConfigs ?? []).forEach((cfg, i) => {
    sounds[`drum${i + 1}`] = {
      sample: cfg.patchSelect, level: cfg.level, pitch: cfg.pitch, decay: cfg.decay,
      distortion: cfg.distortion, eq: cfg.eq, pan: cfg.pan,
    };
  });
  if (Object.keys(sounds).length) song.sounds = sounds;

  const pfx = project.fx ?? {};
  const fx = {
    reverb: { type: pfx.reverbType, decay: pfx.reverbDecay, damping: pfx.reverbDamping },
    delay: {
      time: pfx.delayTime, sync: pfx.delaySync, feedback: pfx.delayFeedback,
      width: pfx.delayWidth, lr_ratio: pfx.delayLrRatio, slew: pfx.delaySlew,
    },
  };
  const reverbSends = {};
  const delaySends = {};
  SEND_NAMES.forEach((track, idx) => {
    if (pfx.reverbSends?.[idx]) reverbSends[track] = pfx.reverbSends[idx];
    if (pfx.delaySends?.[idx]) delaySends[track] = pfx.delaySends[idx];
  });
  if (Object.keys(reverbSends).length) fx.reverb_sends = reverbSends;
  if (Object.keys(delaySends).length) fx.delay_sends = delaySends;
  const sidechain = {};
  SIDECHAIN_TRACKS.forEach((track, i) => {
    const sc = project.sidechain?.[i];
    if (!sc) return;
    const source = SC_SOURCE_NAMES[sc.source] ?? 'off';
    if (source !== 'off' || sc.depth > 0 || sc.preset > 0) {
      const d = { source, attack: sc.attack, hold: sc.hold, decay: sc.decay, depth: sc.depth };
      if (sc.preset > 0) d.preset = sc.preset;
      sidechain[track] = d;
    }
  });
  if (Object.keys(sidechain).length) fx.sidechain = sidechain;
  fx.reverb_preset = project.reverbPreset ?? 0;
  fx.delay_preset = project.delayPreset ?? 0;
  song.fx = fx;

  const mixer = {};
  [['synth1', 0], ['synth2', 1]].forEach(([synth, i]) => {
    const level = project.mixerLevels?.[i] ?? 100;
    const pan = project.mixerPans?.[i] ?? 64;
    if (level !== 100 || pan !== 64) mixer[synth] = { level, pan };
  });
  if (Object.keys(mixer).length) song.mixer = mixer;

  if (Object.keys(patterns).length) song.patterns = patterns;

  const order = [];
  if (slotNames.size) {
    const start = project.sceneChain?.start ?? 0;
    const end = project.sceneChain?.end ?? 0;
    for (let i = start; i <= end && i < (project.scenes?.length ?? 0); i++) {
      const slot = project.scenes[i].trackChains?.[0]?.end ?? 0;
      if (slotNames.has(slot)) order.push(slotNames.get(slot));
    }
    if (!order.length && slotNames.has(0) && start === 0 && end === 0) {
      const slot = project.scenes?.[0]?.trackChains?.[0]?.end ?? 0;
      if (slotNames.has(slot)) order.push(slotNames.get(slot));
    }
  }
  if (order.length) song.song = order;
  return song;
}
