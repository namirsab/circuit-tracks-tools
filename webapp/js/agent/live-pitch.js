// Streaming/causal note detection for the live "sing while the sequencer
// plays" workflow (Shift+track arm in app.js): a simplified, one-pass
// version of transcribe.js's segmentNotes() heuristics. segmentNotes can
// look ahead and retroactively split notes because it runs once over a
// whole recorded take; here a note has to be reported the instant it
// starts/ends so app.js can hand it straight to Sequencer.recordNote() on
// the step the transport is crossing right now. No scale-snapping here
// either — recordNote()/midiToNcs() store the raw semitone and the
// project's scaleRoot/scaleType reinterpret it at read time, exactly like
// a human playing pads live (see app.js liveNoteOn).
//
// Pure logic, no DOM/Web Audio — see mic.js for the getUserMedia tap that
// feeds this via push(). Unit-tested in Node (live-pitch.test.mjs).
import { yinPitch, DEFAULT_FMIN, DEFAULT_FMAX } from './transcribe.js';

function median(arr) {
  const s = [...arr].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

export class LivePitchTracker {
  constructor(sr, {
    fmin = DEFAULT_FMIN, fmax = DEFAULT_FMAX, hopS = 0.010, winS = 0.040, threshold = 0.15,
    silenceDb = -32.0, minConfidence = 0.55, pitchTolerance = 0.75,
    onsetDb = 6.0, onsetLag = 3, minNoteFrames = 4,
    onNoteOn = () => {}, onNoteOff = () => {},
  } = {}) {
    this.sr = sr;
    this.fmin = fmin;
    this.fmax = fmax;
    this.threshold = threshold;
    this.hop = Math.max(1, Math.round(sr * hopS));
    this.win = Math.max(64, Math.round(sr * winS));
    this.frameLen = this.win + Math.floor(sr / fmin);
    this.silenceDb = silenceDb;
    this.minConfidence = minConfidence;
    this.pitchTolerance = pitchTolerance;
    this.onsetDb = onsetDb;
    this.onsetLag = onsetLag;
    // Frames of sustained voicing required before a note is reported at
    // all (~minNoteFrames * hopS seconds) — this is what keeps a breath
    // puff or a single noisy frame from ever becoming a note, causally,
    // instead of segmentNotes' after-the-fact min_note_s discard.
    this.minNoteFrames = minNoteFrames;
    this.onNoteOn = onNoteOn;
    this.onNoteOff = onNoteOff;

    this.buffer = new Float32Array(0);
    // Adaptive noise floor: fast attack, slow decay peak-hold. analyze()
    // gates silence against the whole take's peak; streaming has no such
    // peak yet, so this tracks one instead.
    this.peakDb = -100;
    this.rmsHistory = []; // last onsetLag+1 rmsDb values (re-articulation check)
    this.pendingMidi = []; // candidate pitches while confirming an onset
    this.noteOpen = false;
    this.noteMidi = []; // pitches collected for the currently-open note
    this.framesSinceOpen = 0;
  }

  /** Feed newly captured mono samples at this.sr (Float32Array). */
  push(chunk) {
    const merged = new Float32Array(this.buffer.length + chunk.length);
    merged.set(this.buffer);
    merged.set(chunk, this.buffer.length);
    this.buffer = merged;
    while (this.buffer.length >= this.frameLen) {
      this._processFrame(this.buffer.subarray(0, this.frameLen));
      this.buffer = this.buffer.subarray(this.hop);
    }
  }

  /** Ends any open note immediately (e.g. on disarm), without waiting for silence. */
  flush() {
    if (this.noteOpen) this._closeNote();
    this.pendingMidi = [];
  }

  _processFrame(frame) {
    let sumSq = 0;
    for (let j = 0; j < this.win; j++) sumSq += frame[j] * frame[j];
    const rmsDb = 20 * Math.log10(Math.sqrt(sumSq / this.win) + 1e-9);
    this.peakDb = Math.max(rmsDb, this.peakDb - 0.05); // ~5 dB/s decay at a 10ms hop
    this.rmsHistory.push(rmsDb);
    if (this.rmsHistory.length > this.onsetLag + 1) this.rmsHistory.shift();

    const { f0, confidence } = yinPitch(frame, this.sr, this.fmin, this.fmax, this.threshold, this.win);
    const inRange = f0 >= this.fmin && f0 <= this.fmax;
    const voiced = rmsDb > this.peakDb + this.silenceDb && confidence >= this.minConfidence && inRange;

    if (!voiced) {
      if (this.noteOpen) this._closeNote();
      this.pendingMidi = [];
      return;
    }
    const midi = 69.0 + 12.0 * Math.log2(f0 / 440.0);

    // New attack on a (possibly) same pitch: level jumped up recently, e.g.
    // re-articulated "da da da" without an intervening silent gap. Gated on
    // framesSinceOpen so a note's own attack ramp (level rising for its
    // first ~20-30ms) can't look like a re-articulation of itself — mirrors
    // segmentNotes' `i - start >= max(minFrames, onsetLag)` guard.
    const settled = this.framesSinceOpen >= Math.max(this.minNoteFrames, this.onsetLag);
    const jumped = settled && this.rmsHistory.length > this.onsetLag
      && rmsDb - this.rmsHistory[this.rmsHistory.length - 1 - this.onsetLag] > this.onsetDb;

    if (this.noteOpen) {
      this.framesSinceOpen += 1;
      if (jumped || Math.abs(midi - median(this.noteMidi)) > this.pitchTolerance) {
        this._closeNote();
        this.pendingMidi = [midi];
        return;
      }
      this.noteMidi.push(midi);
      if (this.noteMidi.length > 20) this.noteMidi.shift(); // bound the drift-check window
      return;
    }

    this.pendingMidi.push(midi);
    if (this.pendingMidi.length >= this.minNoteFrames) {
      this.noteOpen = true;
      this.framesSinceOpen = 0;
      this.noteMidi = this.pendingMidi;
      this.pendingMidi = [];
      const startMidi = Math.round(median(this.noteMidi));
      const velocity = Math.max(40, Math.min(127, Math.round(127 + 3 * (rmsDb - this.peakDb))));
      this.onNoteOn(startMidi, velocity);
    }
  }

  _closeNote() {
    this.noteOpen = false;
    this.noteMidi = [];
    this.framesSinceOpen = 0;
    this.onNoteOff();
  }
}
