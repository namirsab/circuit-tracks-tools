import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validate, formatErrors } from '../js/agent/schema.js';

const songSchema = JSON.parse(readFileSync(new URL('../data/song.schema.json', import.meta.url)));

const FULL_SONG = {
  name: 'Test Techno',
  bpm: 130,
  swing: 55,
  color: 3,
  scale: { root: 'D', type: 'minor' },
  sounds: {
    synth1: { preset: 'pad', name: 'TestPad', params: { filter_frequency: 80 } },
    synth2: { preset: 'bass' },
    drum1: { sample: 0 },
    drum2: { sample: 2 },
  },
  fx: {
    reverb: { type: 2, decay: 80, damping: 60 },
    delay: { time: 64, feedback: 70 },
    reverb_sends: { synth1: 40, drum2: 10 },
    delay_sends: { synth1: 30 },
    sidechain: { synth1: { source: 'drum1', depth: 80 } },
  },
  mixer: { synth1: { level: 110, pan: 50 } },
  patterns: {
    intro: {
      length: 16,
      tracks: {
        synth1: {
          steps: {
            0: { note: 62, velocity: 100, gate: 0.8 },
            8: { notes: [62, 65, 69], velocity: 90, macros: { 1: 80 } },
          },
          macros: { 5: { 0: 20, 8: 100 } },
        },
        drum1: { steps: { 0: {}, 4: {}, 8: {}, 12: {} }, params: { pitch: { 0: 30 } } },
        drum2: { steps: { 4: { velocity: 80 }, 12: { velocity: 80, sample: 3 } } },
      },
    },
  },
  song: ['intro', 'intro'],
};

test('song schema accepts a full song', () => {
  assert.deepEqual(validate(songSchema, FULL_SONG), []);
});

test('song schema accepts the minimal song', () => {
  const song = { patterns: { a: { tracks: { drum1: { steps: { 0: {} } } } } } };
  assert.deepEqual(validate(songSchema, song), []);
});

test('missing required key is reported at the object', () => {
  const errors = validate(songSchema, { name: 'x' });
  assert.equal(errors.length, 1);
  assert.equal(errors[0].path, '$');
  assert.match(errors[0].message, /missing required key "patterns"/);
});

test('unknown step key gets a did-you-mean hint and the allowed keys', () => {
  const song = structuredClone(FULL_SONG);
  song.patterns.intro.tracks.synth1.steps[0]['p-locks'] = { 1: 80 };
  const errors = validate(songSchema, song);
  assert.equal(errors.length, 1);
  assert.equal(errors[0].path, '$.patterns.intro.tracks.synth1.steps["0"]["p-locks"]');
  assert.match(errors[0].message, /unknown key "p-locks"/);
  assert.match(errors[0].message, /allowed keys: .*macros/);
});

test('out-of-range and wrong-type values point at the exact path', () => {
  const song = structuredClone(FULL_SONG);
  song.bpm = 300;
  song.patterns.intro.tracks.synth1.steps[0].velocity = '100';
  const errors = validate(songSchema, song);
  const text = formatErrors(errors);
  assert.match(text, /\$\.bpm: must be <= 240; got 300/);
  assert.match(text, /steps\["0"\]\.velocity: expected integer or null, got string "100"/);
});

test('enum mismatch suggests the closest value', () => {
  const song = structuredClone(FULL_SONG);
  song.scale.type = 'Minor ';
  const errors = validate(songSchema, song);
  assert.equal(errors.length, 1);
  assert.match(errors[0].message, /did you mean "minor"\?/);
});

test('nullable fields accept null and reject other types', () => {
  const s = { type: 'object', properties: { x: { anyOf: [{ type: 'integer', minimum: 0 }, { type: 'null' }] } } };
  assert.deepEqual(validate(s, { x: null }), []);
  assert.deepEqual(validate(s, { x: 3 }), []);
  assert.equal(validate(s, { x: -1 })[0].message, 'must be >= 0; got -1');
  assert.match(validate(s, { x: 'a' })[0].message, /expected integer or null, got string "a"/);
});

test('arrays, patternProperties, propertyNames and $ref', () => {
  const s = {
    $defs: { step: { type: 'object', properties: { v: { type: 'integer' } }, additionalProperties: false } },
    type: 'object',
    properties: {
      notes: { type: 'array', items: { type: 'integer', maximum: 127 }, minItems: 1 },
      steps: { type: 'object', additionalProperties: { $ref: '#/$defs/step' }, propertyNames: { pattern: '^\\d+(\\.\\d+)?$' } },
      tags: { type: 'object', patternProperties: { '^t_': { type: 'string' } }, additionalProperties: false },
    },
  };
  assert.deepEqual(validate(s, { notes: [60], steps: { 0: { v: 1 }, 1.5: {} }, tags: { t_a: 'x' } }), []);
  const text = formatErrors(validate(s, { notes: [], steps: { a: { v: 'q' } }, tags: { u: 'x' } }));
  assert.match(text, /\$\.notes: must have at least 1 items/);
  assert.match(text, /\$\.steps\.a\.v: expected integer, got string/);
  assert.match(text, /\$\.steps\.a: invalid key "a"/);
  assert.match(text, /\$\.tags\.u: unknown key "u"/);
});

test('integers accept 5.0 but not 5.5', () => {
  const s = { type: 'integer' };
  assert.deepEqual(validate(s, 5.0), []);
  assert.match(validate(s, 5.5)[0].message, /expected integer, got number 5.5/);
});
