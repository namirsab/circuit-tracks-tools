"""Song format: a single JSON structure describing an entire Circuit Tracks song.

Supports loading into the live sequencer for preview, and exporting to an NCS
project file for transfer to the hardware.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from circuit_mcp.constants import (
    DELAY_PRESET_BY_NAME,
    DELAY_PRESETS,
    DRUM_CC,
    DRUMS_CHANNEL,
    PROJECT_CC,
    PROJECT_CHANNEL,
    PROJECT_NRPN,
    REVERB_PRESET_BY_NAME,
    REVERB_PRESETS,
    SYNTH_CC,
)
from circuit_mcp.midi import MidiConnection
from circuit_mcp.ncs_parser import (
    DEFAULT_DRUM_CHOICE,
    DEFAULT_NOTE_VELOCITY,
    DEFAULT_PROBABILITY,
    NOTES_PER_STEP,
    NUM_TRACKS,
    PATTERNS_PER_TRACK,
    STEPS_PER_PATTERN,
    DrumPattern,
    DrumStep,
    FXSettings,
    NCSFile,
    NCSNote,
    NCSProjectSettings,
    PatternSettings,
    SidechainSettings,
    SynthPattern,
    SynthStep,
    get_drum_pattern,
    get_synth_pattern,
    parse_ncs,
    serialize_ncs,
    set_scene,
    set_scene_chain,
)
from circuit_mcp.ncs_transfer import send_ncs_project
from circuit_mcp.patch import _PARAM_OFFSETS, send_current_patch
from circuit_mcp.patch_builder import (
    PatchBuilder,
    preset_bass,
    preset_lead,
    preset_pad,
    preset_pluck,
)
from circuit_mcp.sequencer import (
    VALID_TRACK_NAMES,
    Pattern,
    SequencerEngine,
    Step,
    TrackType,
)

# Template file for NCS export (bundled with the package)
_TEMPLATE_NCS = Path(__file__).parent / "data" / "Empty.ncs"

_PRESETS = {"pad": preset_pad, "bass": preset_bass, "lead": preset_lead, "pluck": preset_pluck}

# Friendly name -> NCS send array index
_SEND_INDEX = {
    "synth1": 0, "synth2": 1,
    "drum1": 2, "drum2": 3, "drum3": 4, "drum4": 5,
    "midi1": 6, "midi2": 7,
}

# NCS track ordering: S1, S2, M1, M2, D1, D2, D3, D4
_NCS_TRACK_ORDER = ["synth1", "synth2", "midi1", "midi2", "drum1", "drum2", "drum3", "drum4"]

# Sidechain source name -> NCS integer
_SC_SOURCE = {"drum1": 0, "drum2": 1, "drum3": 2, "drum4": 3, "off": 4}

# Scale root name -> integer
_SCALE_ROOT = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
    "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8,
    "A": 9, "A#": 10, "Bb": 10, "B": 11,
}

# Scale type name -> integer
_SCALE_TYPE = {
    "natural minor": 0, "minor": 0, "major": 1, "dorian": 2, "mixolydian": 3,
    "phrygian": 4, "harmonic minor": 5, "blues": 6, "minor pentatonic": 7,
    "hungarian minor": 8, "ukranian dorian": 9, "marva": 10,
    "todi": 11, "whole tone": 12, "hirajoshi": 13, "chromatic": 14,
}


# --- Data structures ---


@dataclass
class SoundConfig:
    """Sound configuration for a single track."""

    # Synth-specific
    preset: str | None = None
    name: str | None = None
    params: dict[str, int] | None = None
    mod_matrix: list[dict] | None = None
    macros: dict[str, dict] | None = None
    # Drum-specific
    sample: int | None = None
    level: int | None = None
    pitch: int | None = None
    decay: int | None = None
    distortion: int | None = None
    eq: int | None = None
    pan: int | None = None


@dataclass
class FXConfig:
    """FX configuration."""

    reverb: dict[str, int] = field(default_factory=dict)
    delay: dict[str, int] = field(default_factory=dict)
    reverb_sends: dict[str, int] = field(default_factory=dict)
    delay_sends: dict[str, int] = field(default_factory=dict)
    sidechain: dict[str, dict] = field(default_factory=dict)
    reverb_preset: str | int | None = None  # preset name or index (0-7)
    delay_preset: str | int | None = None   # preset name or index (0-15)


@dataclass
class MixerConfig:
    """Mixer configuration for a single track."""

    level: int = 100
    pan: int = 64


@dataclass
class PatternData:
    """A single pattern's data."""

    length: int = 16
    tracks: dict[str, dict] = field(default_factory=dict)


