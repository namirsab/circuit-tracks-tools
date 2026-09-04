// Scale engine. Hardware-verified behaviour: sequencer output =
// quantize(ncsNote, root=0, scaleType) + root - 12, rounding UP on ties.
import { SCALE_TYPES } from './constants.js';

// Nearest scale note (ties round up, never outside 0-127). root shifts the
// interval set: the hardware plays with root 0 and adds the root afterwards,
// while the song compiler quantises root-aware like song.py.
export function quantizeToScale(note, scaleType, root = 0) {
  const intervals = SCALE_TYPES[scaleType]?.intervals ?? SCALE_TYPES[15].intervals;
  if (intervals.length === 12) return note;
  const pc = ((note % 12) + 12) % 12;
  const base = note - pc + root;
  let best = null;
  let bestDist = Infinity;
  for (const iv of intervals) {
    for (const cand of [base + iv - 12, base + iv, base + iv + 12]) {
      if (cand < 0 || cand > 127) continue; // as the hardware path (song.py): never leave the MIDI range
      const dist = Math.abs(cand - note);
      if (dist < bestDist || (dist === bestDist && cand > best)) {
        best = cand;
        bestDist = dist;
      }
    }
  }
  return best;
}

// NCS note number -> sounding MIDI note (NCS values are +12 from MIDI).
export function ncsToMidi(ncsNote, scaleRoot, scaleType) {
  return quantizeToScale(ncsNote, scaleType) + scaleRoot - 12;
}

export function midiToNcs(midiNote, scaleRoot) {
  return Math.max(0, Math.min(127, midiNote - scaleRoot + 12));
}

// Note-view keyboard layout (user guide p.27-32).
//
// Non-chromatic scales: each row of 8 pads walks 8 consecutive scale degrees
// from the root; the upper row is one octave above the lower. The first and
// last pad of each row are "paler" on hardware. Expanded view stacks four
// such rows (each +12 from the one below).
//
// Chromatic: piano layout. Normal view = one octave on the two lower rows
// (white keys on the bottom row, black keys above); expanded = two octaves.
//
// Returns a 32-entry array indexed by pad (0 = top-left); each entry is
// null (unused pad) or { midi, pale }.
export function keyboardLayout(scaleRoot, scaleType, octave, expanded) {
  const intervals = SCALE_TYPES[scaleType]?.intervals ?? SCALE_TYPES[15].intervals;
  const rootMidi = 12 * octave + scaleRoot;
  const pads = new Array(32).fill(null);

  if (intervals.length === 12) {
    // Piano layout. blackMap[col] = semitone or null for the upper row.
    const whites = [0, 2, 4, 5, 7, 9, 11, 12];
    const blacks = [null, 1, 3, null, 6, 8, 10, null];
    const octaves = expanded ? 2 : 1;
    for (let o = 0; o < octaves; o++) {
      const base = rootMidi + o * 12;
      const whiteRow = o === 0 ? 24 : 8; // pad row start (bottom rows first)
      const blackRow = o === 0 ? 16 : 0;
      for (let col = 0; col < 8; col++) {
        pads[whiteRow + col] = { midi: base + whites[col], pale: col === 0 || col === 7 };
        if (blacks[col] != null) pads[blackRow + col] = { midi: base + blacks[col], pale: false };
      }
    }
    return pads;
  }

  const rowNotes = (base) => {
    const out = [];
    for (let i = 0; i < 8; i++) {
      const oct = Math.floor(i / intervals.length);
      out.push(base + oct * 12 + intervals[i % intervals.length]);
    }
    return out;
  };
  const rows = expanded ? 4 : 2;
  for (let r = 0; r < rows; r++) {
    const padRow = 24 - r * 8; // bottom row first, stacking upward
    const notes = rowNotes(rootMidi + r * 12);
    for (let col = 0; col < 8; col++) {
      pads[padRow + col] = { midi: notes[col], pale: col === 0 || col === 7 };
    }
  }
  return pads;
}
