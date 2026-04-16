"""Song format: a single JSON structure describing an entire Circuit Tracks song.

Supports loading into the live sequencer for preview, and exporting to an NCS
project file for transfer to the hardware.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from circuit_tracks.constants import (
    DELAY_PRESET_BY_NAME,
    DELAY_PRESETS,
    DRUM_CC,
    DRUMS_CHANNEL,
    PROJECT_CC,
    PROJECT_CHANNEL,
    PROJECT_NRPN,
    REVERB_PRESET_BY_NAME,
    REVERB_PRESETS,
)
from circuit_tracks.midi import MidiConnection
from circuit_tracks.ncs_parser import (
    DEFAULT_DRUM_CHOICE,
    NOTES_PER_STEP,
    NUM_TRACKS,
    PATTERNS_PER_TRACK,
    STEPS_PER_PATTERN,
    DrumPattern,
    NCSFile,
    NCSNote,
    SidechainSettings,
    SynthPattern,
    get_drum_pattern,
    get_midi_pattern,
    get_synth_pattern,
    parse_ncs,
    serialize_ncs,
    set_scene,
    set_scene_chain,
)
from circuit_tracks.ncs_transfer import send_ncs_project
from circuit_tracks.patch import _PARAM_OFFSETS, send_current_patch
from circuit_tracks.patch_builder import (
    PatchBuilder,
    preset_bass,
    preset_lead,
    preset_pad,
    preset_pluck,
)
from circuit_tracks.sequencer import (
    Pattern,
    SequencerEngine,
    Step,
)

logger = logging.getLogger(__name__)

# Template file for NCS export (bundled with the package)
_TEMPLATE_NCS = Path(__file__).parent / "data" / "Empty.ncs"

_PRESETS = {"pad": preset_pad, "bass": preset_bass, "lead": preset_lead, "pluck": preset_pluck}

# Friendly name -> NCS send array index
_SEND_INDEX = {
    "synth1": 0,
    "synth2": 1,
    "drum1": 2,
    "drum2": 3,
    "drum3": 4,
    "drum4": 5,
    "midi1": 6,
    "midi2": 7,
}

# NCS track ordering: S1, S2, M1, M2, D1, D2, D3, D4
_NCS_TRACK_ORDER = ["synth1", "synth2", "midi1", "midi2", "drum1", "drum2", "drum3", "drum4"]

# Sidechain source name -> NCS integer
_SC_SOURCE = {"drum1": 0, "drum2": 1, "drum3": 2, "drum4": 3, "off": 4}

# Sidechain preset -> parameter values (attack, hold, decay, depth)
# Presets ramp from subtle (1) to heavy ducking (7). Attack is always 5.
_SC_PRESET_PARAMS: dict[int, tuple[int, int, int, int]] = {
    1: (5, 50, 80, 80),
    2: (5, 70, 70, 100),
    3: (5, 85, 70, 115),
    4: (5, 90, 75, 123),
    5: (5, 90, 85, 127),
    6: (5, 95, 95, 127),
    7: (5, 102, 95, 127),
}

# Scale root name -> integer
_SCALE_ROOT = {
    "C": 0,
    "C#": 1,
    "Db": 1,
    "D": 2,
    "D#": 3,
    "Eb": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "Gb": 6,
    "G": 7,
    "G#": 8,
    "Ab": 8,
    "A": 9,
    "A#": 10,
    "Bb": 10,
    "B": 11,
}

# Scale type name -> integer
_SCALE_TYPE = {
    "natural minor": 0,
    "minor": 0,
    "major": 1,
    "dorian": 2,
    "phrygian": 3,
    "mixolydian": 4,
    "melodic minor": 5,
    "harmonic minor": 6,
    "bebop dorian": 7,
    "blues": 8,
    "minor pentatonic": 9,
    "hungarian minor": 10,
    "ukranian dorian": 11,
    "marva": 12,
    "todi": 13,
    "whole tone": 14,
    "chromatic": 15,
}

# Scale type integer -> semitone intervals from root
_SCALE_INTERVALS: dict[int, list[int]] = {
    0: [0, 2, 3, 5, 7, 8, 10],  # Natural Minor
    1: [0, 2, 4, 5, 7, 9, 11],  # Major
    2: [0, 2, 3, 5, 7, 9, 10],  # Dorian
    3: [0, 1, 3, 5, 7, 8, 10],  # Phrygian
    4: [0, 2, 4, 5, 7, 9, 10],  # Mixolydian
    5: [0, 2, 3, 5, 7, 9, 11],  # Melodic Minor
    6: [0, 2, 3, 5, 7, 8, 11],  # Harmonic Minor
    7: [0, 2, 3, 4, 5, 7, 9, 10],  # Bebop Dorian
    8: [0, 3, 5, 6, 7, 10],  # Blues
    9: [0, 3, 5, 7, 10],  # Minor Pentatonic
    10: [0, 2, 3, 6, 7, 8, 11],  # Hungarian Minor
    11: [0, 2, 3, 6, 7, 9, 10],  # Ukrainian Dorian
    12: [0, 1, 4, 6, 7, 9, 11],  # Marva
    13: [0, 1, 3, 6, 7, 8, 11],  # Todi
    14: [0, 2, 4, 6, 8, 10],  # Whole Tone
    15: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],  # Chromatic
}


def quantize_to_scale(note: int, root: int, scale_type: int) -> int:
    """Snap a MIDI note to the nearest note in a scale.

    Args:
        note: MIDI note number (0-127).
        root: Scale root as semitone offset from C (0-11).
        scale_type: Scale type index (0-15, matching Circuit Tracks values).

    Returns:
        The nearest MIDI note number that belongs to the scale.
        On ties (equidistant from two scale notes), rounds up to match
        Circuit Tracks hardware behavior.
    """
    if scale_type == 15:  # Chromatic — every note is in scale
        return note

    intervals = _SCALE_INTERVALS.get(scale_type)
    if intervals is None:
        return note

    # Check candidates across nearby octaves to handle boundary cases
    best = note
    best_dist = 128
    for octave in range((note // 12) - 1, (note // 12) + 2):
        for interval in intervals:
            candidate = octave * 12 + root + interval
            if candidate < 0 or candidate > 127:
                continue
            dist = abs(candidate - note)
            if dist < best_dist or (dist == best_dist and candidate > best):
                best_dist = dist
                best = candidate

    return best


# Reverse lookup dicts for ncs_to_song
_SCALE_ROOT_REVERSE = {
    0: "C",
    1: "C#",
    2: "D",
    3: "D#",
    4: "E",
    5: "F",
    6: "F#",
    7: "G",
    8: "G#",
    9: "A",
    10: "A#",
    11: "B",
}
_SCALE_TYPE_REVERSE = {v: k for k, v in _SCALE_TYPE.items() if k != "minor"}
_SC_SOURCE_REVERSE = {v: k for k, v in _SC_SOURCE.items()}
_SEND_INDEX_REVERSE = {v: k for k, v in _SEND_INDEX.items()}

# Synth engine param offsets to include in ncs_to_song output (skip mod matrix)
_SYNTH_ENGINE_PARAM_MAX_OFFSET = 123


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
    delay_preset: str | int | None = None  # preset name or index (0-15)


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

    Uses Pydantic schema validation to reject unknown/invalid keys,
    then converts to the internal SongData dataclass for downstream use.

    Raises ValueError or pydantic.ValidationError on invalid input.
    """
    from pydantic import ValidationError as PydanticValidationError

    from circuit_tracks.song_schema import SongSchema

    try:
        validated = SongSchema.model_validate(d)
    except PydanticValidationError as e:
        raise ValueError(str(e)) from e
    song = _schema_to_song_data(validated)

    # Quantize notes to scale so MIDI preview and NCS export match (DEV-0017)
    _quantize_song_notes(song)

    return song


