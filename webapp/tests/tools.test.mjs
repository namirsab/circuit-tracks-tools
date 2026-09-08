import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { ToolRegistry } from '../js/agent/registry.js';
import { createTools } from '../js/agent/tools.js';

// A recording fake of AgentApi: every method logs its call and returns a marker.
function fakeApi() {
  const calls = [];
  const handler = {
    get(_, prop) {
      if (prop === 'calls') return calls;
      if (prop === 'seq') return { bpm: 120 };
      if (prop === 'snapshot') return async (label) => { calls.push(['snapshot', label]); };
      return (...args) => { calls.push([prop, ...args]); return `${String(prop)}:ok`; };
    },
  };
  return new Proxy({}, handler);
}

const reference = { '': { available_sections: ['synth'], best_practices: {} }, synth: { section: 'synth', synth_cc_params: ['filter_frequency'] } };

function setup() {
  const api = fakeApi();
  const reg = new ToolRegistry().registerAll(createTools(api, { loadJson: async () => reference }));
  return { api, reg };
}

test('every tool has a description and an object schema without additional keys', () => {
  const { reg } = setup();
  for (const t of reg.list()) {
    assert.ok(t.description.length > 20, `${t.name} needs a description`);
    assert.equal(t.inputSchema.type, 'object', t.name);
    assert.equal(t.inputSchema.additionalProperties, false, t.name);
  }
  assert.ok(reg.names().includes('get_parameter_reference'));
});

test('mirrors the hardware server tool names for the shared subset', () => {
  const { reg } = setup();
  for (const name of ['get_parameter_reference', 'get_sequencer_status', 'start_sequencer', 'stop_sequencer', 'transport',
    'set_bpm', 'mute_track', 'set_synth_params', 'set_drum_params', 'set_project_params', 'set_macro', 'get_macros',
    'play_notes', 'play_drum', 'list_drum_samples', 'select_patch', 'select_project', 'get_synth_patch', 'edit_synth_patch',
    'export_song_to_project']) {
    assert.ok(reg.has(name), `missing ${name}`);
  }
});

test('get_parameter_reference serves sections and adds Web Tracks notes to the overview', async () => {
  const { reg } = setup();
  const overview = await reg.call('get_parameter_reference', {});
  assert.ok(Array.isArray(overview.structuredContent.web_tracks));
  const synth = await reg.call('get_parameter_reference', { section: 'synth' });
  assert.deepEqual(synth.structuredContent, reference.synth);
  const bad = await reg.call('get_parameter_reference', { section: 'nope' });
  assert.equal(bad.isError, true);
  assert.match(bad.content[0].text, /must be one of/);
});

test('arguments are validated before the api is touched', async () => {
  const { api, reg } = setup();
  const r = await reg.call('mute_track', { track: 'synth9' });
  assert.equal(r.isError, true);
  assert.match(r.content[0].text, /\$\.track: must be one of "synth1"/);
  const r2 = await reg.call('set_synth_params', { synth: 3, params: { filter_frequency: 80 } });
  assert.match(r2.content[0].text, /\$\.synth: must be <= 2/);
  const r3 = await reg.call('set_synth_params', { synth: 1, params: {} });
  assert.match(r3.content[0].text, /\$\.params: must have at least 1 entries/);
  const r4 = await reg.call('set_drum_params', { drum: 1, params: { pitch: 200 } });
  assert.match(r4.content[0].text, /\$\.params\.pitch: must be <= 127/);
  assert.deepEqual(api.calls, []);
});

test('valid calls reach the api with resolved arguments', async () => {
  const { api, reg } = setup();
  await reg.call('mute_track', { track: 'drum2' });
  await reg.call('set_macro', { synth: 2, macro: 5, value: 100 });
  await reg.call('set_synth_params', { synth: 1, params: { filter_frequency: 80, name: 'Bass' } });
  await reg.call('select_project', { project_number: 3 });
  await reg.call('transport', { action: 'continue', bpm: 128 });
  assert.deepEqual(api.calls, [
    ['mute', 'drum2', true],
    ['setMacro', 2, 5, 100],
    ['setSynthParams', 1, { filter_frequency: 80, name: 'Bass' }],
    ['snapshot', 'select_project'],
    ['selectProject', 3, { queued: false }],
    ['setBpm', 128],
    ['play', { resume: true }],
  ]);
});

test('read-only tools carry the readOnlyHint annotation', () => {
  const { reg } = setup();
  const ro = reg.list().filter((t) => t.annotations?.readOnlyHint).map((t) => t.name);
  assert.deepEqual(ro, ['get_parameter_reference', 'read_project', 'get_pattern', 'list_patterns', 'get_sequencer_status',
    'get_synth_patch', 'get_macros', 'list_drum_samples', 'list_patches', 'list_projects']);
});

test('song tools embed the song schema with its $defs hoisted and validate deeply', async () => {
  const schema = JSON.parse(readFileSync(new URL('../data/song.schema.json', import.meta.url)));
  const api = fakeApi();
  const reg = new ToolRegistry().registerAll(createTools(api, { loadJson: async () => reference, songSchema: schema }));
  const load = reg.list().find((t) => t.name === 'load_song');
  assert.equal(Object.keys(load.inputSchema.$defs).length, Object.keys(schema.$defs).length);
  assert.equal(load.inputSchema.properties.song.$defs, undefined);
  const bad = await reg.call('load_song', { song: { patterns: { a: { tracks: { synth1: { steps: { 0: { velocity: 300 } } } } } } } });
  assert.equal(bad.isError, true);
  assert.match(bad.content[0].text, /\$\.song\.patterns\.a\.tracks\.synth1\.steps\["0"\]\.velocity: must be <= 127/);
  const badTrack = await reg.call('set_pattern', { name: 'x', tracks: { synth9: { steps: {} } } });
  assert.match(badTrack.content[0].text, /\$\.tracks\.synth9: invalid key "synth9"/);
  const badPreset = await reg.call('create_synth_patch', { synth: 1, name: 'p', preset: 'organ' });
  assert.match(badPreset.content[0].text, /must be one of "pad", "bass", "lead", "pluck"/);
  assert.deepEqual(api.calls, []);
  await reg.call('set_pattern', { name: 'intro', tracks: { drum1: { steps: { 0: {} } } }, length: 32 });
  await reg.call('queue_patterns', { patterns: ['intro'] });
  assert.deepEqual(api.calls, [['snapshot', 'set_pattern'], ['setPattern', 'intro', { drum1: { steps: { 0: {} } } }, 32], ['setSongOrder', ['intro'], { append: true }]]);
});

test('song tools still register without the schema', () => {
  const reg = new ToolRegistry().registerAll(createTools(fakeApi(), { loadJson: async () => reference }));
  const load = reg.list().find((t) => t.name === 'load_song');
  assert.equal(load.inputSchema.$defs, undefined);
  assert.equal(load.inputSchema.properties.song.type, 'object');
});
