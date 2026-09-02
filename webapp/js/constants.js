// Circuit Tracks constants — ported from src/circuit_tracks/constants.py
// and docs/ncs-format.md.

export const OSC_WAVEFORMS = {
  0: 'sine', 1: 'triangle', 2: 'sawtooth',
  3: 'saw 9:1 PW', 4: 'saw 8:2 PW', 5: 'saw 7:3 PW', 6: 'saw 6:4 PW',
  7: 'saw 5:5 PW', 8: 'saw 4:6 PW', 9: 'saw 3:7 PW', 10: 'saw 2:8 PW',
  11: 'saw 1:9 PW', 12: 'pulse width', 13: 'square', 14: 'sine table',
  15: 'analogue pulse', 16: 'analogue sync', 17: 'triangle-saw blend',
  18: 'digital nasty 1', 19: 'digital nasty 2', 20: 'digital saw-square',
  21: 'digital vocal 1', 22: 'digital vocal 2', 23: 'digital vocal 3',
  24: 'digital vocal 4', 25: 'digital vocal 5', 26: 'digital vocal 6',
  27: 'random collection 1', 28: 'random collection 2', 29: 'random collection 3',
};

export const FILTER_TYPES = {
  0: 'low pass 12dB', 1: 'low pass 24dB',
  2: 'band pass 6/6 dB', 3: 'band pass 12/12 dB',
  4: 'high pass 12dB', 5: 'high pass 24dB',
};

export const DISTORTION_TYPES = {
  0: 'diode', 1: 'valve', 2: 'clipper', 3: 'cross-over',
  4: 'rectifier', 5: 'bit reducer', 6: 'rate reducer',
};

export const LFO_WAVEFORMS = {
  0: 'sine', 1: 'triangle', 2: 'sawtooth', 3: 'square',
  4: 'random S/H', 5: 'time S/H', 6: 'piano envelope',
};

export const REVERB_TYPES = {
  0: 'Chamber', 1: 'Small Room', 2: 'Large Room',
  3: 'Small Hall', 4: 'Large Hall', 5: 'Great Hall',
};

// L/R delay time ratios indexed by delay_lr_ratio (0-12).
export const DELAY_LR_RATIOS = [
  [1, 1], [4, 3], [3, 4], [3, 2], [2, 3], [2, 1], [1, 2],
  [3, 1], [1, 3], [4, 1], [1, 4], [1, 0], [0, 1],
];

export const REVERB_PRESETS = [
  { type: 0, decay: 80, damping: 120 },
  { type: 1, decay: 90, damping: 100 },
  { type: 2, decay: 80, damping: 80 },
  { type: 2, decay: 100, damping: 110 },
  { type: 3, decay: 90, damping: 100 },
  { type: 4, decay: 105, damping: 105 },
  { type: 5, decay: 90, damping: 80 },
  { type: 5, decay: 120, damping: 115 },
];

export const DELAY_PRESETS = [
  { time: 3, sync: 0, feedback: 100, width: 115, lr_ratio: 5, slew: 115 },
  { time: 6, sync: 0, feedback: 45, width: 104, lr_ratio: 6, slew: 26 },
  { time: 0, sync: 2, feedback: 63, width: 62, lr_ratio: 5, slew: 40 },
  { time: 0, sync: 4, feedback: 25, width: 10, lr_ratio: 5, slew: 75 },
  { time: 0, sync: 5, feedback: 59, width: 15, lr_ratio: 5, slew: 39 },
  { time: 0, sync: 7, feedback: 15, width: 34, lr_ratio: 6, slew: 56 },
  { time: 0, sync: 7, feedback: 75, width: 115, lr_ratio: 5, slew: 98 },
  { time: 0, sync: 7, feedback: 75, width: 75, lr_ratio: 3, slew: 23 },
  { time: 0, sync: 8, feedback: 80, width: 10, lr_ratio: 6, slew: 68 },
  { time: 0, sync: 9, feedback: 50, width: 100, lr_ratio: 5, slew: 33 },
  { time: 0, sync: 10, feedback: 82, width: 23, lr_ratio: 5, slew: 56 },
  { time: 0, sync: 10, feedback: 78, width: 88, lr_ratio: 6, slew: 47 },
  { time: 0, sync: 10, feedback: 33, width: 127, lr_ratio: 3, slew: 33 },
  { time: 0, sync: 11, feedback: 50, width: 60, lr_ratio: 6, slew: 86 },
  { time: 0, sync: 12, feedback: 24, width: 90, lr_ratio: 3, slew: 106 },
  { time: 0, sync: 12, feedback: 50, width: 115, lr_ratio: 5, slew: 111 },
];

