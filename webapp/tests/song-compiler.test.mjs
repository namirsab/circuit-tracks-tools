import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { parseNCS, serializeNCS } from '../js/ncs.js';
import { quantizeToScale } from '../js/scales.js';
import { SCALE_TYPES } from '../js/constants.js';
import {
  compileSong, projectToSong, trackConfigToPattern, validateSong, validateTrackConfig,
  patternSlotToSong, slotIsEmpty, quantizeToScaleRoot, songNoteToNcs, roundHalfEven,
  patchBytesToSound, cloneProject,
} from '../js/agent/song-compiler.js';
import { defaultProject } from '../js/state.js';

const dir = new URL('./vectors/', import.meta.url);
const read = (name) => readFileSync(new URL(name, dir));
const toArrayBuffer = (buf) => buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
const emptyBytes = readFileSync(new URL('../data/Empty.ncs', import.meta.url));
const emptyProject = () => parseNCS(toArrayBuffer(emptyBytes));

const vectors = readdirSync(dir).filter((f) => f.endsWith('.song.json')).sort().map((f) => {
  const stem = f.slice(0, -'.song.json'.length);
  return {
    stem,
    song: JSON.parse(read(f)),
    ncs: read(`${stem}.ncs`),
    readback: JSON.parse(read(`${stem}.readback.json`)),
  };
});

// Both sides are reduced to one shape before comparing: parseNCS carries raw
// extras (drum rawHeader, chain byte2/byte3, no paramLocks on block 63) and a
// compiled model omits keys parseNCS always writes (scene flags, drum micro).
function normalise(project) {
  const p = JSON.parse(JSON.stringify(project, (k, v) => (v instanceof Uint8Array ? [...v] : v)));
  for (const scene of p.scenes) {
    scene.flags ??= 0;
    for (const c of scene.trackChains) { delete c.byte2; delete c.byte3; }
  }
  for (const c of p.patternChains) { delete c.byte2; delete c.byte3; }
  for (const track of p.patterns) {
    for (const pat of track) {
      delete pat.rawHeader;
      pat.paramLocks ??= {};
      if (pat.kind === 'drum') for (const s of pat.steps) s.micro ??= null;
    }
  }
  return p;
}

test('golden song vectors exist', () => {
  assert.ok(vectors.length >= 6, `only ${vectors.length} song vectors`);
});

for (const v of vectors) {
  test(`${v.stem}: compiled project equals the parsed Python export`, () => {
    const { project } = compileSong(v.song, { baseProject: emptyProject() });
    assert.deepEqual(normalise(project), normalise(parseNCS(toArrayBuffer(v.ncs))));
  });

  test(`${v.stem}: serialised bytes are identical to the Python export`, () => {
    const { project } = compileSong(v.song, { baseProject: emptyProject() });
    const bytes = serializeNCS(project, toArrayBuffer(emptyBytes), { freshScenes: true });
    const diffs = [];
    for (let i = 0; i < bytes.length && diffs.length < 10; i++) {
      if (bytes[i] !== v.ncs[i]) diffs.push(`0x${i.toString(16)}: js=${bytes[i]} py=${v.ncs[i]}`);
    }
    assert.deepEqual(diffs, []);
  });

  test(`${v.stem}: read-back matches Python's ncs_to_song`, () => {
    assert.deepEqual(projectToSong(parseNCS(toArrayBuffer(v.ncs))), v.readback);
    const { project } = compileSong(v.song, { baseProject: emptyProject() });
    assert.deepEqual(projectToSong(project), v.readback);
  });
}

test('roundHalfEven matches Python round()', () => {
  assert.deepEqual([0.5, 1.5, 2.5, 4.5, 10.5, 1.4, 1.6, 3].map(roundHalfEven), [0, 2, 2, 4, 10, 1, 2, 3]);
});

test('quantizeToScaleRoot agrees with the playback quantiser in scales.js for every scale and note', () => {
  for (let type = 0; type < 16; type++) {
    for (let n = 0; n < 128; n++) {
      assert.equal(quantizeToScaleRoot(n, 0, type), quantizeToScale(n, type), `type ${type} note ${n}`);
    }
  }
  assert.equal(SCALE_TYPES[7].intervals.length, 8); // Bebop Dorian has the major second (matches song.py)
  // D minor: 61 (C#) sits between C (60) and D (62); ties round up.
  assert.equal(quantizeToScaleRoot(61, 2, 0), 62);
  assert.equal(quantizeToScaleRoot(63, 2, 0), 64);
  assert.equal(songNoteToNcs(62, 2, 0), 72); // D relative to C, +12
  assert.equal(songNoteToNcs(60, 0, 15), 72);
});