def _schema_to_song_data(v: object) -> SongData:
    """Convert a validated SongSchema to the internal SongData dataclass.

    Keeps downstream code (sequencer, NCS export) unchanged by producing
    the same dict-based structures they expect.
    """
    song = SongData()

    song.name = v.name
    song.bpm = v.bpm
    song.swing = v.swing
    song.color = v.color
    song.scale_root = v.scale.root
    song.scale_type = v.scale.type

    # Sounds — convert Pydantic models back to the SoundConfig dataclass
    if v.sounds:
        for track_name, sound_model in v.sounds.items():
            sc = SoundConfig()
            if hasattr(sound_model, "preset"):
                # SynthSoundConfig
                sc.preset = sound_model.preset
                sc.name = sound_model.name
                sc.params = sound_model.params
                # Convert mod matrix entries back to dicts
                if sound_model.mod_matrix:
                    sc.mod_matrix = [
                        entry.model_dump(exclude_none=True, exclude={"destination"}) for entry in sound_model.mod_matrix
                    ]
                # Convert macro configs back to dicts
                if sound_model.macros:
                    sc.macros = {k: cfg.model_dump() for k, cfg in sound_model.macros.items()}
            else:
                # DrumSoundConfig
                sc.sample = sound_model.sample
                sc.level = sound_model.level
                sc.pitch = sound_model.pitch
                sc.decay = sound_model.decay
                sc.distortion = sound_model.distortion
                sc.eq = sound_model.eq
                sc.pan = sound_model.pan
            song.sounds[track_name] = sc

    # FX — convert back to dict-based FXConfig dataclass
    if v.fx:
        fx = v.fx
        song.fx = FXConfig(
            reverb=fx.reverb.model_dump(exclude_none=True) if fx.reverb else {},
            delay=fx.delay.model_dump(exclude_none=True) if fx.delay else {},
            reverb_sends=dict(fx.reverb_sends) if fx.reverb_sends else {},
            delay_sends=dict(fx.delay_sends) if fx.delay_sends else {},
            sidechain={k: sc.model_dump(exclude_none=True) for k, sc in fx.sidechain.items()} if fx.sidechain else {},
            reverb_preset=fx.reverb_preset,
            delay_preset=fx.delay_preset,
        )

    # Mixer
    if v.mixer:
        for track_name, mix_model in v.mixer.items():
            song.mixer[track_name] = MixerConfig(
                level=mix_model.level,
                pan=mix_model.pan,
            )

    # Patterns — convert back to raw dicts for downstream compatibility
    for pat_name, pat_model in v.patterns.items():
        tracks_raw: dict[str, dict] = {}
        for track_name, track_model in pat_model.tracks.items():
            tracks_raw[track_name] = track_model.model_dump(
                exclude_none=True,
                exclude_defaults=True,
            )
        song.patterns[pat_name] = PatternData(
            length=pat_model.length,
            tracks=tracks_raw,
        )

    # Song structure
    song.song = list(v.song) if v.song else []

    return song