// Sync rate (0-35) -> beats per cycle, fastest to slowest (Nova-engine
// table, shared by delay time and LFO rate sync). Anchored by the factory
// delay presets' "cycles per bar" descriptions in the user guide (p.92):
// sync 2 = 48/bar (32nd-T), 4 = 32/bar, 5 = 24/bar, 7 = 16/bar (16th),
// 8 = 12/bar, 9 = 8 per 3 beats, 10 = 8/bar (8th), 11 = 6/bar, 12 = dotted.
const SYNC_BEATS = [
  1 / 24, 1 / 16, 1 / 12, 3 / 32, 1 / 8, 1 / 6, 3 / 16, 1 / 4,
  1 / 3, 3 / 8, 1 / 2, 2 / 3, 3 / 4, 1, 4 / 3, 1.5,
  2, 8 / 3, 3, 4, 16 / 3, 6, 8, 32 / 3,
  12, 16, 64 / 3, 24, 32, 128 / 3, 48, 160 / 3,
  64, 224 / 3, 256 / 3, 96,
];

export function syncToBeats(sync) {
  return SYNC_BEATS[Math.max(0, Math.min(35, sync))] ?? 0.5;
}

// Backwards-compatible alias used by the delay bus.
export const delaySyncToBeats = syncToBeats;

// LFO rate-sync ladder. Same triplet/dotted/straight ladder as the delay
// table but it starts three steps later, at a 32nd-triplet. Anchored by
// hardware: PolterGeist's LFO2 rate_sync 4 chops at regular 16th notes.
const LFO_SYNC_BEATS = [
  1 / 12, 1 / 8, 1 / 6, 3 / 16, 1 / 4, 1 / 3, 3 / 8, 1 / 2,
  2 / 3, 3 / 4, 1, 4 / 3, 1.5, 2, 8 / 3, 3,
  4, 16 / 3, 6, 8, 32 / 3, 12, 16, 64 / 3,
  24, 32, 128 / 3, 48, 64, 256 / 3, 96, 128,
  512 / 3, 192, 256, 320,
];

export function lfoSyncToBeats(sync) {
  return LFO_SYNC_BEATS[Math.max(0, Math.min(35, sync))] ?? 1;
}

// Pattern sync rate, indexed by the STORED byte (0-7). The stored value is
// reversed relative to the hardware's left-to-right pad order: 7 = 1/4
// (slowest, leftmost) ... 0 = 1/32T (fastest, rightmost). Default 3 = 1/16.
// Evidence: Perfume Disco drum 1 stores 7 and shows 1/4 on hardware; its
// 32-step synth at stored 5 (1/8) spans the same 4 bars as its 16-step
// drums at 1/4.
export const SYNC_RATE_BEATS = [1 / 12, 0.125, 1 / 6, 0.25, 1 / 3, 0.5, 2 / 3, 1];
export const SYNC_RATE_NAMES = ['1/32T', '1/32', '1/16T', '1/16', '1/8T', '1/8', '1/4T', '1/4'];

export const PROBABILITY_LEVELS = [0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0];

export const SCALE_ROOTS = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];

export const SCALE_TYPES = [
  { name: 'Natural Minor', intervals: [0, 2, 3, 5, 7, 8, 10] },
  { name: 'Major', intervals: [0, 2, 4, 5, 7, 9, 11] },
  { name: 'Dorian', intervals: [0, 2, 3, 5, 7, 9, 10] },
  { name: 'Phrygian', intervals: [0, 1, 3, 5, 7, 8, 10] },
  { name: 'Mixolydian', intervals: [0, 2, 4, 5, 7, 9, 10] },
  { name: 'Melodic Minor', intervals: [0, 2, 3, 5, 7, 9, 11] },
  { name: 'Harmonic Minor', intervals: [0, 2, 3, 5, 7, 8, 11] },
  { name: 'Bebop Dorian', intervals: [0, 2, 3, 4, 5, 7, 9, 10] },
  { name: 'Blues', intervals: [0, 3, 5, 6, 7, 10] },
  { name: 'Minor Pentatonic', intervals: [0, 3, 5, 7, 10] },
  { name: 'Hungarian Minor', intervals: [0, 2, 3, 6, 7, 8, 11] },
  { name: 'Ukrainian Dorian', intervals: [0, 2, 3, 6, 7, 9, 10] },
  { name: 'Marva', intervals: [0, 1, 4, 6, 7, 9, 11] },
  { name: 'Todi', intervals: [0, 1, 3, 6, 7, 8, 11] },
  { name: 'Whole Tone', intervals: [0, 2, 4, 6, 8, 10] },
  { name: 'Chromatic', intervals: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11] },
];