test('compileSong without a base uses defaultProject and reports pattern names', () => {
  const song = { patterns: { a: { tracks: { drum1: { steps: { 0: {} } } } }, b: { tracks: {} } }, song: ['b', 'a', 'b'] };
  const { project, patternNames, warnings } = compileSong(song);
  assert.deepEqual([...patternNames], [['b', 0], ['a', 1]]);
  assert.deepEqual(warnings, []);
  assert.equal(project.patterns[4][1].steps[0].active, true);
  assert.deepEqual(project.sceneChain, { start: 0, end: 2 });
  assert.equal(project.scenes[1].trackChains[7].end, 1);
  assert.equal(project.scenes[1].flags, 1);
  assert.equal(project.name, 'Song');
  assert.equal(project.synth1Patch, null); // no sounds, no template
  assert.ok(defaultProject().patterns[0][0].steps.length === 32);
});

test('warnings cover skipped steps, unused patterns, chord overflow and micro_step', () => {
  const song = {
    patterns: {
      a: { length: 16, tracks: { synth1: { steps: { 20: { note: 60 }, 0: { notes: [1, 2, 3, 4, 5, 6, 7] } }, macros: { 1: { 16: 5 } } }, drum1: { steps: { 0: { micro_step: 2 } } } } },
      b: { length: 16, tracks: {} },
    },
    song: ['a'],
  };
  const { warnings } = compileSong(song);
  assert.match(warnings.join('\n'), /patterns\.b is not used by the song order/);
  assert.match(warnings.join('\n'), /steps\.20: beyond the 16-step pattern/);
  assert.match(warnings.join('\n'), /only the first 6 notes/);
  assert.match(warnings.join('\n'), /macros\.1: position 16 is outside/);
  assert.match(warnings.join('\n'), /micro_step is not written/);
  const cut = compileSong({ patterns: { a: { length: 32, tracks: { drum1: { params: { pan: { 30: 1, 29: 2 } } } } } } });
  assert.match(cut.warnings.join('\n'), /params\.pan: position 30 does not fit .* \(max step 29\.333\)/);
  assert.deepEqual(cut.project.patterns[4][0].paramLocks, { pan: { 29: 2 } });
});

test('semantic validation rejects what the JSON schema cannot', () => {
  const base = (patch) => ({ patterns: { a: { length: 16, tracks: { drum1: { steps: { 0: {} } } } } }, ...patch });
  assert.throws(() => validateSong({ patterns: {} }), /at least one pattern/);
  assert.throws(() => validateSong({ patterns: { a: { length: 16, tracks: {} }, b: { length: 32, tracks: {} } } }), /different lengths \(a: 16, b: 32\)/);
  assert.throws(() => validateSong(base({ patterns: { a: { tracks: { synth1: { steps: { 0: { sample: 3 } } } } } } })), /"sample" is not a synth step key/);
  assert.throws(() => validateSong(base({ patterns: { a: { tracks: { drum1: { steps: { 0: { note: 60 } } } } } } })), /"note" is not a drum step key/);
  assert.throws(() => validateSong(base({ patterns: { a: { tracks: { drum1: { macros: {} } } } } })), /"macros" is not a drum track key/);
  assert.throws(() => validateSong(base({ patterns: { a: { tracks: { synth1: { params: {} } } } } })), /"params" is not a synth track key/);
  assert.throws(() => validateSong(base({ patterns: { a: { tracks: { synth1: { steps: { 1.5: { note: 60 } } } } } } })), /step keys must be whole numbers/);
  assert.throws(() => validateSong(base({ patterns: { a: { tracks: { synth1: { macros: { 1: { x: 3 } } } } } } })), /automation positions must be step numbers/);
  assert.throws(() => validateSong(base({ song: ['a', 'zzz'] })), /unknown pattern "zzz"/);
  assert.throws(() => validateSong(base({ song: Array(17).fill('a') })), /16 scenes/);
  const nine = Object.fromEntries(Array.from({ length: 9 }, (_, i) => [`p${i}`, { length: 16, tracks: {} }]));
  assert.throws(() => validateSong({ patterns: nine }), /Too many unique patterns \(9\)/);
  assert.throws(() => validateSong(base({ sounds: { drum1: { preset: 'pad' } } })), /"preset" is not a drum sound key/);
  assert.throws(() => validateSong(base({ sounds: { synth1: { sample: 2 } } })), /"sample" is not a synth sound key/);
  assert.throws(() => validateSong(base({ fx: { sidechain: { drum1: {} } } })), /cannot be sidechained/);
  assert.throws(() => validateSong(base({ fx: { reverb_sends: { bad: 1 } } })), /unknown track "bad"/);
  assert.throws(() => validateSong(base({ patterns: { a: { tracks: { organ: {} } } } })), /unknown track/);
  assert.doesNotThrow(() => validateSong(base({})));
});