def _quantize_song_notes(song: SongData) -> int:
    """Quantize all synth/MIDI track notes in a SongData to its scale.

    Modifies the song in place. Drum tracks are not affected.

    Returns:
        Number of notes that were changed.
    """
    root = _SCALE_ROOT.get(song.scale_root, 0)
    scale_type = _SCALE_TYPE.get(song.scale_type.lower(), 15)

    if scale_type == 15:  # Chromatic — no quantization needed
        return 0

    changed = 0
    for pat_name, pat_data in song.patterns.items():
        for track_name, track_data in pat_data.tracks.items():
            if track_name not in ("synth1", "synth2", "midi1", "midi2"):
                continue

            steps = track_data.get("steps", {})
            for idx_str, step_data in steps.items():
                if not isinstance(step_data, dict):
                    continue

                if "note" in step_data:
                    original = step_data["note"]
                    quantized = quantize_to_scale(original, root, scale_type)
                    if quantized != original:
                        step_data["note"] = quantized
                        changed += 1
                        logger.debug(
                            "Quantized note %d -> %d (pattern=%s, track=%s, step=%s)",
                            original,
                            quantized,
                            pat_name,
                            track_name,
                            idx_str,
                        )

                if "notes" in step_data:
                    notes = step_data["notes"]
                    new_notes = []
                    for n in notes:
                        q = quantize_to_scale(n, root, scale_type)
                        if q != n:
                            changed += 1
                            logger.debug(
                                "Quantized note %d -> %d (pattern=%s, track=%s, step=%s)",
                                n,
                                q,
                                pat_name,
                                track_name,
                                idx_str,
                            )
                        new_notes.append(q)
                    step_data["notes"] = new_notes

    return changed


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
        "type": "reverb_type",
        "decay": "reverb_decay",
        "damping": "reverb_damping",
    }
    for param, nrpn_key in _nrpn_map.items():
        if param in reverb_params and nrpn_key in PROJECT_NRPN:
            msb, lsb = PROJECT_NRPN[nrpn_key]
            midi.nrpn(PROJECT_CHANNEL, msb, lsb, reverb_params[param])

    # Delay params
    _delay_map = {
        "time": "delay_time",
        "sync": "delay_time_sync",
        "feedback": "delay_feedback",
        "width": "delay_width",
        "lr_ratio": "delay_lr_ratio",
        "slew": "delay_slew_rate",
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
        for param, value in [("source", source_val)] + [(k, v) for k, v in sc_data.items() if k != "source"]:
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

    # Scale integers for note quantization in _write_synth_steps (DEV-0017)
    _sr = ncs.project_settings.scale_root
    _st = ncs.project_settings.scale_type

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

    # Set consistent length on ALL pattern slots and ALL tracks upfront.
    # This ensures every track in every slot has the correct length, even
    # tracks that have no step data in a given pattern.
    default_length = 32
    if song.patterns:
        default_length = max(p.length for p in song.patterns.values())
    for slot_idx in range(PATTERNS_PER_TRACK):
        # Used slots get their own length; unused slots get the default
        if slot_idx in pattern_slots.values():
            pat_name = next(n for n, s in pattern_slots.items() if s == slot_idx)
            end = min(song.patterns[pat_name].length, STEPS_PER_PATTERN) - 1
        else:
            end = min(default_length, STEPS_PER_PATTERN) - 1
        for track_idx in range(2):
            get_synth_pattern(ncs, track_idx, slot_idx).settings.playback_end = end
        for drum_idx in range(4):
            get_drum_pattern(ncs, drum_idx, slot_idx).settings.playback_end = end
        for midi_idx in range(2):
            get_midi_pattern(ncs, midi_idx, slot_idx).settings.playback_end = end

    # Write patterns into NCS slots
    for pat_name, slot_idx in pattern_slots.items():
        pat_data = song.patterns[pat_name]
        ncs_length = min(pat_data.length, STEPS_PER_PATTERN)

        for track_name, track_data in pat_data.tracks.items():
            steps_raw = track_data.get("steps", {})

            if track_name in ("synth1", "synth2"):
                track_idx = 0 if track_name == "synth1" else 1
                ncs_pat = get_synth_pattern(ncs, track_idx, slot_idx)
                _write_synth_steps(ncs_pat, steps_raw, ncs_length, _sr, _st)
                _write_track_macros(ncs_pat, track_data, ncs_length)
                _write_mixer_locks(ncs_pat, track_data, ncs_length)

            elif track_name.startswith("drum"):
                drum_idx = int(track_name[-1]) - 1  # 0-3
                ncs_pat = get_drum_pattern(ncs, drum_idx, slot_idx)
                _write_drum_steps(ncs_pat, steps_raw, ncs_length, song, track_name)
                _write_drum_param_locks(ncs_pat, track_data, ncs_length)

            elif track_name in ("midi1", "midi2"):
                midi_idx = 0 if track_name == "midi1" else 1
                ncs_pat = get_midi_pattern(ncs, midi_idx, slot_idx)
                _write_synth_steps(ncs_pat, steps_raw, ncs_length, _sr, _st)
                _write_track_macros(ncs_pat, track_data, ncs_length)
                _write_mixer_locks(ncs_pat, track_data, ncs_length)

    # Synth patches
    for synth_name, attr in [("synth1", "synth1_patch"), ("synth2", "synth2_patch")]:
        sc = song.sounds.get(synth_name)
        if sc:
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
            # All tracks play a single pattern: start == end
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


# --- NCS to SongData (reverse conversion) ---


def ncs_to_song(ncs: NCSFile) -> SongData:
    """Convert an NCSFile to a SongData structure.

    This is the reverse of song_to_ncs(). It reads the binary NCS project
    data and reconstructs a SongData with patterns, sounds, FX, mixer, and
    song order.

    Args:
        ncs: Parsed NCSFile structure.

    Returns:
        SongData representing the project contents.
    """
    song = SongData()

    # Header
    song.name = ncs.header.name.strip()
    song.color = ncs.header.color

    # Timing
    song.bpm = ncs.timing.tempo
    song.swing = ncs.timing.swing

    # Scale
    song.scale_root = _SCALE_ROOT_REVERSE.get(ncs.project_settings.scale_root, "C")
    song.scale_type = _SCALE_TYPE_REVERSE.get(ncs.project_settings.scale_type, "chromatic")

    # Scan pattern slots 0-7 for non-empty patterns
    pattern_names: dict[int, str] = {}  # slot_idx -> pattern name

    _root_int = ncs.project_settings.scale_root
    _type_int = ncs.project_settings.scale_type

    for slot_idx in range(PATTERNS_PER_TRACK):
        if _is_slot_non_empty(ncs, slot_idx):
            pat_name = f"pattern_{slot_idx}"
            pat_data = _read_pattern_slot(ncs, slot_idx, _root_int, _type_int)
            song.patterns[pat_name] = pat_data
            pattern_names[slot_idx] = pat_name

    # Synth patches
    for synth_name, patch_bytes in [("synth1", ncs.synth1_patch), ("synth2", ncs.synth2_patch)]:
        sc = _parse_embedded_patch(patch_bytes)
        if sc is not None:
            song.sounds[synth_name] = sc

    # Drum configs
    for drum_idx in range(4):
        cfg = ncs.drum_configs[drum_idx]
        drum_name = f"drum{drum_idx + 1}"
        sc = SoundConfig(
            sample=cfg.patch_select,
            level=cfg.level,
            pitch=cfg.pitch,
            decay=cfg.decay,
            distortion=cfg.distortion,
            eq=cfg.eq,
            pan=cfg.pan,
        )
        song.sounds[drum_name] = sc

    # FX
    song.fx = _read_fx_from_ncs(ncs)

    # Mixer
    if ncs.fx.mixer_levels[0] != 100 or ncs.fx.mixer_pans[0] != 64:
        song.mixer["synth1"] = MixerConfig(
            level=ncs.fx.mixer_levels[0],
            pan=ncs.fx.mixer_pans[0],
        )
    if ncs.fx.mixer_levels[1] != 100 or ncs.fx.mixer_pans[1] != 64:
        song.mixer["synth2"] = MixerConfig(
            level=ncs.fx.mixer_levels[1],
            pan=ncs.fx.mixer_pans[1],
        )

    # Song order from scene chain
    song.song = _read_song_order(ncs, pattern_names)

    return song


def _is_slot_non_empty(ncs: NCSFile, slot_idx: int) -> bool:
    """Check if any track at a given pattern slot has data."""
    # Synth tracks
    for track_idx in range(2):
        pat = get_synth_pattern(ncs, track_idx, slot_idx)
        if any(s.assigned_note_mask != 0 for s in pat.steps):
            return True
        if pat.macro_locks or pat.mixer_locks:
            return True

    # Drum tracks
    for drum_idx in range(4):
        pat = get_drum_pattern(ncs, drum_idx, slot_idx)
        if any(s.active for s in pat.steps):
            return True
        if pat.param_locks:
            return True

    # MIDI tracks
    for midi_idx in range(2):
        pat = get_midi_pattern(ncs, midi_idx, slot_idx)
        if any(s.assigned_note_mask != 0 for s in pat.steps):
            return True
        if pat.macro_locks or pat.mixer_locks:
            return True

    return False


def _read_pattern_slot(
    ncs: NCSFile,
    slot_idx: int,
    scale_root: int = 0,
    scale_type: int = 15,
) -> PatternData:
    """Read all tracks at a pattern slot and build a PatternData."""
    tracks: dict[str, dict] = {}
    max_length = 1

    # Synth tracks
    for track_idx, track_name in enumerate(["synth1", "synth2"]):
        pat = get_synth_pattern(ncs, track_idx, slot_idx)
        track_data = _read_synth_track(pat, scale_root, scale_type)
        if track_data:
            tracks[track_name] = track_data
            length = pat.settings.playback_end + 1
            max_length = max(max_length, length)

    # Drum tracks
    for drum_idx in range(4):
        track_name = f"drum{drum_idx + 1}"
        pat = get_drum_pattern(ncs, drum_idx, slot_idx)
        track_data = _read_drum_track(pat)
        if track_data:
            tracks[track_name] = track_data
            length = pat.settings.playback_end + 1
            max_length = max(max_length, length)

    # MIDI tracks
    for midi_idx, track_name in enumerate(["midi1", "midi2"]):
        pat = get_midi_pattern(ncs, midi_idx, slot_idx)
        track_data = _read_synth_track(pat, scale_root, scale_type)
        if track_data:
            tracks[track_name] = track_data
            length = pat.settings.playback_end + 1
            max_length = max(max_length, length)

    return PatternData(length=max_length, tracks=tracks)


def _read_synth_track(
    pat: SynthPattern,
    scale_root: int = 0,
    scale_type: int = 15,
) -> dict | None:
    """Read a synth/MIDI pattern into a track data dict."""
    steps: dict[str, dict] = {}
    has_data = False

    for i, step in enumerate(pat.steps):
        if step.assigned_note_mask == 0:
            continue
        has_data = True
        step_dict: dict = {}

        # Read active notes
        # Device plays: quantize(ncs_note, 0, type) + root - 12
        active = step.active_notes
        if len(active) == 1:
            note = active[0]
            step_dict["note"] = quantize_to_scale(note.note_number, 0, scale_type) - 12 + scale_root
            step_dict["velocity"] = note.velocity
            raw_gate = note.gate
            step_dict["gate"] = round((raw_gate & 0x7F) / 6.0, 3)
            if raw_gate & 0x80:
                step_dict["tie"] = True
        elif len(active) > 1:
            step_dict["notes"] = [quantize_to_scale(n.note_number, 0, scale_type) - 12 + scale_root for n in active]
            step_dict["velocity"] = active[0].velocity
            raw_gate = active[0].gate
            step_dict["gate"] = round((raw_gate & 0x7F) / 6.0, 3)
            if raw_gate & 0x80:
                step_dict["tie"] = True

        step_dict["probability"] = round(step.probability / 7.0, 3)
        steps[str(i)] = step_dict

    # Track-level macro locks
    macros: dict[str, dict[str, int]] = {}
    for macro_num, positions in pat.macro_locks.items():
        macro_dict: dict[str, int] = {}
        for pos, val in positions.items():
            macro_dict[str(pos)] = val
        macros[str(macro_num)] = macro_dict

    # Track-level mixer locks
    mixer: dict[str, dict[str, int]] = {}
    for param_name, positions in pat.mixer_locks.items():
        mixer_dict: dict[str, int] = {}
        for pos, val in positions.items():
            mixer_dict[str(pos)] = val
        mixer[param_name] = mixer_dict

    if not has_data and not macros and not mixer:
        return None

    result: dict = {}
    if steps:
        result["steps"] = steps
    if macros:
        result["macros"] = macros
    if mixer:
        result["mixer"] = mixer
    return result


def _read_drum_track(pat: DrumPattern) -> dict | None:
    """Read a drum pattern into a track data dict."""
    steps: dict[str, dict] = {}
    has_data = False

    for i, step in enumerate(pat.steps):
        if not step.active:
            continue
        has_data = True
        step_dict: dict = {"velocity": step.velocity}
        step_dict["probability"] = round(step.probability / 7.0, 3)
        if step.drum_choice != DEFAULT_DRUM_CHOICE:
            step_dict["sample"] = step.drum_choice
        steps[str(i)] = step_dict

    # Track-level param locks
    params: dict[str, dict[str, int]] = {}
    for param_name, positions in pat.param_locks.items():
        param_dict: dict[str, int] = {}
        for pos, val in positions.items():
            param_dict[str(pos)] = val
        params[param_name] = param_dict

    if not has_data and not params:
        return None

    result: dict = {}
    if steps:
        result["steps"] = steps
    if params:
        result["params"] = params
    return result


def _parse_embedded_patch(patch_bytes: bytes) -> SoundConfig | None:
    """Parse a 340-byte embedded synth patch into a SoundConfig."""
    if len(patch_bytes) < 340:
        return None

    from circuit_tracks.constants import (
        MACRO_DESTINATIONS,
        MOD_MATRIX_DESTINATIONS,
        MOD_MATRIX_SOURCES,
    )

    # Extract name (bytes 0-15 ASCII)
    name = ""
    for b in patch_bytes[0:16]:
        if 32 <= b <= 126:
            name += chr(b)
    name = name.strip()

    # Extract synth engine params (skip mod matrix params)
    params: dict[str, int] = {}
    for param_name, offset in _PARAM_OFFSETS.items():
        # Skip mod matrix params (modN_sourceN, modN_depth, modN_destination)
        if offset > _SYNTH_ENGINE_PARAM_MAX_OFFSET:
            continue
        if offset < len(patch_bytes):
            params[param_name] = patch_bytes[offset]

    # Extract mod matrix (20 slots, 4 bytes each, starting at offset 124)
    _MOD_START = 124
    _MOD_SLOTS = 20
    mod_matrix: list[dict] = []
    for i in range(_MOD_SLOTS):
        addr = _MOD_START + i * 4
        source = patch_bytes[addr]
        source2 = patch_bytes[addr + 1]
        raw_depth = patch_bytes[addr + 2]
        dest = patch_bytes[addr + 3]
        # Skip empty slots (depth 64 = no modulation, source 0 + dest 0)
        if raw_depth == 64 and source == 0 and dest == 0:
            continue
        entry: dict = {
            "source": MOD_MATRIX_SOURCES.get(source, source),
            "dest": MOD_MATRIX_DESTINATIONS.get(dest, dest),
            "depth": raw_depth - 64,  # Convert raw 0-127 to signed -64..+63
        }
        if source2 != 0:
            entry["source2"] = MOD_MATRIX_SOURCES.get(source2, source2)
        mod_matrix.append(entry)

    # Extract macros (8 macros, 17 bytes each, starting at offset 204)
    _MACRO_START = 204
    _MACRO_COUNT = 8
    _MACRO_SIZE = 17
    _MACRO_TARGETS = 4
    macros: dict[str, dict] = {}
    for m in range(_MACRO_COUNT):
        base = _MACRO_START + m * _MACRO_SIZE
        position = patch_bytes[base]
        targets: list[dict] = []
        for t in range(_MACRO_TARGETS):
            tb = base + 1 + t * 4
            dest_idx = patch_bytes[tb]
            start = patch_bytes[tb + 1]
            end = patch_bytes[tb + 2]
            depth = patch_bytes[tb + 3]
            # Skip empty targets (dest=0, start=0, end=127, depth=64 is sentinel)
            if dest_idx == 0 and start == 0 and end == 127 and depth == 64:
                continue
            targets.append(
                {
                    "dest": MACRO_DESTINATIONS.get(dest_idx, dest_idx),
                    "start": start,
                    "end": end,
                    "depth": depth,
                }
            )
        if targets:
            macro_cfg: dict = {"targets": targets}
            if position != 0:
                macro_cfg["position"] = position
            macros[str(m + 1)] = macro_cfg

    return SoundConfig(
        name=name if name else None,
        params=params,
        mod_matrix=mod_matrix if mod_matrix else None,
        macros=macros if macros else None,
    )


def _read_fx_from_ncs(ncs: NCSFile) -> FXConfig:
    """Read FX settings from an NCS file into an FXConfig."""
    fx = FXConfig()

    # Reverb params
    fx.reverb = {
        "type": ncs.fx.reverb_type,
        "decay": ncs.fx.reverb_decay,
        "damping": ncs.fx.reverb_damping,
    }

    # Delay params
    fx.delay = {
        "time": ncs.fx.delay_time,
        "sync": ncs.fx.delay_sync,
        "feedback": ncs.fx.delay_feedback,
        "width": ncs.fx.delay_width,
        "lr_ratio": ncs.fx.delay_lr_ratio,
        "slew": ncs.fx.delay_slew,
    }

    # Reverb sends
    for idx, val in enumerate(ncs.fx.reverb_sends):
        track_name = _SEND_INDEX_REVERSE.get(idx)
        if track_name and val != 0:
            fx.reverb_sends[track_name] = val

    # Delay sends
    for idx, val in enumerate(ncs.fx.delay_sends):
        track_name = _SEND_INDEX_REVERSE.get(idx)
        if track_name and val != 0:
            fx.delay_sends[track_name] = val

    # Sidechain
    for track_name, sc_settings in [
        ("synth1", ncs.fx.sidechain_s1),
        ("synth2", ncs.fx.sidechain_s2),
        ("midi1", ncs.fx.sidechain_m1),
        ("midi2", ncs.fx.sidechain_m2),
    ]:
        source_name = _SC_SOURCE_REVERSE.get(sc_settings.source, "off")
        if source_name != "off" or sc_settings.depth > 0 or sc_settings.preset > 0:
            sc_dict: dict[str, object] = {
                "source": source_name,
                "attack": sc_settings.attack,
                "hold": sc_settings.hold,
                "decay": sc_settings.decay,
                "depth": sc_settings.depth,
            }
            if sc_settings.preset > 0:
                sc_dict["preset"] = sc_settings.preset
            fx.sidechain[track_name] = sc_dict

    # Preset indices
    fx.reverb_preset = ncs.project_settings.reverb_preset
    fx.delay_preset = ncs.project_settings.delay_preset

    return fx


def _read_song_order(ncs: NCSFile, pattern_names: dict[int, str]) -> list[str]:
    """Read the song order from the scene chain."""
    if not pattern_names:
        return []

    start = ncs.scene_chain.scene_chain_start
    end = ncs.scene_chain.end

    song_order: list[str] = []
    for scene_idx in range(start, end + 1):
        if scene_idx >= len(ncs.scenes):
            break
        scene = ncs.scenes[scene_idx]
        # byte[0] (end field) holds the pattern slot index
        slot_idx = scene.track_chains[0].end
        pat_name = pattern_names.get(slot_idx)
        if pat_name:
            song_order.append(pat_name)

    # If scene_chain is (0, 0) and slot 0 has a pattern, emit single-element list
    if not song_order and 0 in pattern_names and start == 0 and end == 0:
        scene = ncs.scenes[0]
        slot_idx = scene.track_chains[0].end
        if slot_idx in pattern_names:
            song_order.append(pattern_names[slot_idx])

    return song_order


def _song_data_to_dict(song: SongData) -> dict:
    """Convert a SongData to a plain JSON-serializable dict.

    Only includes non-None and non-empty fields for clean output.
    """
    d: dict = {
        "name": song.name,
        "bpm": song.bpm,
        "swing": song.swing,
        "color": song.color,
        "scale": {
            "root": song.scale_root,
            "type": song.scale_type,
        },
    }

    # Sounds
    if song.sounds:
        sounds: dict = {}
        for track_name, sc in song.sounds.items():
            s: dict = {}
            if sc.preset is not None:
                s["preset"] = sc.preset
            if sc.name is not None:
                s["name"] = sc.name
            if sc.params:
                s["params"] = sc.params
            if sc.mod_matrix:
                s["mod_matrix"] = sc.mod_matrix
            if sc.macros:
                s["macros"] = sc.macros
            if sc.sample is not None:
                s["sample"] = sc.sample
            if sc.level is not None:
                s["level"] = sc.level
            if sc.pitch is not None:
                s["pitch"] = sc.pitch
            if sc.decay is not None:
                s["decay"] = sc.decay
            if sc.distortion is not None:
                s["distortion"] = sc.distortion
            if sc.eq is not None:
                s["eq"] = sc.eq
            if sc.pan is not None:
                s["pan"] = sc.pan
            if s:
                sounds[track_name] = s
        if sounds:
            d["sounds"] = sounds

    # FX
    fx_dict: dict = {}
    if song.fx.reverb:
        fx_dict["reverb"] = song.fx.reverb
    if song.fx.delay:
        fx_dict["delay"] = song.fx.delay
    if song.fx.reverb_sends:
        fx_dict["reverb_sends"] = song.fx.reverb_sends
    if song.fx.delay_sends:
        fx_dict["delay_sends"] = song.fx.delay_sends
    if song.fx.sidechain:
        fx_dict["sidechain"] = song.fx.sidechain
    if song.fx.reverb_preset is not None:
        fx_dict["reverb_preset"] = song.fx.reverb_preset
    if song.fx.delay_preset is not None:
        fx_dict["delay_preset"] = song.fx.delay_preset
    if fx_dict:
        d["fx"] = fx_dict

    # Mixer
    if song.mixer:
        mixer_dict: dict = {}
        for track_name, mix_cfg in song.mixer.items():
            mixer_dict[track_name] = {"level": mix_cfg.level, "pan": mix_cfg.pan}
        d["mixer"] = mixer_dict

    # Patterns
    if song.patterns:
        patterns_dict: dict = {}
        for pat_name, pat_data in song.patterns.items():
            pat_d: dict = {"length": pat_data.length}
            if pat_data.tracks:
                pat_d["tracks"] = pat_data.tracks
            patterns_dict[pat_name] = pat_d
        d["patterns"] = patterns_dict

    # Song order
    if song.song:
        d["song"] = song.song

    return d


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
    ncs_pat: SynthPattern,
    steps_raw: dict,
    length: int,
    scale_root: int = 0,
    scale_type: int = 15,
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
        # Device plays: quantize(ncs_note, 0, type) + root - 12
        # So we store: quantize(midi_note - root, 0, type) + 12
        mask = 0
        for i, note in enumerate(step.notes[:NOTES_PER_STEP]):
            mask |= 1 << i
            c_relative = quantize_to_scale(note - scale_root, 0, scale_type)
            gate_ticks = max(1, min(96, round(step.gate * 6)))
            if step.tie:
                gate_ticks |= 0x80
            ncs_step.notes[i] = NCSNote(
                note_number=max(0, min(127, c_relative + 12)),
                gate=gate_ticks,
                delay=0,
                velocity=max(0, min(127, step.velocity)),
            )

        ncs_step.assigned_note_mask = mask
        ncs_step.probability = max(0, min(7, round(step.probability * 7)))

        # Catch common mistake: "params" on synth steps is not supported.
        # Synth p-locks use "macros" (macro number -> value) on steps,
        # or track-level "macros" (macro number -> {step -> value}).
        if isinstance(step_data, dict) and "params" in step_data:
            raise ValueError(
                f"Synth step {idx}: 'params' is not supported on synth steps. "
                f'Use \'macros\' instead (e.g. "macros": {{"1": 80}}) to p-lock '
                f"via macro knobs, or use track-level 'macros' for automation."
            )

        # Write macro locks (p-locks)
        macros = step_data.get("macros") if isinstance(step_data, dict) else None
        if macros:
            for macro_str, value in macros.items():
                macro_num = int(macro_str)
                if 1 <= macro_num <= 8:
                    if macro_num not in ncs_pat.macro_locks:
                        ncs_pat.macro_locks[macro_num] = {}
                    ncs_pat.macro_locks[macro_num][idx] = max(0, min(127, int(value)))


def _write_track_macros(
    ncs_pat: SynthPattern,
    track_data: dict,
    length: int,
) -> None:
    """Write track-level macro automation into an NCS SynthPattern.

    Track data may contain a "macros" dict:
        {"macros": {"5": {"0": 0, "1": 30, "2.5": 60, ...}}}

    Keys are macro numbers (1-8). Values are dicts mapping position to value.
    Position can be an integer step index or a float for micro-step resolution
    (e.g. "2.5" = step 2, halfway through).
    """
    track_macros = track_data.get("macros")
    if not track_macros:
        return

    for macro_str, positions in track_macros.items():
        macro_num = int(macro_str)
        if not (1 <= macro_num <= 8):
            continue
        if macro_num not in ncs_pat.macro_locks:
            ncs_pat.macro_locks[macro_num] = {}
        for pos_str, value in positions.items():
            pos = float(pos_str)
            if 0 <= pos < length:
                ncs_pat.macro_locks[macro_num][pos] = max(0, min(127, int(value)))


def _write_mixer_locks(
    ncs_pat: SynthPattern,
    track_data: dict,
    length: int,
) -> None:
    """Write track-level mixer/FX automation into an NCS SynthPattern.

    Track data may contain a "mixer" dict:
        {"mixer": {"level": {"0": 100, "8": 50}, "pan": {"0": 0, "15": 127}}}

    Keys are parameter names ("reverb_send", "delay_send", "level", "pan").
    Values are dicts mapping step position to value (0-127).
    Float positions supported for micro-step resolution.
    """
    mixer_data = track_data.get("mixer")
    if not mixer_data:
        return

    valid_params = {"reverb_send", "delay_send", "level", "pan"}
    for param_name, positions in mixer_data.items():
        if param_name not in valid_params:
            continue
        if param_name not in ncs_pat.mixer_locks:
            ncs_pat.mixer_locks[param_name] = {}
        for pos_str, value in positions.items():
            pos = float(pos_str)
            if 0 <= pos < length:
                ncs_pat.mixer_locks[param_name][pos] = max(0, min(127, int(value)))


def _write_drum_param_locks(
    ncs_pat: DrumPattern,
    track_data: dict,
    length: int,
) -> None:
    """Write track-level parameter automation into an NCS DrumPattern.

    Track data may contain a "params" dict:
        {"params": {"pitch": {"0": 30, "4": 90, ...}, "decay": {"0": 100}}}

    Keys are parameter names ("pitch", "decay", "distortion", "eq",
    "reverb_send", "delay_send", "level", "pan").
    Values are dicts mapping step position to value (0-127).
    Float positions supported for micro-step resolution.
    """
    track_params = track_data.get("params")
    if not track_params:
        return

    valid_params = {"pitch", "decay", "distortion", "eq", "reverb_send", "delay_send", "level", "pan"}
    for param_name, positions in track_params.items():
        if param_name not in valid_params:
            continue
        if param_name not in ncs_pat.param_locks:
            ncs_pat.param_locks[param_name] = {}
        for pos_str, value in positions.items():
            pos = float(pos_str)
            if 0 <= pos < length:
                ncs_pat.param_locks[param_name][pos] = max(0, min(127, int(value)))


def _write_drum_steps(
    ncs_pat: DrumPattern,
    steps_raw: dict,
    length: int,
    song: SongData,
    track_name: str,
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
        dist = sum(((params.get(k, preset[k]) - preset[k]) / _REVERB_RANGES[k]) ** 2 for k in _REVERB_RANGES)
        if dist < best_dist:
            best_idx, best_dist = idx, dist
    return best_idx


def _find_closest_delay(params: dict[str, int]) -> int:
    """Find the delay preset closest to the given parameter values."""
    best_idx, best_dist = 0, float("inf")
    for idx, preset in DELAY_PRESETS.items():
        dist = sum(((params.get(k, preset[k]) - preset[k]) / _DELAY_RANGES[k]) ** 2 for k in _DELAY_RANGES)
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
    _sc_track_map = {
        "synth1": "sidechain_s1",
        "synth2": "sidechain_s2",
        "midi1": "sidechain_m1",
        "midi2": "sidechain_m2",
    }
    for track_name, sc_data in fx.sidechain.items():
        attr = _sc_track_map.get(track_name)
        if attr is None:
            continue
        source_name = sc_data.get("source", "off")
        source_val = _SC_SOURCE.get(source_name, 4)
        preset = sc_data.get("preset", 0)
        # Auto-populate parameters from preset when not explicitly provided
        preset_defaults = _SC_PRESET_PARAMS.get(preset, (0, 50, 70, 0))
        sc = SidechainSettings(
            preset=preset,
            source=source_val,
            attack=sc_data.get("attack", preset_defaults[0]),
            hold=sc_data.get("hold", preset_defaults[1]),
            decay=sc_data.get("decay", preset_defaults[2]),
            depth=sc_data.get("depth", preset_defaults[3]),
        )
        setattr(ncs.fx, attr, sc)
