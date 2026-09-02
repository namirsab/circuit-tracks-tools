// MCP tool descriptors for Web Tracks. Names and argument shapes mirror the
// circuit-tracks-mcp hardware server wherever the concept exists here, so
// prompts written for the hardware work unchanged; MIDI-plumbing tools
// (connect, send_cc, clock, file paths) have no browser equivalent and are
// left out. Web-only extras (list_patches, download_project, undo, ...) are
// marked as such in their descriptions.
import { TRACK_NAMES, PROJECT_PARAM_HELP } from './api.js';

const int = (minimum, maximum, description) => ({ type: 'integer', minimum, maximum, ...(description ? { description } : {}) });
const num = (minimum, maximum, description) => ({ type: 'number', minimum, maximum, ...(description ? { description } : {}) });
const str = (description, extra = {}) => ({ type: 'string', description, ...extra });
const bool = (description, def) => ({ type: 'boolean', description, ...(def !== undefined ? { default: def } : {}) });
const obj = (properties, required = [], description) => ({
  type: 'object', properties, required, additionalProperties: false, ...(description ? { description } : {}),
});
const params127 = (description) => ({
  type: 'object', description, minProperties: 1,
  additionalProperties: { type: 'integer', minimum: 0, maximum: 127 },
});

const TRACK = { type: 'string', enum: TRACK_NAMES, description: 'Track name.' };
const SYNTH = int(1, 2, 'Synth number: 1 or 2.');
const DRUM = int(1, 4, 'Drum track: 1-4.');
const READ_ONLY = { readOnlyHint: true };

const REFERENCE_SECTIONS = ['synth', 'patch', 'drums', 'project', 'lookup_tables', 'mod_matrix', 'macros', 'song_format', 'best_practices'];

const WEB_TRACKS_NOTES = [
  'You are controlling Web Tracks, a browser emulation of the Circuit Tracks. The sequencer, synths, drums and FX run in the page; the user sees every change on the pads, knobs and LCD.',
  'No MIDI connection is needed: skip connect/list_midi_ports. Changes apply instantly. load_song loads a song into the project; start_sequencer plays it.',
  'Drum sample selection works here (set_drum_params {"sample": n} or sounds.drumN.sample in load_song); the hardware CC bug does not apply.',
  'Browsers block sound until the page has been clicked once. If a play tool reports locked audio, ask the user to click the page and retry.',
  'Project slots (select_project / export_song_to_project) are the in-app project bank; download_project hands the user a hardware-ready .ncs file.',
];

// Tool schemas that embed parts of the song schema keep its $defs at the
// root so "#/$defs/..." references resolve. Without the schema (fetch failed,
// tests) the compiler's own checks still produce path-qualified errors.
function songSchemaParts(songSchema) {
  if (!songSchema) return { defs: null, song: { type: 'object', description: 'Song in the load_song format (see get_parameter_reference("song_format")).' }, ref: () => ({ type: 'object' }) };
  const { $defs: defs = {}, ...song } = songSchema;
  return { defs, song, ref: (name) => (name in defs ? { $ref: `#/$defs/${name}` } : { type: 'object' }) };
}