@dataclass
class SongData:
    """Complete song description."""

    name: str = "Song"
    bpm: int = 120
    swing: int = 50
    color: int = 8
    scale_root: str = "C"
    scale_type: str = "chromatic"
    sounds: dict[str, SoundConfig] = field(default_factory=dict)
    fx: FXConfig = field(default_factory=FXConfig)
    mixer: dict[str, MixerConfig] = field(default_factory=dict)
    patterns: dict[str, PatternData] = field(default_factory=dict)
    song: list[str] = field(default_factory=list)


# --- Parsing & Validation ---


def parse_song(d: dict) -> SongData:
    """Parse and validate a song JSON dict into a SongData object.

    Raises ValueError on invalid input.
    """
    song = SongData()

    # Top-level fields
    song.name = str(d.get("name", "Song"))
    song.bpm = int(d.get("bpm", 120))
    if not 40 <= song.bpm <= 240:
        raise ValueError(f"BPM must be 40-240, got {song.bpm}")
    song.swing = int(d.get("swing", 50))
    song.color = int(d.get("color", 8))

    # Scale
    scale = d.get("scale", {})
    song.scale_root = str(scale.get("root", "C"))
    song.scale_type = str(scale.get("type", "chromatic"))
    if song.scale_root not in _SCALE_ROOT:
        raise ValueError(f"Unknown scale root: {song.scale_root!r}. Valid: {list(_SCALE_ROOT.keys())}")
    if song.scale_type.lower() not in _SCALE_TYPE:
        raise ValueError(f"Unknown scale type: {song.scale_type!r}. Valid: {list(_SCALE_TYPE.keys())}")

    # Sounds
    for track_name, sound_data in d.get("sounds", {}).items():
        if track_name not in VALID_TRACK_NAMES:
            raise ValueError(f"Invalid sound track '{track_name}'. Must be one of: {sorted(VALID_TRACK_NAMES)}")
        sc = SoundConfig()
        if isinstance(sound_data, dict):
            sc.preset = sound_data.get("preset")
            sc.name = sound_data.get("name")
            sc.params = sound_data.get("params")
            sc.mod_matrix = sound_data.get("mod_matrix")
            sc.macros = sound_data.get("macros")
            sc.sample = sound_data.get("sample")
            sc.level = sound_data.get("level")
            sc.pitch = sound_data.get("pitch")
            sc.decay = sound_data.get("decay")
            sc.distortion = sound_data.get("distortion")
            sc.eq = sound_data.get("eq")
            sc.pan = sound_data.get("pan")
        song.sounds[track_name] = sc

    # FX
    fx_data = d.get("fx", {})
    reverb_preset = fx_data.get("reverb_preset")
    delay_preset = fx_data.get("delay_preset")
    # Validate preset values
    if reverb_preset is not None:
        if isinstance(reverb_preset, str):
            if reverb_preset.lower() not in REVERB_PRESET_BY_NAME:
                raise ValueError(
                    f"Unknown reverb preset: {reverb_preset!r}. "
                    f"Valid: {list(REVERB_PRESET_BY_NAME.keys())} or 0-7"
                )
        elif isinstance(reverb_preset, int):
            if not 0 <= reverb_preset <= 7:
                raise ValueError(f"Reverb preset index must be 0-7, got {reverb_preset}")
    if delay_preset is not None:
        if isinstance(delay_preset, str):
            if delay_preset.lower() not in DELAY_PRESET_BY_NAME:
                raise ValueError(
                    f"Unknown delay preset: {delay_preset!r}. "
                    f"Valid: {list(DELAY_PRESET_BY_NAME.keys())} or 0-15"
                )
        elif isinstance(delay_preset, int):
            if not 0 <= delay_preset <= 15:
                raise ValueError(f"Delay preset index must be 0-15, got {delay_preset}")
    song.fx = FXConfig(
        reverb=fx_data.get("reverb", {}),
        delay=fx_data.get("delay", {}),
        reverb_sends=fx_data.get("reverb_sends", {}),
        delay_sends=fx_data.get("delay_sends", {}),
        sidechain=fx_data.get("sidechain", {}),
        reverb_preset=reverb_preset,
        delay_preset=delay_preset,
    )
    # Validate send track names
    for name in list(song.fx.reverb_sends) + list(song.fx.delay_sends):
        if name not in _SEND_INDEX:
            raise ValueError(f"Invalid send track '{name}'. Valid: {list(_SEND_INDEX.keys())}")
    for name in song.fx.sidechain:
        if name not in ("synth1", "synth2"):
            raise ValueError(f"Sidechain only applies to synth1/synth2, got '{name}'")

    # Mixer
    for track_name, mix_data in d.get("mixer", {}).items():
        if track_name not in ("synth1", "synth2"):
            raise ValueError(f"Mixer only supports synth1/synth2, got '{track_name}'")
        song.mixer[track_name] = MixerConfig(
            level=int(mix_data.get("level", 100)),
            pan=int(mix_data.get("pan", 64)),
        )

    # Patterns (required)
    patterns_data = d.get("patterns", {})
    if not patterns_data:
        raise ValueError("Song must have at least one pattern")
    for pat_name, pat_data in patterns_data.items():
        length = int(pat_data.get("length", 16))
        tracks = pat_data.get("tracks", {})
        for track_name in tracks:
            if track_name not in VALID_TRACK_NAMES:
                raise ValueError(
                    f"Invalid track '{track_name}' in pattern '{pat_name}'. "
                    f"Must be one of: {sorted(VALID_TRACK_NAMES)}"
                )
            # Validate step indices
            steps = tracks[track_name].get("steps", {})
            for idx_str in steps:
                idx = int(idx_str)
                if idx < 0 or idx >= length:
                    raise ValueError(
                        f"Step index {idx} out of range for pattern '{pat_name}' (length={length})"
                    )
        song.patterns[pat_name] = PatternData(length=length, tracks=tracks)

    # Song structure
    song.song = list(d.get("song", []))
    for pat_name in song.song:
        if pat_name not in song.patterns:
            raise ValueError(f"Song references unknown pattern '{pat_name}'")

    # Hardware limits
    unique_patterns = set(song.song) if song.song else set(song.patterns.keys())
    if len(unique_patterns) > PATTERNS_PER_TRACK:
        raise ValueError(
            f"Too many unique patterns ({len(unique_patterns)}). "
            f"Circuit Tracks supports max {PATTERNS_PER_TRACK}."
        )
    if len(song.song) > 16:
        raise ValueError(f"Song has {len(song.song)} sections, max 16 (scenes).")

    return song


