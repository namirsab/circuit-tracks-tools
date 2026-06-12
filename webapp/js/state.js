// Application state: the loaded project plus UI state.

export function emptySynthStep() {
  return {
    mask: 0,
    probability: 7,
    notes: Array.from({ length: 6 }, () => ({ note: 0, gate: 0, delay: 0, velocity: 96 })),
  };
}

export function emptyDrumStep() {
  return { active: false, velocity: 0, probability: 7, drumChoice: 0xff };
}

function defaultSettings() {
  return { playbackEnd: 15, playbackStart: 0, syncRate: 3, direction: 0 };
}

export function emptyPattern(kind) {
  return {
    kind,
    steps: Array.from({ length: 32 }, () => (kind === 'drum' ? emptyDrumStep() : emptySynthStep())),
    settings: defaultSettings(),
    paramLocks: {},
  };
}

export function defaultProject() {
  const patterns = [];
  for (let t = 0; t < 8; t++) {
    const kind = t >= 4 ? 'drum' : 'synth';
    patterns.push(Array.from({ length: 8 }, () => emptyPattern(kind)));
  }
  return {
    name: 'New Project',
    color: 8,
    tempo: 120,
    swing: 50,
    swingSyncRate: 3,
    scaleRoot: 0,
    scaleType: 0,
    delayPreset: 0,
    reverbPreset: 0,
    patterns,
    scenes: Array.from({ length: 16 }, () => ({
      trackChains: Array.from({ length: 8 }, () => ({ start: 0, end: 0 })),
    })),
    sceneChain: { start: 0, end: 0 },
    patternChains: Array.from({ length: 8 }, () => ({ start: 0, end: 0 })),
    synth1Patch: null,
    synth2Patch: null,
    drumConfigs: [
      { patchSelect: 0, level: 100, pitch: 64, decay: 127, distortion: 0, eq: 64, pan: 64, reverbSend: 0, delaySend: 0 },
      { patchSelect: 2, level: 100, pitch: 64, decay: 127, distortion: 0, eq: 64, pan: 64, reverbSend: 0, delaySend: 0 },
      { patchSelect: 4, level: 100, pitch: 64, decay: 127, distortion: 0, eq: 64, pan: 64, reverbSend: 0, delaySend: 0 },
      { patchSelect: 8, level: 100, pitch: 64, decay: 127, distortion: 0, eq: 64, pan: 64, reverbSend: 0, delaySend: 0 },
    ],
    fx: {
      reverbSends: [0, 0, 0, 0, 0, 0, 0, 0],
      reverbType: 2, reverbDecay: 64, reverbDamping: 64,
      delaySends: [0, 0, 0, 0, 0, 0, 0, 0],
      delayTime: 64, delaySync: 20, delayFeedback: 64, delayWidth: 127,
      delayLrRatio: 4, delaySlew: 5,
      fxBypass: false,
    },
    sidechain: Array.from({ length: 4 }, () => ({ preset: 0, source: 4, attack: 0, hold: 50, decay: 70, depth: 0 })),
    mixerLevels: [100, 100, 100, 100],
    mixerPans: [64, 64, 64, 64],
  };
}

export const VIEWS = [
  'note', 'velocity', 'gate', 'probability',
  'patternSettings', 'patterns', 'mixer', 'fx', 'scales', 'preset', 'tempo',
];

export class UIState {
  constructor() {
    this.currentTrack = 0;
    this.view = 'note';
    this.shift = false;
    this.recording = false;
    this.clearHeld = false;
    this.duplicateHeld = false;
    this.copySource = null; // {trackId, patIdx} while Duplicate is held
    this.stepPage = 0; // 0 = steps 1-16, 1 = steps 17-32
    this.octave = [4, 4, 4, 4]; // per synth/MIDI track
    this.samplePage = [0, 0, 0, 0]; // per drum track (note view, pages of 16)
    this.presetPage = 0; // preset view page (pages of 32)
    this.patternPage = 0; // patterns view: 0 = patterns 1-4, 1 = 5-8
    this.projectPage = 0; // projects view: pages of 32 pack projects
    this.currentProjectIdx = null; // pack project slot currently loaded
    this.patchIndex = [null, null]; // selected bank patch per synth track
    this.midiTemplate = [0, 0]; // selected template per MIDI track
    this.currentPattern = [0, 0, 0, 0, 0, 0, 0, 0];
    this.mutes = [false, false, false, false, false, false, false, false];
    this.selectedStep = [0, 0, 0, 0, 0, 0, 0, 0]; // value-display cursor per track
    this.heldStep = null; // step pad currently held down
    this.selectedNoteSlots = null; // micro-step view note selection (null = all)
    this.recQuantise = true; // Shift+Record toggles (guide p.64)
    this.noteExpanded = false; // Expanded Note / Expanded Drum view
    this.fixedVelocity = false; // Shift+Velocity
    this.mixerPanMode = false; // mixer view macros: levels (false) or pans
    this.fxMacroMode = 'reverb'; // FX view macros control reverb or delay sends
    this.scPage = 0; // sidechain view page: 0 = S1/S2, 1 = M1/M2
    this.scFocus = 0; // sidechain track whose source row is shown
    this.keyOverlay = false;
  }
}