export function createTools(api, { loadJson = defaultLoadJson, songSchema = null } = {}) {
  let referencePromise = null;
  const reference = () => (referencePromise ??= loadJson('data/parameter-reference.json'));
  const parts = songSchemaParts(songSchema);
  const withDefs = (schema) => (parts.defs ? { ...schema, $defs: parts.defs } : schema);
  const trackUnion = { anyOf: [parts.ref('SynthTrackConfig'), parts.ref('DrumTrackConfig')] };
  const stepUnion = { anyOf: [parts.ref('SynthStepConfig'), parts.ref('DrumStepConfig')] };
  const namesList = { type: 'array', items: { type: 'string' }, minItems: 1, maxItems: 16, description: 'Pattern names from load_song / set_pattern, in playback order.' };

  // Wrap a mutating tool so its previous project state can be undone.
  const undoable = (label, fn) => async (args) => {
    await api.snapshot(label);
    return fn(args);
  };

  return [
    {
      name: 'get_parameter_reference',
      description: 'Reference for synth, drum, project and patch parameters, lookup tables, mod matrix, macros and the song format. Call with no section for the section list and best practices; call with a section before using complex tools so parameter names are exact. Sections: synth, patch, drums, project, lookup_tables, mod_matrix, macros, song_format, best_practices.',
      inputSchema: obj({ section: str('Section name, or empty for the overview.', { enum: ['', ...REFERENCE_SECTIONS], default: '' }) }),
      annotations: READ_ONLY,
      async execute({ section = '' }) {
        const ref = await reference();
        const data = ref[section];
        if (!data) throw new Error(`Unknown section "${section}". Sections: ${REFERENCE_SECTIONS.join(', ')}`);
        if (section === '' || section === 'best_practices') return { ...data, web_tracks: WEB_TRACKS_NOTES };
        return data;
      },
    },
    {
      name: 'load_song',
      description: 'Load a complete song (patterns, sounds, FX, mixer, song order) into the project, replacing it. The song format is the hardware server\'s: see get_parameter_reference("song_format"). CRITICAL: all patterns share one length (16 or 32); include "sounds" for both synths; synth p-locks use the step/track "macros" key, drum automation the track "params" key; mod matrix names are space separated ("filter frequency"); gate max 16. Pattern names map to slots 1-8 and the "song" list to scenes. Then start_sequencer to listen and export_song_to_project to save.',
      inputSchema: withDefs(obj({ song: parts.song }, ['song'])),
      execute: undoable('load_song', ({ song }) => api.loadSong(song)),
    },
    {
      name: 'read_project',
      description: 'Read the live project back in the load_song song format (patterns by name where known, sounds, fx, mixer, scale, song order): what the user played or edited on the pads, ready to modify and load_song again.',
      inputSchema: obj({}),
      annotations: READ_ONLY,
      execute: () => api.readProject(),
    },
    {
      name: 'set_pattern',
      description: 'Define or replace one named pattern (a slot 1-8 on every track) with song-format track configs: {"synth1": {"steps": {"0": {"note": 60, "gate": 1}}}, "drum1": {"steps": {"0": {}, "4": {}}}}. Tracks not given are silent in this pattern. Length must match the other patterns (all 16 or all 32). When the sequencer is stopped the new pattern is selected on all tracks.',
      inputSchema: withDefs(obj({
        name: str('Pattern name, e.g. "intro".', { minLength: 1, maxLength: 32 }),
        tracks: { type: 'object', minProperties: 1, propertyNames: { enum: TRACK_NAMES }, additionalProperties: trackUnion, description: 'track name -> track config.' },
        length: int(1, 32, 'Pattern length in steps (default 16).'),
      }, ['name', 'tracks'])),
      execute: undoable('set_pattern', ({ name, tracks, length = 16 }) => api.setPattern(name, tracks, length)),
    },
    {
      name: 'set_track',
      description: 'Replace (default) or merge the steps of one track inside a named pattern while everything else keeps playing. steps: {"0": {"note": 48, "velocity": 110}, "8": {"notes": [48, 55]}} for synths, {"0": {}, "4": {"velocity": 80}} for drums. Track-level automation lanes are kept.',
      inputSchema: withDefs(obj({
        pattern_name: str('Pattern name.'),
        track: TRACK,
        steps: { type: 'object', additionalProperties: stepUnion, description: 'step index (as string) -> step config; absent steps are rests.' },
        clear_existing: bool('true (default) replaces all steps of the track; false merges into them.', true),
      }, ['pattern_name', 'track', 'steps'])),
      execute: undoable('set_track', ({ pattern_name, track, steps, clear_existing = true }) => api.setTrack(pattern_name, track, steps, clear_existing)),
    },
    {
      name: 'get_pattern',
      description: 'Read one named pattern in song format (length and per-track steps / automation).',
      inputSchema: obj({ name: str('Pattern name.') }, ['name']),
      annotations: READ_ONLY,
      execute: ({ name }) => api.getPattern(name),
    },
    {
      name: 'list_patterns',
      description: 'Named patterns with their slots, unnamed slots that hold data, the current song order and the pattern length in use.',
      inputSchema: obj({}),
      annotations: READ_ONLY,
      execute: () => api.listPatterns(),
    },
    {
      name: 'clear_pattern',
      description: 'Clear every track of a named pattern (the name keeps its slot).',
      inputSchema: obj({ name: str('Pattern name.') }, ['name']),
      execute: undoable('clear_pattern', ({ name }) => api.clearNamedPattern(name)),
    },
    {
      name: 'set_song',
      description: 'Set the song order: a list of pattern names becomes scenes 1..n and a scene chain that loops as a whole (like the hardware). While playing it takes over at the end of the current Drum 1 pattern. Example: ["intro", "verse", "verse", "chorus"].',
      inputSchema: obj({ patterns: namesList }, ['patterns']),
      execute: ({ patterns }) => api.setSongOrder(patterns),
    },
    {
      name: 'queue_patterns',
      description: 'Append pattern names to the song order (see set_song).',
      inputSchema: obj({ patterns: namesList }, ['patterns']),
      execute: ({ patterns }) => api.setSongOrder(patterns, { append: true }),
    },
    {
      name: 'clear_queue',
      description: 'Drop the song order; the current pattern selection loops.',
      inputSchema: obj({}),
      execute: () => api.clearQueue(),
    },
    {
      name: 'get_sequencer_status',
      description: 'Current state: playing, BPM, swing, audio unlock state, project name and slot, per-track pattern/step/mute/chain, scene chain, loaded synth patches, drum samples, and the pattern names known from load_song.',
      inputSchema: obj({}),
      annotations: READ_ONLY,
      execute: () => api.status(),
    },
    {
      name: 'start_sequencer',
      description: 'Start playback. Optionally select a named pattern (from load_song) on every track first and/or set the tempo. Loops the current pattern selection or scene chain until stop_sequencer.',
      inputSchema: obj({
        pattern: str('Pattern name from load_song to select on all tracks before starting. Omit to play the current selection.'),
        bpm: num(40, 240, 'Tempo in BPM.'),
      }),
      async execute({ pattern, bpm }) {
        if (pattern) api.selectNamedPattern(pattern);
        if (bpm !== undefined) api.setBpm(bpm);
        const msg = await api.play();
        return `${msg} at ${api.seq.bpm} BPM${pattern ? ` on pattern "${pattern}"` : ''}`;
      },
    },
    {
      name: 'stop_sequencer',
      description: 'Stop playback (all notes off).',
      inputSchema: obj({}),
      execute: () => api.stop(),
    },
    {
      name: 'transport',
      description: 'Transport control: "start" plays from the top, "continue" resumes from where playback last stopped, "stop" stops. Optional bpm sets the tempo first.',
      inputSchema: obj({
        action: str('One of start, stop, continue.', { enum: ['start', 'stop', 'continue'] }),
        bpm: num(40, 240, 'Tempo in BPM.'),
      }, ['action']),
      async execute({ action, bpm }) {
        if (bpm !== undefined) api.setBpm(bpm);
        if (action === 'stop') return api.stop();
        return api.play({ resume: action === 'continue' });
      },
    },
    {
      name: 'set_bpm',
      description: 'Set the tempo (40-240 BPM). Works while playing.',
      inputSchema: obj({ bpm: num(40, 240, 'Tempo in BPM.') }, ['bpm']),
      execute: ({ bpm }) => `Tempo ${api.setBpm(bpm)} BPM`,
    },
    {
      name: 'set_swing',
      description: 'Set swing (20-80, 50 = straight). Web Tracks extra; on hardware swing is part of the song.',
      inputSchema: obj({ swing: int(20, 80, 'Swing amount, 50 = none.') }, ['swing']),
      execute: ({ swing }) => `Swing ${api.setSwing(swing)}`,
    },
    {
      name: 'mute_track',
      description: 'Mute or unmute a track. Takes effect immediately.',
      inputSchema: obj({ track: TRACK, muted: bool('true to mute, false to unmute.', true) }, ['track']),
      execute: ({ track, muted = true }) => api.mute(track, muted),
    },
    {
      name: 'select_pattern',
      description: 'Select which of the 8 pattern slots a track plays (Web Tracks extra). While playing the switch is queued for the end of the current pattern unless immediate is true.',
      inputSchema: obj({
        track: TRACK,
        pattern: int(1, 8, 'Pattern slot 1-8.'),
        immediate: bool('Switch now instead of at the pattern end.', false),
      }, ['track', 'pattern']),
      execute: ({ track, pattern, immediate = false }) => api.selectPattern(track, pattern, { immediate }),
    },
    {
      name: 'set_synth_params',
      description: 'Set one or more parameters of the live patch on Synth 1 or 2 (also affects sounding notes). Keys are snake_case patch parameter names from get_parameter_reference("synth") or ("patch"), e.g. {"filter_frequency": 80, "osc1_wave": 2}. Also accepts macro_knob1-8 (knob positions) and "name" (patch name, string).',
      inputSchema: obj({
        synth: SYNTH,
        params: {
          type: 'object', minProperties: 1, description: 'param_name -> value 0-127 ("name" -> string).',
          additionalProperties: { anyOf: [{ type: 'integer', minimum: 0, maximum: 127 }, { type: 'string', maxLength: 16 }] },
        },
      }, ['synth', 'params']),
      execute: ({ synth, params }) => ({ synth, applied: api.setSynthParams(synth, params) }),
    },
    {
      name: 'edit_synth_patch',
      description: 'Edit the current patch on Synth 1 or 2: same as set_synth_params (all 340-byte patch parameters incl. mod matrix slots modN_source1/source2/depth/destination and "name"). Kept for parity with the hardware server.',
      inputSchema: obj({
        synth: SYNTH,
        params: {
          type: 'object', minProperties: 1, description: 'param_name -> value 0-127 ("name" -> string).',
          additionalProperties: { anyOf: [{ type: 'integer', minimum: 0, maximum: 127 }, { type: 'string', maxLength: 16 }] },
        },
      }, ['synth', 'params']),
      execute: ({ synth, params }) => ({ synth, applied: api.setSynthParams(synth, params), patch: api.getSynthPatch(synth).name }),
    },
    {
      name: 'create_synth_patch',
      description: 'Build a synth patch from scratch (or from a preset: pad, bass, lead, pluck) and put it on Synth 1 or 2. Call get_parameter_reference("patch"), then ("mod_matrix"), then ("macros") first. params are snake_case patch parameters 0-127; mod_matrix entries use SPACE-separated names ({"source1": "LFO 1+/-", "dest": "filter frequency", "depth": 30}, depth -64..63); macros are keyed "1"-"8" with targets [{"dest": "filter frequency", "start": 0, "end": 127, "depth": 63}]. Macros ADD to base values: set base values low for params a macro should sweep up.',
      inputSchema: withDefs(obj({
        synth: SYNTH,
        name: str('Patch name (up to 16 chars).', { maxLength: 16 }),
        params: { type: 'object', additionalProperties: { type: 'integer', minimum: 0, maximum: 127 }, description: 'param_name -> value.' },
        mod_matrix: { type: 'array', items: parts.ref('ModMatrixEntry'), maxItems: 20 },
        macros: { type: 'object', propertyNames: { enum: ['1', '2', '3', '4', '5', '6', '7', '8'] }, additionalProperties: parts.ref('MacroConfig') },
        preset: str('Base preset.', { enum: ['pad', 'bass', 'lead', 'pluck'] }),
      }, ['synth', 'name'])),
      execute: undoable('create_synth_patch', ({ synth, ...cfg }) => api.createSynthPatch(synth, cfg)),
    },
    {
      name: 'save_synth_patch',
      description: 'Store the live patch of Synth 1 or 2 into a slot of the pack\'s patch bank (0-127) so select_patch and the Preset view can recall it; included in Export pack.',
      inputSchema: obj({ synth: SYNTH, slot: int(0, 127, 'Bank slot.') }, ['synth', 'slot']),
      execute: ({ synth, slot }) => api.savePatchToBank(synth, slot),
    },
    {
      name: 'get_synth_patch',
      description: 'Read the live patch of Synth 1 or 2: name, all parameter values, active mod matrix slots (named sources/destinations, signed depth) and macro assignments.',
      inputSchema: obj({ synth: SYNTH }, ['synth']),
      annotations: READ_ONLY,
      execute: ({ synth }) => api.getSynthPatch(synth),
    },
    {
      name: 'set_drum_params',
      description: 'Set drum track parameters: level, pitch, decay, distortion, eq, pan (0-127), sample (0-63, the drum sample slot), reverb_send, delay_send. E.g. {"pitch": 80, "sample": 2}.',
      inputSchema: obj({ drum: DRUM, params: params127('param_name -> value.') }, ['drum', 'params']),
      execute: ({ drum, params }) => ({ drum, applied: api.setDrumParams(drum, params) }),
    },
    {
      name: 'set_project_params',
      description: `Set project-level parameters: FX sends, mixer, master filter, reverb, delay, FX bypass, sidechain. Names:\n- ${PROJECT_PARAM_HELP.join('\n- ')}\nE.g. {"reverb_synth1_send": 60, "delay_feedback": 80, "sidechain_synth1_preset": 3, "sidechain_synth1_source": 0}.`,
      inputSchema: obj({ params: params127('param_name -> value.') }, ['params']),
      execute: ({ params }) => api.setProjectParams(params),
    },
    {
      name: 'set_macro',
      description: 'Turn a macro knob (1-8) on Synth 1 or 2 to a position 0-127. Macros ADD to the patch base values through the assignments shown by get_macros.',
      inputSchema: obj({ synth: SYNTH, macro: int(1, 8, 'Macro knob 1-8.'), value: int(0, 127, 'Knob position.') }, ['synth', 'macro', 'value']),
      execute: ({ synth, macro, value }) => api.setMacro(synth, macro, value),
    },
    {
      name: 'get_macros',
      description: 'Macro knob layout for both synths: current knob positions and, per macro, the parameters it moves with their start/end knob range and signed depth.',
      inputSchema: obj({}),
      annotations: READ_ONLY,
      execute: () => api.getMacros(),
    },
    {
      name: 'play_notes',
      description: 'Audition notes right now (independent of the sequencer). Pass a track name, or a channel like the hardware: 0 = synth1, 1 = synth2, 2 = midi1, 3 = midi2, 9 = drums (notes 60, 62, 64, 65 hit drums 1-4). Middle C = 60.',
      inputSchema: obj({
        notes: { type: 'array', items: int(0, 127), minItems: 1, maxItems: 16, description: 'MIDI note numbers; several = chord.' },
        track: { ...TRACK, description: 'Track name (preferred).' },
        channel: int(0, 15, 'Hardware-style channel if no track is given.'),
        velocity: int(1, 127, 'Velocity (default 100).'),
        duration_ms: int(20, 20000, 'Hold time in ms (default 500).'),
      }, ['notes']),
      execute: (args) => api.playNotes(args),
    },
    {
      name: 'play_drum',
      description: 'Trigger one drum hit right now.',
      inputSchema: obj({ drum: DRUM, velocity: int(1, 127, 'Velocity (default 100).') }, ['drum']),
      execute: ({ drum, velocity = 100 }) => api.playDrum(drum, velocity),
    },
    {
      name: 'list_drum_samples',
      description: 'List the 64 drum samples of the loaded pack with their index numbers and names (4 pages of 16). Use the index with set_drum_params {"sample": n} or sounds.drumN.sample.',
      inputSchema: obj({ page: int(1, 4, 'Only this page (1-4); omit for all.') }),
      annotations: READ_ONLY,
      execute: ({ page }) => api.listDrumSamples(page ?? null),
    },
    {
      name: 'list_patches',
      description: 'List the synth patches of the loaded pack with their index numbers (Web Tracks extra). Use the index with select_patch.',
      inputSchema: obj({}),
      annotations: READ_ONLY,
      execute: () => api.listPatches(),
    },
    {
      name: 'select_patch',
      description: 'Load a patch from the pack\'s patch bank onto Synth 1 or 2 by index (see list_patches).',
      inputSchema: obj({ synth: SYNTH, patch_number: int(0, 127, 'Patch index in the bank.') }, ['synth', 'patch_number']),
      execute: undoable('select_patch', ({ synth, patch_number }) => api.selectPatch(synth, patch_number)),
    },
    {
      name: 'list_projects',
      description: 'List the project bank slots that hold a project (Web Tracks extra).',
      inputSchema: obj({}),
      annotations: READ_ONLY,
      execute: () => api.listProjects(),
    },
    {
      name: 'select_project',
      description: 'Load a project from the bank by slot (0-63), replacing the live project. queued = true switches at the end of the current pattern while playing. An empty slot loads an init project.',
      inputSchema: obj({ project_number: int(0, 63, 'Project slot 0-63.'), queued: bool('Switch at the pattern end while playing.', false) }, ['project_number']),
      execute: undoable('select_project', ({ project_number, queued = false }) => api.selectProject(project_number, { queued })),
    },
    {
      name: 'export_song_to_project',
      description: 'Save the live project into a bank slot (0-63), like pressing Save on the hardware. Defaults to the current slot, else the first empty one. Optional name renames the project (16 chars fit the hardware display). Use download_project to get the .ncs file.',
      inputSchema: obj({ slot: int(-1, 63, 'Target slot; -1 = current/first empty.'), name: str('Project name.', { maxLength: 32 }) }),
      execute: ({ slot = -1, name = '' }) => api.saveToSlot(slot, name),
    },
    {
      name: 'download_project',
      description: 'Download the live project as a hardware-ready .ncs file in the user\'s browser (Web Tracks extra; the file can be sent to a Circuit Tracks with Components or the hardware MCP server).',
      inputSchema: obj({}),
      execute: () => api.downloadProject(),
    },
    {
      name: 'undo',
      description: 'Restore the project state from before the last project-changing agent call (load_song, select_project, select_patch, set_pattern...). Repeat to go further back (up to 12 states).',
      inputSchema: obj({}),
      execute: () => api.undo(),
    },
  ];
}

async function defaultLoadJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load ${url}: ${res.status}`);
  return res.json();
}