# --- Load into sequencer ---


def load_song_to_sequencer(
    song: SongData,
    engine: SequencerEngine,
    midi: MidiConnection,
) -> dict:
    """Load a song into the live sequencer and configure sounds/FX via MIDI.

    Returns a summary dict.
    """
    results = {"patterns_loaded": 0, "sounds_configured": []}

    # 1. Load patterns into sequencer
    for pat_name, pat_data in song.patterns.items():
        pattern = Pattern(length=pat_data.length)
        for track_name, track_data in pat_data.tracks.items():
            steps_raw = track_data.get("steps", {})
            steps = {}
            for idx_str, step_data in steps_raw.items():
                steps[int(idx_str)] = Step.from_dict(step_data)
            track = pattern.tracks[track_name]
            track.steps = steps
        engine.set_pattern(pat_name, pattern)
        results["patterns_loaded"] += 1

    # 2. Set BPM
    engine._bpm = song.bpm

    # 3. Configure synth sounds
    for synth_name in ("synth1", "synth2"):
        sc = song.sounds.get(synth_name)
        if not sc:
            continue
        synth_num = 1 if synth_name == "synth1" else 2
        patch_bytes = list(_build_patch_bytes(sc))
        send_current_patch(midi, synth_num, patch_bytes)
        results["sounds_configured"].append(synth_name)

    # 4. Select drum samples (best effort via CC)
    for drum_name in ("drum1", "drum2", "drum3", "drum4"):
        sc = song.sounds.get(drum_name)
        if not sc or sc.sample is None:
            continue
        drum_num = int(drum_name[-1])
        cc = DRUM_CC[drum_num]["patch_select"]
        midi.control_change(DRUMS_CHANNEL, cc, sc.sample)
        results["sounds_configured"].append(drum_name)

    # 5. Set FX via CC/NRPN
    _send_fx_midi(song.fx, midi)

    # 6. Set mixer
    for track_name, mix_cfg in song.mixer.items():
        level_key = f"{track_name}_level"
        pan_key = f"{track_name}_pan"
        if level_key in PROJECT_CC:
            midi.control_change(PROJECT_CHANNEL, PROJECT_CC[level_key], mix_cfg.level)
        if pan_key in PROJECT_CC:
            midi.control_change(PROJECT_CHANNEL, PROJECT_CC[pan_key], mix_cfg.pan)

    # 7. Set song queue
    if song.song:
        engine.set_queue(song.song)

    return results


