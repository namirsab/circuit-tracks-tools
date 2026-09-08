// 340-byte synth patch decoding — ported from src/circuit_tracks/patch.py
// and render/patch_translator.py (Programmer's Reference Guide v3 layout).

export const PATCH_SIZE = 340;

export const PARAM_OFFSETS = {
  polyphony_mode: 32, portamento_rate: 33, pre_glide: 34, keyboard_octave: 35,
  osc1_wave: 36, osc1_wave_interpolate: 37, osc1_pulse_width_index: 38,
  osc1_virtual_sync_depth: 39, osc1_density: 40, osc1_density_detune: 41,
  osc1_semitones: 42, osc1_cents: 43, osc1_pitchbend: 44,
  osc2_wave: 45, osc2_wave_interpolate: 46, osc2_pulse_width_index: 47,
  osc2_virtual_sync_depth: 48, osc2_density: 49, osc2_density_detune: 50,
  osc2_semitones: 51, osc2_cents: 52, osc2_pitchbend: 53,
  osc1_level: 54, osc2_level: 55, ring_mod_level: 56, noise_level: 57,
  pre_fx_level: 58, post_fx_level: 59,
  routing: 60, drive: 61, drive_type: 62, filter_type: 63,
  filter_frequency: 64, filter_tracking: 65, filter_resonance: 66,
  filter_q_normalize: 67, env2_to_filter_freq: 68,
  env1_velocity: 69, env1_attack: 70, env1_decay: 71, env1_sustain: 72, env1_release: 73,
  env2_velocity: 74, env2_attack: 75, env2_decay: 76, env2_sustain: 77, env2_release: 78,
  env3_delay: 79, env3_attack: 80, env3_decay: 81, env3_sustain: 82, env3_release: 83,
  lfo1_waveform: 84, lfo1_phase_offset: 85, lfo1_slew_rate: 86, lfo1_delay: 87,
  lfo1_delay_sync: 88, lfo1_rate: 89, lfo1_rate_sync: 90, lfo1_flags: 91,
  lfo2_waveform: 92, lfo2_phase_offset: 93, lfo2_slew_rate: 94, lfo2_delay: 95,
  lfo2_delay_sync: 96, lfo2_rate: 97, lfo2_rate_sync: 98, lfo2_flags: 99,
  distortion_level: 100, chorus_level: 102,
  eq_bass_frequency: 105, eq_bass_level: 106, eq_mid_frequency: 107,
  eq_mid_level: 108, eq_treble_frequency: 109, eq_treble_level: 110,
  distortion_type: 116, distortion_compensation: 117,
  chorus_type: 118, chorus_rate: 119, chorus_rate_sync: 120,
  chorus_feedback: 121, chorus_mod_depth: 122, chorus_delay: 123,
};
for (let s = 1; s <= 20; s++) {
  const base = 124 + (s - 1) * 4;
  PARAM_OFFSETS[`mod${s}_source1`] = base;
  PARAM_OFFSETS[`mod${s}_source2`] = base + 1;
  PARAM_OFFSETS[`mod${s}_depth`] = base + 2;
  PARAM_OFFSETS[`mod${s}_destination`] = base + 3;
}

export function decodePatch(bytes) {
  if (bytes.length < PATCH_SIZE) {
    throw new Error(`Patch must be ${PATCH_SIZE} bytes, got ${bytes.length}`);
  }
  let name = '';
  for (let i = 0; i < 16; i++) {
    const b = bytes[i];
    if (b >= 32 && b <= 126) name += String.fromCharCode(b);
  }
  name = name.trim();

  const params = {};
  for (const [k, off] of Object.entries(PARAM_OFFSETS)) params[k] = bytes[off];

  const modMatrix = [];
  for (let s = 0; s < 20; s++) {
    const base = 124 + s * 4;
    modMatrix.push({
      source1: bytes[base], source2: bytes[base + 1],
      depth: bytes[base + 2], destination: bytes[base + 3],
    });
  }

  // 8 macros × 17 bytes at offset 204: position + 4 × (dest, start, end, depth).
  // start/end are knob positions bounding the active range; depth is the
  // bipolar amount (byte − 64). An unused target is (0, 0, 127, 64).
  const macros = [];
  for (let k = 0; k < 8; k++) {
    const base = 204 + k * 17;
    const targets = [];
    for (let tIdx = 0; tIdx < 4; tIdx++) {
      const off = base + 1 + tIdx * 4;
      const depth = bytes[off + 3] - 64;
      if (depth !== 0 && bytes[off + 2] > bytes[off + 1]) {
        targets.push({
          destination: bytes[off], start: bytes[off + 1], end: bytes[off + 2], depth,
        });
      }
    }
    macros.push({ position: bytes[base], targets });
  }

  return { name, category: bytes[16], genre: bytes[17], params, modMatrix, macros, raw: bytes };
}

// Parse a .syx synth patch file: F0 00 20 29 01 64 <cmd> <idx> 00 <340 bytes> F7
export function parseSyxPatch(arrayBuffer) {
  const raw = new Uint8Array(arrayBuffer);
  if (raw.length < 10 || raw[0] !== 0xf0 || raw[raw.length - 1] !== 0xf7) {
    throw new Error('Not a valid SysEx file');
  }
  const data = raw.subarray(1, raw.length - 1);
  // Patch binary starts at byte 8: manufacturer(3) + product(2) + cmd + index + 0
  const patchBytes = data.subarray(8);
  return decodePatch(patchBytes);
}

// Wrap 340 patch bytes as a Circuit Tracks .syx message for synth 0/1:
// F0 00 20 29 01 64 <cmd 0> <synth> 00 <340 bytes> F7 (the inverse of parseSyxPatch).
export function encodeSyxPatch(raw, synthIdx) {
  return new Uint8Array([0xf0, 0x00, 0x20, 0x29, 0x01, 0x64, 0x00, synthIdx, 0x00, ...raw.subarray(0, PATCH_SIZE), 0xf7]);
}

export function initPatch() {
  const bytes = new Uint8Array(PATCH_SIZE);
  for (let i = 0; i < 16; i++) bytes[i] = 0x20;
  const name = 'Initial Patch';
  for (let i = 0; i < name.length; i++) bytes[i] = name.charCodeAt(i);
  const set = (k, v) => { bytes[PARAM_OFFSETS[k]] = v; };
  set('polyphony_mode', 2);
  set('keyboard_octave', 64);
  set('osc1_wave', 2); // sawtooth
  set('osc1_semitones', 64); set('osc1_cents', 64);
  set('osc2_wave', 2);
  set('osc2_semitones', 64); set('osc2_cents', 64);
  set('osc1_level', 127);
  set('pre_fx_level', 64); set('post_fx_level', 64);
  set('filter_frequency', 127);
  set('env2_to_filter_freq', 64);
  set('env1_decay', 90); set('env1_sustain', 127); set('env1_release', 40);
  set('env2_decay', 75); set('env2_release', 40);
  set('lfo1_rate', 68); set('lfo2_rate', 68);
  set('eq_bass_level', 64); set('eq_mid_level', 64); set('eq_treble_level', 64);
  return decodePatch(bytes);
}
