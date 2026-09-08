import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import {
  buildPatchBytes, buildPatch, PatchBuilder, PRESETS, PRESET_BUILDERS,
  resolveModSource, resolveModDestination, resolveMacroDestination,
  MOD_SOURCES, MOD_DESTINATIONS, MACRO_DESTINATIONS,
} from '../js/agent/patch-builder.js';
import { PARAM_OFFSETS } from '../js/patch.js';

const vectorDir = new URL('./vectors/patches/', import.meta.url);
const vectors = readdirSync(vectorDir).filter((f) => f.endsWith('.json')).sort()
  .map((f) => ({ name: f.replace(/\.json$/, ''), ...JSON.parse(readFileSync(new URL(f, vectorDir))) }));

test('golden vectors exist', () => {
  assert.ok(vectors.length >= 6, `only ${vectors.length} patch vectors`);
});

for (const v of vectors) {
  test(`patch vector ${v.name} is byte-identical to the Python builder`, () => {
    const bytes = buildPatchBytes(v.config);
    assert.equal(bytes.length, 340);
    const diffs = [];
    for (let i = 0; i < 340; i++) if (bytes[i] !== v.bytes[i]) diffs.push(`[${i}] js=${bytes[i]} py=${v.bytes[i]}`);
    assert.deepEqual(diffs, []);
  });
}

test('presets are exposed and build distinct patches named after themselves', () => {
  assert.deepEqual(PRESETS, ['pad', 'bass', 'lead', 'pluck']);
  const names = PRESETS.map((p) => buildPatch({ preset: p }).name);
  assert.deepEqual(names, ['pad', 'bass', 'lead', 'pluck']);
  assert.equal(PRESET_BUILDERS.pad().build()[64], 65); // pad filter frequency
  assert.notDeepEqual([...buildPatchBytes({ preset: 'pad' })], [...buildPatchBytes({ preset: 'lead' })]);
});

test('the init template follows the Programmer\'s Reference defaults', () => {
  const b = new PatchBuilder().build();
  assert.equal(String.fromCharCode(...b.slice(0, 16)), 'Init            ');
  assert.equal(b[63], 1); // LP24
  assert.equal(b[64], 127); // filter open
  assert.equal(b[109], 125); // treble frequency
  for (let s = 0; s < 20; s++) assert.equal(b[124 + s * 4 + 2], 64);
  for (let k = 0; k < 8; k++) for (let t = 0; t < 4; t++) {
    const tb = 204 + k * 17 + 1 + t * 4;
    assert.deepEqual([b[tb], b[tb + 1], b[tb + 2], b[tb + 3]], [0, 0, 127, 64]);
  }
});

test('mod matrix names normalise like Python (case, underscores) and reject unknowns', () => {
  assert.equal(resolveModSource('lfo 1+/-'), 7);
  assert.equal(resolveModSource('env_filter'), 11);
  assert.equal(resolveModSource(12), 12);
  assert.equal(resolveModDestination('Filter Frequency'), 12);
  assert.equal(resolveModDestination('osc_1_&_2_pitch'), 0);
  assert.throws(() => resolveModSource('LFO 3'), /Unknown mod source "LFO 3". Valid: "direct", "velocity"/);
  assert.throws(() => resolveModDestination('cutoff'), /Unknown mod destination "cutoff"/);
  assert.throws(() => resolveModSource(200), /out of range/);
  assert.equal(Object.keys(MOD_SOURCES).length, 10);
  assert.equal(Object.keys(MOD_DESTINATIONS).length, 18);
});

test('macro destinations accept Python names, the constants.js modN_depth alias and indices', () => {
  assert.equal(resolveMacroDestination('filter_frequency'), 21);
  assert.equal(resolveMacroDestination('mod_matrix_3_depth'), 53);
  assert.equal(resolveMacroDestination('mod3_depth'), 53);
  assert.equal(resolveMacroDestination(70), 70);
  assert.equal(Object.keys(MACRO_DESTINATIONS).length, 71);
  assert.throws(() => resolveMacroDestination('filter frequency'), /Unknown macro destination "filter frequency"/);
});

test('macro targets get Python\'s key hints and dest is required', () => {
  const b = new PatchBuilder();
  assert.throws(() => b.setMacro(5, [{ param: 'filter_frequency' }]), /unknown key "param", did you mean "dest"\?/);
  assert.throws(() => b.setMacro(5, [{ min: 0, dest: 'drive' }]), /did you mean "start"/);
  assert.throws(() => b.setMacro(5, [{ start: 0 }]), /missing required key "dest"/);
  assert.throws(() => b.setMacro(9, []), /macro number must be 1-8/);
  assert.throws(() => b.setMacro(1, [{ dest: 0 }, { dest: 0 }, { dest: 0 }, { dest: 0 }, { dest: 0 }]), /Maximum 4 targets/);
});

test('unknown params and presets raise with hints instead of being ignored', () => {
  assert.throws(() => buildPatchBytes({ params: { filter_freq: 3 } }), /Unknown synth parameter "filter_freq". Did you mean "filter_frequency"\?/);
  assert.throws(() => buildPatchBytes({ preset: 'organ' }), /Unknown preset "organ". Valid presets: pad, bass, lead, pluck/);
  assert.throws(() => buildPatchBytes({ mod_matrix: [{ source1: 'velocity' }] }), /mod_matrix\[0\]: missing "dest"/);
});

test('config application order: params, then cleared mod matrix, then macros', () => {
  const bytes = buildPatchBytes({
    preset: 'pad', // pad has one LFO->filter routing in slot 0
    params: { mod1_depth: 100, filter_frequency: 12 },
    mod_matrix: [{ source1: 'velocity', dest: 'filter resonance', depth: 10 }],
    macros: { 2: { targets: [{ dest: 'drive', start: 5, end: 100, depth: 90 }], position: 33 } },
  });
  assert.equal(bytes[64], 12);
  assert.deepEqual([...bytes.slice(124, 128)], [4, 0, 74, 13]); // slot 0 replaced by the config entry
  assert.deepEqual([...bytes.slice(128, 132)], [0, 0, 64, 0]); // slot 1 cleared (mod1_depth param overwritten)
  const base = 204 + 17;
  assert.deepEqual([...bytes.slice(base, base + 5)], [33, 23, 5, 100, 90]);
  assert.deepEqual([...bytes.slice(base + 5, base + 9)], [0, 0, 127, 64]);
});

test('every PARAM_OFFSETS name is addressable and clamps to 0-127', () => {
  const all = Object.fromEntries(Object.keys(PARAM_OFFSETS).map((k) => [k, 300]));
  const bytes = buildPatchBytes({ params: all });
  for (const off of Object.values(PARAM_OFFSETS)) assert.equal(bytes[off], 127);
  assert.equal(buildPatchBytes({ params: { drive: -9 } })[61], 0);
});