def _send_fx_midi(fx: FXConfig, midi: MidiConnection) -> None:
    """Send FX settings over MIDI CC/NRPN."""
    # Build effective params: preset defaults merged with explicit overrides
    reverb_params = dict(fx.reverb)
    if fx.reverb_preset is not None and REVERB_PRESETS:
        preset_idx = _resolve_reverb_preset(fx)
        preset_vals = REVERB_PRESETS.get(preset_idx, {})
        for k, v in preset_vals.items():
            reverb_params.setdefault(k, v)

    delay_params = dict(fx.delay)
    if fx.delay_preset is not None and DELAY_PRESETS:
        preset_idx = _resolve_delay_preset(fx)
        preset_vals = DELAY_PRESETS.get(preset_idx, {})
        for k, v in preset_vals.items():
            delay_params.setdefault(k, v)

    # Reverb sends
    for track_name, value in fx.reverb_sends.items():
        key = f"reverb_{track_name}_send"
        if key in PROJECT_CC:
            midi.control_change(PROJECT_CHANNEL, PROJECT_CC[key], value)

    # Delay sends
    for track_name, value in fx.delay_sends.items():
        key = f"delay_{track_name}_send"
        if key in PROJECT_CC:
            midi.control_change(PROJECT_CHANNEL, PROJECT_CC[key], value)

    # Reverb params
    _nrpn_map = {
        "type": "reverb_type", "decay": "reverb_decay", "damping": "reverb_damping",
    }
    for param, nrpn_key in _nrpn_map.items():
        if param in reverb_params and nrpn_key in PROJECT_NRPN:
            msb, lsb = PROJECT_NRPN[nrpn_key]
            midi.nrpn(PROJECT_CHANNEL, msb, lsb, reverb_params[param])

    # Delay params
    _delay_map = {
        "time": "delay_time", "sync": "delay_time_sync", "feedback": "delay_feedback",
        "width": "delay_width", "lr_ratio": "delay_lr_ratio", "slew": "delay_slew_rate",
    }
    for param, nrpn_key in _delay_map.items():
        if param in delay_params and nrpn_key in PROJECT_NRPN:
            msb, lsb = PROJECT_NRPN[nrpn_key]
            midi.nrpn(PROJECT_CHANNEL, msb, lsb, delay_params[param])

    # Sidechain
    for synth_name, sc_data in fx.sidechain.items():
        prefix = f"sidechain_{synth_name}_"
        source_name = sc_data.get("source", "off")
        source_val = _SC_SOURCE.get(source_name, 4)
        for param, value in [("source", source_val)] + [
            (k, v) for k, v in sc_data.items() if k != "source"
        ]:
            nrpn_key = prefix + param
            if nrpn_key in PROJECT_NRPN:
                msb, lsb = PROJECT_NRPN[nrpn_key]
                midi.nrpn(PROJECT_CHANNEL, msb, lsb, value)


# --- NCS export ---