// Macro knob destination index -> patch parameter name (verified on hardware).
export const MACRO_DESTINATIONS = {
  0: 'pre_fx_level', 1: 'portamento_rate', 2: 'post_fx_level',
  3: 'osc1_wave_interpolate', 4: 'osc1_pulse_width_index', 5: 'osc1_virtual_sync_depth',
  6: 'osc1_density', 7: 'osc1_density_detune', 8: 'osc1_semitones', 9: 'osc1_cents',
  10: 'osc2_wave_interpolate', 11: 'osc2_pulse_width_index', 12: 'osc2_virtual_sync_depth',
  13: 'osc2_density', 14: 'osc2_density_detune', 15: 'osc2_semitones', 16: 'osc2_cents',
  17: 'osc1_level', 18: 'osc2_level', 19: 'ring_mod_level', 20: 'noise_level',
  21: 'filter_frequency', 22: 'filter_resonance', 23: 'drive', 24: 'filter_tracking',
  25: 'env2_to_filter_freq', 26: 'env1_attack', 27: 'env1_decay', 28: 'env1_sustain',
  29: 'env1_release', 30: 'env2_attack', 31: 'env2_decay', 32: 'env2_sustain',
  33: 'env2_release', 34: 'env3_delay', 35: 'env3_attack', 36: 'env3_decay',
  37: 'env3_sustain', 38: 'env3_release', 39: 'lfo1_rate', 40: 'lfo1_delay',
  41: 'lfo1_slew_rate', 42: 'lfo2_rate', 43: 'lfo2_delay', 44: 'lfo2_slew_rate',
  45: 'distortion_level', 46: 'chorus_level', 47: 'chorus_rate', 48: 'chorus_feedback',
  49: 'chorus_mod_depth', 50: 'chorus_delay',
};
for (let i = 51; i <= 70; i++) MACRO_DESTINATIONS[i] = `mod${i - 50}_depth`;

// Mod matrix source index -> name (Programmer's Reference Guide v3). Sparse:
// only these indices are valid sources.
export const MOD_MATRIX_SOURCES = {
  0: 'direct', 4: 'velocity', 5: 'keyboard',
  6: 'LFO 1+', 7: 'LFO 1+/-', 8: 'LFO 2+', 9: 'LFO 2+/-',
  10: 'env amp', 11: 'env filter', 12: 'env 3',
};

// Mod matrix destination index -> name.
export const MOD_MATRIX_DESTINATIONS = {
  0: 'osc 1 & 2 pitch', 1: 'osc 1 pitch', 2: 'osc 2 pitch',
  3: 'osc 1 v-sync', 4: 'osc 2 v-sync',
  5: 'osc 1 pulse width / index', 6: 'osc 2 pulse width / index',
  7: 'osc 1 level', 8: 'osc 2 level', 9: 'noise level',
  10: 'ring modulation 1*2 level', 11: 'filter drive amount',
  12: 'filter frequency', 13: 'filter resonance',
  14: 'LFO 1 rate', 15: 'LFO 2 rate',
  16: 'amp envelope decay', 17: 'filter envelope decay',
};

// Sidechain preset index (1-7) -> fixed attack/hold/decay/depth.
export const SIDECHAIN_PRESETS = {
  1: { attack: 5, hold: 50, decay: 80, depth: 80 },
  2: { attack: 5, hold: 70, decay: 70, depth: 100 },
  3: { attack: 5, hold: 85, decay: 70, depth: 115 },
  4: { attack: 5, hold: 90, decay: 75, depth: 123 },
  5: { attack: 5, hold: 90, decay: 85, depth: 127 },
  6: { attack: 5, hold: 95, decay: 95, depth: 127 },
  7: { attack: 5, hold: 102, decay: 95, depth: 127 },
};

export const TRACKS = [
  { id: 0, key: 's1', name: 'Synth 1', kind: 'synth' },
  { id: 1, key: 's2', name: 'Synth 2', kind: 'synth' },
  { id: 2, key: 'm1', name: 'MIDI 1', kind: 'midi' },
  { id: 3, key: 'm2', name: 'MIDI 2', kind: 'midi' },
  { id: 4, key: 'd1', name: 'Drum 1', kind: 'drum' },
  { id: 5, key: 'd2', name: 'Drum 2', kind: 'drum' },
  { id: 6, key: 'd3', name: 'Drum 3', kind: 'drum' },
  { id: 7, key: 'd4', name: 'Drum 4', kind: 'drum' },
];

// Index order used by NCS send/level arrays: S1,S2,D1,D2,D3,D4,M1,M2
export const SEND_ORDER = [0, 1, 4, 5, 6, 7, 2, 3];

// Track colours per the user guide (Patterns View illustration, p.73):
// S1 violet, S2 pale green, M1 blue, M2 salmon, D1 orange, D2 yellow,
// D3 lavender, D4 turquoise.
export const TRACK_COLORS = {
  0: '#f04bf0',
  1: '#4be88a',
  2: '#3c64f0',
  3: '#f07878',
  4: '#ff8c1e',
  5: '#f0e83c',
  6: '#b48cf0',
  7: '#2ee6d6',
};

// Shared display colours (guide illustrations).
export const STEP_BLUE = '#a8d8e8'; // pale blue step pads
export const STEP_NOTE = '#1ed8e8'; // bright cyan steps holding notes
export const SAND = '#ece0a8'; // velocity/gate value "fader"
export const PEACH = '#ffb48c'; // delay presets
export const CREAM = '#d2f0c8'; // reverb presets

export const PROJECT_COLORS = [
  [251, 53, 53], [250, 52, 116], [250, 130, 125], [250, 163, 52],
  [250, 195, 125], [250, 242, 52], [219, 250, 125], [161, 239, 26],
  [52, 250, 55], [75, 250, 134], [52, 175, 250], [52, 87, 250],
  [110, 52, 250], [250, 75, 206],
];
