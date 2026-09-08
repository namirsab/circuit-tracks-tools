import { test } from 'node:test';
import assert from 'node:assert/strict';
import { synthesizeMelody } from '../js/agent/transcribe.js';
import { LivePitchTracker } from '../js/agent/live-pitch.js';

const SR = 22050;
const CHUNK = 4096; // mic.js's ScriptProcessorNode buffer size

// Feeds `audio` through the tracker in mic-sized chunks (the streaming
// contract), collecting the onset/offset sequence it reports.
function run(audio, opts = {}) {
  const events = [];
  const tracker = new LivePitchTracker(SR, {
    ...opts,
    onNoteOn: (midi, velocity) => events.push({ type: 'on', midi, velocity }),
    onNoteOff: () => events.push({ type: 'off' }),
  });
  for (let i = 0; i < audio.length; i += CHUNK) tracker.push(audio.subarray(i, i + CHUNK));
  tracker.flush();
  return events;
}

function onNotes(events) {
  return events.filter((e) => e.type === 'on').map((e) => e.midi);
}

test('LivePitchTracker recovers a twinkle-twinkle melody', () => {
  const bpm = 100;
  const step = 60 / bpm / 4;
  const melody = [60, 60, 67, 67, 69, 69, 67].map((m) => [m, step * 2]);
  const audio = synthesizeMelody(melody, { sr: SR, seed: 4 });
  const events = run(audio);
  assert.deepEqual(onNotes(events), [60, 60, 67, 67, 69, 69, 67]);
  // Every onset closed before the take ends (flush() shouldn't add a stray one).
  assert.equal(events.filter((e) => e.type === 'off').length, 7);
});

test('LivePitchTracker separates re-articulated repeats on the same pitch', () => {
  const audio = synthesizeMelody([[64, 0.2], [64, 0.2], [64, 0.2]], { sr: SR, seed: 1 });
  const events = run(audio);
  assert.deepEqual(onNotes(events), [64, 64, 64]);
});

test('LivePitchTracker stays silent on room noise', () => {
  const n = Math.round(1.0 * SR);
  const audio = new Float32Array(n);
  let s = 777;
  for (let i = 0; i < n; i++) {
    s ^= s << 13; s ^= s >>> 17; s ^= s << 5; s |= 0;
    audio[i] = (s / 2147483648) * 0.01; // quiet noise floor, like an idle mic
  }
  const events = run(audio);
  assert.equal(events.length, 0);
});

test('LivePitchTracker ignores a single-frame blip (debounced by minNoteFrames)', () => {
  const blip = synthesizeMelody([[72, 0.02]], { sr: SR, seed: 2 }); // ~20ms, shorter than the confirm window
  const silence = new Float32Array(Math.round(0.3 * SR));
  const audio = new Float32Array(blip.length + silence.length);
  audio.set(blip, 0);
  audio.set(silence, blip.length);
  const events = run(audio);
  assert.equal(onNotes(events).length, 0);
});

test('LivePitchTracker.flush() closes a note still open at the end of the take', () => {
  const audio = synthesizeMelody([[57, 0.3]], { sr: SR, seed: 3 });
  const events = [];
  const tracker = new LivePitchTracker(SR, {
    onNoteOn: (midi) => events.push({ type: 'on', midi }),
    onNoteOff: () => events.push({ type: 'off' }),
  });
  // Feed everything but the tail so the note is still open, then flush().
  const cut = audio.subarray(0, audio.length - Math.round(0.05 * SR));
  for (let i = 0; i < cut.length; i += CHUNK) tracker.push(cut.subarray(i, i + CHUNK));
  assert.deepEqual(onNotes(events), [57]);
  assert.equal(events.filter((e) => e.type === 'off').length, 0);
  tracker.flush();
  assert.equal(events.filter((e) => e.type === 'off').length, 1);
});