def song_to_ncs(song: SongData, template_path: Path | None = None) -> bytes:
    """Convert a SongData to a 160,780-byte NCS project binary.

    Uses Empty.ncs as template to preserve filler bytes.
    """
    tpl = template_path or _TEMPLATE_NCS
    if not tpl.exists():
        raise FileNotFoundError(f"NCS template not found: {tpl}")

    ncs = parse_ncs(tpl)

    # Header
    ncs.header.name = song.name[:32].ljust(32) if song.name else "Song".ljust(32)
    ncs.header.color = max(0, min(13, song.color))

    # Timing
    ncs.timing.tempo = max(40, min(240, song.bpm))
    ncs.timing.swing = max(20, min(80, song.swing))

    # Project settings
    ncs.project_settings.scale_root = _SCALE_ROOT.get(song.scale_root, 0)
    ncs.project_settings.scale_type = _SCALE_TYPE.get(song.scale_type.lower(), 14)

    # Build pattern slot mapping: unique patterns -> NCS slot indices 0-7
    if song.song:
        # Preserve order from song list
        seen: dict[str, int] = {}
        for pat_name in song.song:
            if pat_name not in seen:
                seen[pat_name] = len(seen)
        pattern_slots = seen
    else:
        pattern_slots = {name: i for i, name in enumerate(song.patterns.keys())}

    # Write patterns into NCS slots
    for pat_name, slot_idx in pattern_slots.items():
        pat_data = song.patterns[pat_name]
        ncs_length = min(pat_data.length, STEPS_PER_PATTERN)

        for track_name, track_data in pat_data.tracks.items():
            steps_raw = track_data.get("steps", {})

            if track_name in ("synth1", "synth2"):
                track_idx = 0 if track_name == "synth1" else 1
                ncs_pat = get_synth_pattern(ncs, track_idx, slot_idx)
                _write_synth_steps(ncs_pat, steps_raw, ncs_length)
                ncs_pat.settings.playback_end = ncs_length - 1

            elif track_name.startswith("drum"):
                drum_idx = int(track_name[-1]) - 1  # 0-3
                ncs_pat = get_drum_pattern(ncs, drum_idx, slot_idx)
                _write_drum_steps(ncs_pat, steps_raw, ncs_length, song, track_name)
                ncs_pat.settings.playback_end = ncs_length - 1

    # Synth patches
    for synth_name, attr in [("synth1", "synth1_patch"), ("synth2", "synth2_patch")]:
        sc = song.sounds.get(synth_name)
        if sc:
            synth_num = 1 if synth_name == "synth1" else 2
            patch_bytes = _build_patch_bytes(sc)
            setattr(ncs, attr, patch_bytes)

    # Drum configs (sample selection + per-drum settings)
    for drum_name in ("drum1", "drum2", "drum3", "drum4"):
        sc = song.sounds.get(drum_name)
        if not sc:
            continue
        drum_idx = int(drum_name[-1]) - 1
        cfg = ncs.drum_configs[drum_idx]
        if sc.sample is not None:
            cfg.patch_select = sc.sample
        if sc.level is not None:
            cfg.level = sc.level
        if sc.pitch is not None:
            cfg.pitch = sc.pitch
        if sc.decay is not None:
            cfg.decay = sc.decay
        if sc.distortion is not None:
            cfg.distortion = sc.distortion
        if sc.eq is not None:
            cfg.eq = sc.eq
        if sc.pan is not None:
            cfg.pan = sc.pan

    # FX settings
    _apply_fx_to_ncs(ncs, song.fx)

    # Mixer
    if "synth1" in song.mixer:
        ncs.fx.mixer_levels[0] = song.mixer["synth1"].level
        ncs.fx.mixer_pans[0] = song.mixer["synth1"].pan
    if "synth2" in song.mixer:
        ncs.fx.mixer_levels[1] = song.mixer["synth2"].level
        ncs.fx.mixer_pans[1] = song.mixer["synth2"].pan

    # Scenes for song order
    if song.song:
        for scene_idx, pat_name in enumerate(song.song):
            slot = pattern_slots[pat_name]
            # All tracks point to the same pattern slot
            track_chains = {i: (slot, slot) for i in range(NUM_TRACKS)}
            set_scene(ncs, scene_idx, track_chains)
        set_scene_chain(ncs, start=0, end=len(song.song) - 1)

    return serialize_ncs(ncs)


def export_song_to_device(
    song: SongData,
    midi: MidiConnection,
    slot: int = 0,
    name: str = "",
    template_path: Path | None = None,
) -> dict:
    """Export a song to the Circuit Tracks as an NCS project.

    Returns transfer result dict.
    """
    if name:
        song.name = name

    ncs_bytes = song_to_ncs(song, template_path)

    return send_ncs_project(
        midi,
        ncs_bytes,
        slot=slot,
        filename=song.name.strip()[:16] or "Song",
    )