test('FX presets resolve by name to the same index as by number', () => {
  const byIndex = compileSong({ patterns: { a: { tracks: {} } }, fx: { reverb_preset: 6, delay_preset: 9 } }).project;
  const byName = compileSong({ patterns: { a: { tracks: {} } }, fx: { reverb_preset: 'Hall - long reflection', delay_preset: '8th Dotted Ping Pong' } }).project;
  assert.equal(byName.reverbPreset, 6);
  assert.equal(byName.delayPreset, 9);
  assert.deepEqual(byName.fx, byIndex.fx);
  assert.throws(() => compileSong({ patterns: { a: { tracks: {} } }, fx: { reverb_preset: 'Cathedral' } }), /Unknown reverb preset "Cathedral"/);
  const { project, warnings } = compileSong({ patterns: { a: { tracks: {} } }, fx: { delay_preset: 40 } });
  assert.equal(project.delayPreset, 15);
  assert.match(warnings[0], /out of range/);
});

test('trackConfigToPattern builds a standalone pattern for set_pattern', () => {
  const pat = trackConfigToPattern('synth1', { steps: { 0: { notes: [60, 64], gate: 1, macros: { 5: 100 } }, 3: { note: 61 } }, macros: { 5: { 2.5: 10 } } }, 16, { scaleRoot: 0, scaleType: 1 });
  assert.equal(pat.kind, 'synth');
  assert.equal(pat.settings.playbackEnd, 15);
  assert.equal(pat.steps[0].mask, 0b11);
  assert.deepEqual(pat.steps[0].notes[1], { note: 76, gate: 6, delay: 0, velocity: 100 });
  assert.equal(pat.steps[3].notes[0].note, 74); // 61 -> 62 (C major, tie rounds up), +12
  assert.deepEqual(pat.paramLocks, { macro5: { 0: 100, 2.5: 10 } });
  const drum = trackConfigToPattern('drum2', { steps: { 4: { velocity: 90 }, 5: { sample: 7 } }, params: { pitch: { 0.75: 40 } } }, 16, { drumSample: 3 });
  assert.equal(drum.kind, 'drum');
  assert.deepEqual(drum.steps[4], { active: true, micro: null, velocity: 90, probability: 7, drumChoice: 3 });
  assert.equal(drum.steps[5].drumChoice, 7);
  assert.deepEqual(drum.paramLocks, { pitch: { 0.75: 40 } });
  assert.equal(trackConfigToPattern('drum1', { steps: { 0: {} } }, 16).steps[0].drumChoice, 0xff);
  assert.throws(() => trackConfigToPattern('bass', {}, 16), /Unknown track "bass"/);
});

test('patchBytesToSound decodes a built patch back to its config essentials', () => {
  const { project } = compileSong({
    patterns: { a: { tracks: {} } },
    sounds: { synth1: { name: 'RoundTrip', preset: 'pad', mod_matrix: [{ source1: 'velocity', dest: 'osc 2 level', depth: -10 }], macros: { 3: { targets: [{ dest: 'env1_attack', start: 5, end: 100, depth: 120 }], position: 9 } } } },
  });
  const sound = patchBytesToSound(project.synth1Patch);
  assert.equal(sound.name, 'RoundTrip');
  assert.deepEqual(sound.mod_matrix, [{ source: 'velocity', dest: 'osc 2 level', depth: -10 }]);
  assert.deepEqual(sound.macros['3'], { targets: [{ dest: 'env1_attack', start: 5, end: 100, depth: 120 }], position: 9 });
  assert.equal(Object.keys(sound.params).length, 84);
  assert.equal(patchBytesToSound(null), null);
});

test('cloneProject copies typed arrays and nested objects', () => {
  const base = emptyProject();
  const copy = cloneProject(base);
  copy.synth1Patch[0] = 1;
  copy.patterns[0][0].steps[0].mask = 5;
  assert.notEqual(base.synth1Patch[0], 1);
  assert.equal(base.patterns[0][0].steps[0].mask, 0);
});

test('validateTrackConfig and patternSlotToSong support set_pattern / get_pattern', () => {
  assert.throws(() => validateTrackConfig('drum1', { steps: { 0: { gate: 1 } } }), /drum1\.steps\.0: "gate" is not a drum step key/);
  assert.throws(() => validateTrackConfig('synth1', { steps: [] }), /must map step indices/);
  assert.doesNotThrow(() => validateTrackConfig('midi2', { steps: { 3: { note: 40 } }, macros: { 2: { 1.5: 3 } } }));
  const { project } = compileSong(JSON.parse(read('song-order.song.json')), { baseProject: emptyProject() });
  const readback = JSON.parse(read('song-order.readback.json'));
  assert.deepEqual(patternSlotToSong(project, 2), readback.patterns.pattern_2);
  assert.equal(slotIsEmpty(project, 2), false);
  assert.equal(slotIsEmpty(project, 3), true); // "unused" was not written
  assert.deepEqual(patternSlotToSong(project, 3), { length: 1 });
});
