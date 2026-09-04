import test from 'node:test';
import assert from 'node:assert/strict';
import {
  mixdownToMono, resampleLinear, floatTo16BitPcm,
  buildWavFile, parseWavHeader, convertToCircuitWav,
  normalize, applyGain, applyFades, trim,
  TARGET_SAMPLE_RATE,
} from '../js/audio/convert.js';

function sine(freq, rate, seconds, amp = 0.5) {
  const n = Math.round(rate * seconds);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = amp * Math.sin((2 * Math.PI * freq * i) / rate);
  return out;
}

test('stereo 44.1 kHz input converts to mono 48 kHz 16-bit WAV with valid header', () => {
  const left = sine(440, 44100, 0.5, 0.5);
  const right = sine(880, 44100, 0.5, 0.3);
  const { wav, pcm, seconds } = convertToCircuitWav([left, right], 44100);

  const header = parseWavHeader(wav);
  assert.equal(header.audioFormat, 1); // PCM
  assert.equal(header.channels, 1);
  assert.equal(header.sampleRate, 48000);
  assert.equal(header.bitsPerSample, 16);
  assert.equal(header.byteRate, 96000);
  assert.equal(header.blockAlign, 2);
  assert.equal(header.dataSize, pcm.length * 2);
  assert.equal(wav.length, 44 + pcm.length * 2);

  // 0.5 s at 44.1 kHz should come out as ~0.5 s at 48 kHz
  assert.ok(Math.abs(seconds - 0.5) < 0.001, `duration was ${seconds}`);
  assert.ok(Math.abs(pcm.length - 24000) <= 2, `pcm length was ${pcm.length}`);
});

test('converted WAV header matches the factory-sample layout byte-for-byte', () => {
  // Factory samples: RIFF/WAVE, 16-byte fmt chunk, PCM(1), mono, 48000 Hz,
  // byte rate 96000, block align 2, 16 bits, then the data chunk.
  const { wav } = convertToCircuitWav([sine(440, 48000, 0.1)], 48000);
  const expectFmt = [1, 1]; // audioFormat, channels
  const v = new DataView(wav.buffer);
  assert.equal(String.fromCharCode(...wav.subarray(0, 4)), 'RIFF');
  assert.equal(String.fromCharCode(...wav.subarray(8, 12)), 'WAVE');
  assert.equal(String.fromCharCode(...wav.subarray(12, 16)), 'fmt ');
  assert.equal(v.getUint32(16, true), 16);
  assert.equal(v.getUint16(20, true), expectFmt[0]);
  assert.equal(v.getUint16(22, true), expectFmt[1]);
  assert.equal(v.getUint32(24, true), 48000);
  assert.equal(String.fromCharCode(...wav.subarray(36, 40)), 'data');
  assert.equal(v.getUint32(4, true), wav.length - 8); // RIFF size
});

test('mixdown averages channels', () => {
  const mono = mixdownToMono([
    new Float32Array([1, 0, -1]),
    new Float32Array([0, 0, -1]),
  ]);
  assert.deepEqual(Array.from(mono), [0.5, 0, -1]);
});

test('resample preserves duration and passthrough at equal rates', () => {
  const src = sine(440, 44100, 1.0);
  const out = resampleLinear(src, 44100, 48000);
  assert.ok(Math.abs(out.length - 48000) <= 2);
  assert.equal(resampleLinear(src, 48000, 48000), src);
});

test('resample roughly preserves a sine wave (low interpolation error)', () => {
  const src = sine(440, 44100, 0.25, 0.8);
  const out = resampleLinear(src, 44100, 48000);
  // Compare against an ideal 48 kHz sine; linear interp error should be small
  let maxErr = 0;
  for (let i = 0; i < out.length; i++) {
    const ideal = 0.8 * Math.sin((2 * Math.PI * 440 * i) / 48000);
    maxErr = Math.max(maxErr, Math.abs(out[i] - ideal));
  }
  assert.ok(maxErr < 0.01, `max error ${maxErr}`);
});

test('16-bit encode clamps and scales correctly', () => {
  const pcm = floatTo16BitPcm(new Float32Array([0, 1, -1, 2, -2, 0.5]));
  assert.deepEqual(Array.from(pcm), [0, 32767, -32768, 32767, -32768, 16384]);
});

test('parseWavHeader reads back what buildWavFile writes', () => {
  const pcm = new Int16Array([0, 1000, -1000, 32767, -32768]);
  const header = parseWavHeader(buildWavFile(pcm));
  assert.equal(header.sampleRate, TARGET_SAMPLE_RATE);
  assert.equal(header.dataSize, 10);
});

test('normalize scales peak to target', () => {
  const out = normalize(new Float32Array([0.25, -0.5, 0.1]), 1.0);
  assert.ok(Math.abs(Math.min(...out) + 1.0) < 1e-6);
  // Silence stays silent
  assert.deepEqual(Array.from(normalize(new Float32Array(3))), [0, 0, 0]);
});

test('gain, fades and trim behave as expected', () => {
  assert.deepEqual(Array.from(applyGain(new Float32Array([0.5, -0.5]), 2)), [1, -1]);

  const faded = applyFades(new Float32Array([1, 1, 1, 1, 1, 1]), 2, 2);
  assert.equal(faded[0], 0);
  assert.equal(faded[1], 0.5);
  assert.equal(faded[2], 1);
  assert.equal(faded[5], 0);
  assert.equal(faded[4], 0.5);

  assert.deepEqual(Array.from(trim(new Float32Array([1, 2, 3, 4]), 1, 3)), [2, 3]);
});