# --- Internal helpers ---


def _build_patch_bytes(sc: SoundConfig) -> bytes:
    """Build a 340-byte synth patch from a SoundConfig."""
    if sc.preset and sc.preset.lower() in _PRESETS:
        builder = _PRESETS[sc.preset.lower()](sc.name or sc.preset)
    else:
        builder = PatchBuilder(sc.name or "Init")

    if sc.params:
        for param_name, value in sc.params.items():
            if param_name in _PARAM_OFFSETS:
                builder._bytes[_PARAM_OFFSETS[param_name]] = max(0, min(127, int(value)))

    if sc.mod_matrix:
        builder.clear_mods()
        for entry in sc.mod_matrix:
            raw_depth = entry.get("depth", 16)
            if -64 <= raw_depth <= 63:
                raw_depth = raw_depth + 64
            builder.add_mod(
                source=entry.get("source", 0),
                destination=entry.get("dest", 0),
                depth=raw_depth,
                source2=entry.get("source2", 0),
            )

    if sc.macros:
        for macro_num_str, config in sc.macros.items():
            targets = config.get("targets", [])
            position = config.get("position", 0)
            builder.set_macro(int(macro_num_str), targets, position=position)

    return builder.build()


def _write_synth_steps(
    ncs_pat: SynthPattern, steps_raw: dict, length: int,
) -> None:
    """Write sequencer step data into an NCS SynthPattern."""
    for idx_str, step_data in steps_raw.items():
        idx = int(idx_str)
        if idx >= length or idx >= STEPS_PER_PATTERN:
            continue

        step = Step.from_dict(step_data)
        if not step.enabled:
            continue

        ncs_step = ncs_pat.steps[idx]

        # Write notes
        mask = 0
        for i, note in enumerate(step.notes[:NOTES_PER_STEP]):
            mask |= 1 << i
            ncs_step.notes[i] = NCSNote(
                # Circuit Tracks plays NCS notes 1 octave below external MIDI
                note_number=max(0, min(127, note + 12)),
                gate=max(1, min(6, round(step.gate * 6))),
                delay=0,
                velocity=max(0, min(127, step.velocity)),
            )

        ncs_step.assigned_note_mask = mask
        ncs_step.probability = max(0, min(7, round(step.probability * 7)))


def _write_drum_steps(
    ncs_pat: DrumPattern, steps_raw: dict, length: int,
    song: SongData, track_name: str,
) -> None:
    """Write sequencer step data into an NCS DrumPattern."""
    # Get global drum sample for this track
    sc = song.sounds.get(track_name)
    global_sample = sc.sample if sc and sc.sample is not None else None

    for idx_str, step_data in steps_raw.items():
        idx = int(idx_str)
        if idx >= length or idx >= STEPS_PER_PATTERN:
            continue

        step = Step.from_dict(step_data)
        if not step.enabled:
            continue

        ncs_step = ncs_pat.steps[idx]
        ncs_step.active = True
        ncs_step.velocity = max(0, min(127, step.velocity))
        ncs_step.probability = max(0, min(7, round(step.probability * 7)))

        # Per-step sample overrides global track sample
        if step.sample is not None:
            ncs_step.drum_choice = step.sample
        elif global_sample is not None:
            ncs_step.drum_choice = global_sample
        else:
            ncs_step.drum_choice = DEFAULT_DRUM_CHOICE


def _resolve_reverb_preset(fx: FXConfig) -> int:
    """Resolve the reverb preset index from explicit preset or closest match."""
    if fx.reverb_preset is not None:
        if isinstance(fx.reverb_preset, str):
            return REVERB_PRESET_BY_NAME[fx.reverb_preset.lower()]
        return fx.reverb_preset
    if fx.reverb and REVERB_PRESETS:
        return _find_closest_reverb(fx.reverb)
    return 0


def _resolve_delay_preset(fx: FXConfig) -> int:
    """Resolve the delay preset index from explicit preset or closest match."""
    if fx.delay_preset is not None:
        if isinstance(fx.delay_preset, str):
            return DELAY_PRESET_BY_NAME[fx.delay_preset.lower()]
        return fx.delay_preset
    if fx.delay and DELAY_PRESETS:
        return _find_closest_delay(fx.delay)
    return 0


# Parameter ranges for normalization in distance calculations
_REVERB_RANGES = {"type": 5, "decay": 127, "damping": 127}
_DELAY_RANGES = {"time": 127, "sync": 35, "feedback": 127, "width": 127, "lr_ratio": 12, "slew": 127}


def _find_closest_reverb(params: dict[str, int]) -> int:
    """Find the reverb preset closest to the given parameter values."""
    best_idx, best_dist = 0, float("inf")
    for idx, preset in REVERB_PRESETS.items():
        dist = sum(
            ((params.get(k, preset[k]) - preset[k]) / _REVERB_RANGES[k]) ** 2
            for k in _REVERB_RANGES
        )
        if dist < best_dist:
            best_idx, best_dist = idx, dist
    return best_idx


def _find_closest_delay(params: dict[str, int]) -> int:
    """Find the delay preset closest to the given parameter values."""
    best_idx, best_dist = 0, float("inf")
    for idx, preset in DELAY_PRESETS.items():
        dist = sum(
            ((params.get(k, preset[k]) - preset[k]) / _DELAY_RANGES[k]) ** 2
            for k in _DELAY_RANGES
        )
        if dist < best_dist:
            best_idx, best_dist = idx, dist
    return best_idx


def _apply_fx_to_ncs(ncs: NCSFile, fx: FXConfig) -> None:
    """Apply FX configuration to an NCS file."""
    # Resolve and set preset indices
    ncs.project_settings.reverb_preset = _resolve_reverb_preset(fx)
    ncs.project_settings.delay_preset = _resolve_delay_preset(fx)

    # If a preset is specified, use its params as defaults
    reverb_params = dict(fx.reverb)
    if fx.reverb_preset is not None and REVERB_PRESETS:
        preset_vals = REVERB_PRESETS.get(ncs.project_settings.reverb_preset, {})
        for k, v in preset_vals.items():
            reverb_params.setdefault(k, v)

    delay_params = dict(fx.delay)
    if fx.delay_preset is not None and DELAY_PRESETS:
        preset_vals = DELAY_PRESETS.get(ncs.project_settings.delay_preset, {})
        for k, v in preset_vals.items():
            delay_params.setdefault(k, v)

    # Reverb sends
    for track_name, value in fx.reverb_sends.items():
        idx = _SEND_INDEX.get(track_name)
        if idx is not None:
            ncs.fx.reverb_sends[idx] = max(0, min(127, value))

    # Delay sends
    for track_name, value in fx.delay_sends.items():
        idx = _SEND_INDEX.get(track_name)
        if idx is not None:
            ncs.fx.delay_sends[idx] = max(0, min(127, value))

    # Reverb params
    if "type" in reverb_params:
        ncs.fx.reverb_type = reverb_params["type"]
    if "decay" in reverb_params:
        ncs.fx.reverb_decay = reverb_params["decay"]
    if "damping" in reverb_params:
        ncs.fx.reverb_damping = reverb_params["damping"]

    # Delay params
    if "time" in delay_params:
        ncs.fx.delay_time = delay_params["time"]
    if "sync" in delay_params:
        ncs.fx.delay_sync = delay_params["sync"]
    if "feedback" in delay_params:
        ncs.fx.delay_feedback = delay_params["feedback"]
    if "width" in delay_params:
        ncs.fx.delay_width = delay_params["width"]
    if "lr_ratio" in delay_params:
        ncs.fx.delay_lr_ratio = delay_params["lr_ratio"]
    if "slew" in delay_params:
        ncs.fx.delay_slew = delay_params["slew"]

    # Sidechain
    for synth_name, sc_data in fx.sidechain.items():
        source_name = sc_data.get("source", "off")
        source_val = _SC_SOURCE.get(source_name, 4)
        sc = SidechainSettings(
            source=source_val,
            attack=sc_data.get("attack", 0),
            hold=sc_data.get("hold", 50),
            decay=sc_data.get("decay", 70),
            depth=sc_data.get("depth", 0),
        )
        if synth_name == "synth1":
            ncs.fx.sidechain_s1 = sc
        elif synth_name == "synth2":
            ncs.fx.sidechain_s2 = sc
