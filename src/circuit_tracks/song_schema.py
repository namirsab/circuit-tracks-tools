"""Pydantic v2 models for the Circuit Tracks song format.

These models provide strict validation and JSON Schema generation for the
``load_song`` MCP tool, ensuring the LLM sees an exact, unambiguous schema
instead of relying on prose docstrings.

The models are used only for *input validation* — after validation, the data is
converted to the existing ``SongData`` dataclass for downstream processing.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Literal types for constrained string values
# ---------------------------------------------------------------------------

ScaleRoot = Literal[
    "C",
    "C#",
    "Db",
    "D",
    "D#",
    "Eb",
    "E",
    "F",
    "F#",
    "Gb",
    "G",
    "G#",
    "Ab",
    "A",
    "A#",
    "Bb",
    "B",
]

ScaleType = Literal[
    "natural minor",
    "minor",
    "major",
    "dorian",
    "phrygian",
    "mixolydian",
    "melodic minor",
    "harmonic minor",
    "bebop dorian",
    "blues",
    "minor pentatonic",
    "hungarian minor",
    "ukranian dorian",
    "marva",
    "todi",
    "whole tone",
    "chromatic",
]

SynthPreset = Literal["pad", "bass", "lead", "pluck"]

TrackName = Literal[
    "synth1",
    "synth2",
    "drum1",
    "drum2",
    "drum3",
    "drum4",
    "midi1",
    "midi2",
]

SynthTrackName = Literal["synth1", "synth2", "midi1", "midi2"]
DrumTrackName = Literal["drum1", "drum2", "drum3", "drum4"]

SendTrackName = Literal[
    "synth1",
    "synth2",
    "drum1",
    "drum2",
    "drum3",
    "drum4",
    "midi1",
    "midi2",
]

SidechainSource = Literal["drum1", "drum2", "drum3", "drum4", "off"]

MacroNumber = Literal["1", "2", "3", "4", "5", "6", "7", "8"]

MixerAutomationParam = Literal["reverb_send", "delay_send", "level", "pan"]
DrumAutomationParam = Literal[
    "pitch",
    "decay",
    "distortion",
    "eq",
    "reverb_send",
    "delay_send",
    "level",
    "pan",
]

# Mod matrix source/destination names (space-separated, NOT snake_case).
ModMatrixSource = Literal[
    "direct",
    "velocity",
    "keyboard",
    "LFO 1+",
    "LFO 1+/-",
    "LFO 2+",
    "LFO 2+/-",
    "env amp",
    "env filter",
    "env 3",
]

ModMatrixDestination = Literal[
    "osc 1 & 2 pitch",
    "osc 1 pitch",
    "osc 2 pitch",
    "osc 1 v-sync",
    "osc 2 v-sync",
    "osc 1 pulse width / index",
    "osc 2 pulse width / index",
    "osc 1 level",
    "osc 2 level",
    "noise level",
    "ring modulation 1*2 level",
    "filter drive amount",
    "filter frequency",
    "filter resonance",
    "LFO 1 rate",
    "LFO 2 rate",
    "amp envelope decay",
    "filter envelope decay",
]

# Type alias for step-position -> value automation dicts.
# Keys are step indices as strings (e.g. "0", "8") or float strings for
# micro-step resolution (e.g. "2.5").
AutomationLane = dict[str, Annotated[int, Field(ge=0, le=127)]]

# ---------------------------------------------------------------------------
# Scale
# ---------------------------------------------------------------------------


class ScaleConfig(BaseModel):
    """Musical scale applied to synth/MIDI note quantization."""

    root: ScaleRoot = "C"
    type: ScaleType = "chromatic"


# ---------------------------------------------------------------------------
# Mod matrix
# ---------------------------------------------------------------------------


class ModMatrixEntry(BaseModel):
    """A single mod-matrix routing slot."""

    source1: ModMatrixSource | int = "direct"
    source2: ModMatrixSource | int = "direct"
    dest: ModMatrixDestination | int = Field(
        ..., description="Destination parameter (use space-separated names, NOT snake_case)."
    )
    # Alias so callers can use "destination" too (common in existing songs).
    destination: ModMatrixDestination | int | None = Field(
        default=None,
        exclude=True,
        description="Alias for 'dest'. Either 'dest' or 'destination' may be used.",
    )
    depth: int = Field(
        default=0,
        ge=-64,
        le=63,
        description="Signed depth: -64 to +63. 0 = no modulation.",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalise_dest(cls, values: Any) -> Any:
        if isinstance(values, dict):
            # Allow "destination" as alias for "dest"
            if "destination" in values and "dest" not in values:
                values["dest"] = values.pop("destination")
            # Allow "source" as alias for "source1"
            if "source" in values and "source1" not in values:
                values["source1"] = values.pop("source")
        return values


# ---------------------------------------------------------------------------
# Macros (sound-level configuration)
# ---------------------------------------------------------------------------


class MacroTarget(BaseModel):
    """A single destination routed to a macro knob."""

    dest: str | int = Field(
        ...,
        description=(
            "Parameter name (e.g. 'filter_frequency') or macro destination "
            "index (0-70). Use get_parameter_reference for the full list."
        ),
    )
    start: int = Field(default=0, ge=0, le=127, description="Value when knob is at 0.")
    end: int = Field(default=127, ge=0, le=127, description="Value when knob is at 127.")
    depth: int = Field(default=127, ge=0, le=127, description="Modulation depth. 127=full, 64=none.")


class MacroConfig(BaseModel):
    """Configuration for one macro knob (1-8)."""

    targets: list[MacroTarget]
    position: int = Field(default=0, ge=0, le=127, description="Initial knob position.")


# ---------------------------------------------------------------------------
# Sounds
# ---------------------------------------------------------------------------


class SynthSoundConfig(BaseModel):
    """Synth engine patch configuration (synth1, synth2)."""

    preset: SynthPreset | None = Field(
        default=None,
        description="Base preset to start from before applying params.",
    )
    name: str | None = Field(default=None, max_length=16, description="Patch name.")
    params: dict[str, int] | None = Field(
        default=None,
        description="Synth parameter name -> value (0-127). Use get_parameter_reference for names.",
    )
    mod_matrix: list[ModMatrixEntry] | None = Field(
        default=None,
        description="Mod matrix routing slots.",
    )
    macros: dict[MacroNumber, MacroConfig] | None = Field(
        default=None,
        description="Macro knob definitions. Keys are '1' through '8'.",
    )


class DrumSoundConfig(BaseModel):
    """Drum track sound configuration."""

    sample: int | None = Field(default=None, ge=0, le=63, description="Sample/patch index.")
    level: int | None = Field(default=None, ge=0, le=127)
    pitch: int | None = Field(default=None, ge=0, le=127)
    decay: int | None = Field(default=None, ge=0, le=127)
    distortion: int | None = Field(default=None, ge=0, le=127)
    eq: int | None = Field(default=None, ge=0, le=127)
    pan: int | None = Field(default=None, ge=0, le=127)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


class SynthStepConfig(BaseModel):
    """A single step in a synth or MIDI track.

    Only these fields are allowed — no others. Use 'macros' for p-locks, NOT 'params' or 'p-locks'.
    """

    model_config = {"extra": "forbid"}

    note: int | None = Field(
        default=None,
        ge=0,
        le=127,
        description="MIDI note number. Mutually exclusive with 'notes'.",
    )
    notes: list[Annotated[int, Field(ge=0, le=127)]] | None = Field(
        default=None,
        description="Multiple MIDI notes for polyphony. Mutually exclusive with 'note'.",
    )
    velocity: int | None = Field(default=None, ge=0, le=127)
    gate: float | None = Field(
        default=None,
        ge=0.0,
        le=16.0,
        description="Gate length in steps (e.g. 0.5 = half step, 2.0 = two steps). Max 16.",
    )
    tie: bool = Field(
        default=False,
        description="Tie forward — sustain this note into the next triggered step.",
    )
    enabled: bool = True
    probability: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Playback probability, 0.0 to 1.0.",
    )
    macros: dict[MacroNumber, Annotated[int, Field(ge=0, le=127)]] | None = Field(
        default=None,
        description=(
            "Per-step macro p-locks. Keys are macro numbers '1'-'8', "
            "values are 0-127. These lock the macro knob position for this step."
        ),
    )


class DrumStepConfig(BaseModel):
    """A single step in a drum track."""

    model_config = {"extra": "forbid"}

    velocity: int | None = Field(default=None, ge=0, le=127)
    enabled: bool = True
    probability: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    sample: int | None = Field(
        default=None,
        ge=0,
        le=63,
        description="Per-step sample override.",
    )
    micro_step: int | None = Field(
        default=None,
        ge=0,
        le=5,
        description="Micro-step offset within the step (0-5).",
    )


# ---------------------------------------------------------------------------
# Tracks (within a pattern)
# ---------------------------------------------------------------------------


class SynthTrackConfig(BaseModel):
    """Track data for a synth or MIDI track within a pattern."""

    model_config = {"extra": "forbid"}

    steps: dict[str, SynthStepConfig] = Field(
        default_factory=dict,
        description="Step index (as string, e.g. '0', '4') -> step data.",
    )
    macros: dict[MacroNumber, AutomationLane] | None = Field(
        default=None,
        description=(
            "Track-level macro automation. Keys are macro numbers '1'-'8'. "
            "Values are {step_position: value} dicts. Float positions supported "
            "for micro-step resolution (e.g. '2.5')."
        ),
    )
    mixer: dict[MixerAutomationParam, AutomationLane] | None = Field(
        default=None,
        description=(
            "Track-level mixer automation. Keys: 'reverb_send', 'delay_send', "
            "'level', 'pan'. Values are {step_position: value} dicts."
        ),
    )


class DrumTrackConfig(BaseModel):
    """Track data for a drum track within a pattern."""

    model_config = {"extra": "forbid"}

    steps: dict[str, DrumStepConfig] = Field(
        default_factory=dict,
        description="Step index (as string) -> step data.",
    )
    params: dict[DrumAutomationParam, AutomationLane] | None = Field(
        default=None,
        description=(
            "Track-level parameter automation. Keys: 'pitch', 'decay', "
            "'distortion', 'eq', 'reverb_send', 'delay_send', 'level', 'pan'. "
            "Values are {step_position: value} dicts."
        ),
    )


# ---------------------------------------------------------------------------
# Pattern
# ---------------------------------------------------------------------------

_SYNTH_TRACKS = {"synth1", "synth2", "midi1", "midi2"}
_DRUM_TRACKS = {"drum1", "drum2", "drum3", "drum4"}


class PatternConfig(BaseModel):
    """A named pattern containing track data."""

    length: int = Field(default=16, ge=1, le=32, description="Pattern length in steps.")
    tracks: dict[TrackName, SynthTrackConfig | DrumTrackConfig] = Field(
        default_factory=dict,
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_tracks(cls, values: Any) -> Any:
        """Validate and coerce tracks to the correct type based on track name."""
        if not isinstance(values, dict):
            return values
        tracks = values.get("tracks", {})
        if not isinstance(tracks, dict):
            return values

        coerced: dict[str, Any] = {}
        for track_name, track_data in tracks.items():
            if not isinstance(track_data, dict):
                coerced[track_name] = track_data
                continue

            # Already a model instance — skip
            if isinstance(track_data, (SynthTrackConfig, DrumTrackConfig)):
                coerced[track_name] = track_data
                continue

            if track_name in _SYNTH_TRACKS:
                coerced[track_name] = SynthTrackConfig.model_validate(track_data)
            elif track_name in _DRUM_TRACKS:
                coerced[track_name] = DrumTrackConfig.model_validate(track_data)
            else:
                coerced[track_name] = track_data  # will fail TrackName validation

        values["tracks"] = coerced
        return values


# ---------------------------------------------------------------------------
# FX
# ---------------------------------------------------------------------------


class ReverbConfig(BaseModel):
    """Reverb engine parameters."""

    type: int | None = Field(default=None, ge=0, le=5)
    decay: int | None = Field(default=None, ge=0, le=127)
    damping: int | None = Field(default=None, ge=0, le=127)


class DelayConfig(BaseModel):
    """Delay engine parameters."""

    time: int | None = Field(default=None, ge=0, le=127)
    sync: int | None = Field(default=None, ge=0, le=35)
    feedback: int | None = Field(default=None, ge=0, le=127)
    width: int | None = Field(default=None, ge=0, le=127)
    lr_ratio: int | None = Field(default=None, ge=0, le=12)
    slew: int | None = Field(default=None, ge=0, le=127)


class SidechainConfig(BaseModel):
    """Sidechain compressor settings for a synth or MIDI track."""

    preset: int | None = Field(
        default=None,
        ge=0,
        le=7,
        description="Sidechain preset (1-7). Required to activate sidechain on hardware. 0 or omit = OFF.",
    )
    source: SidechainSource = "off"
    attack: int | None = Field(default=None, ge=0, le=127)
    hold: int | None = Field(default=None, ge=0, le=127)
    decay: int | None = Field(default=None, ge=0, le=127)
    depth: int | None = Field(default=None, ge=0, le=127)


class FXConfig(BaseModel):
    """Global FX configuration."""

    reverb: ReverbConfig | None = None
    delay: DelayConfig | None = None
    reverb_preset: str | int | None = Field(
        default=None,
        description="Reverb preset by name or index (0-7).",
    )
    delay_preset: str | int | None = Field(
        default=None,
        description="Delay preset by name or index (0-15).",
    )
    reverb_sends: dict[SendTrackName, Annotated[int, Field(ge=0, le=127)]] | None = Field(
        default=None,
        description="Per-track reverb send level.",
    )
    delay_sends: dict[SendTrackName, Annotated[int, Field(ge=0, le=127)]] | None = Field(
        default=None,
        description="Per-track delay send level.",
    )
    sidechain: dict[Literal["synth1", "synth2", "midi1", "midi2"], SidechainConfig] | None = None


# ---------------------------------------------------------------------------
# Mixer
# ---------------------------------------------------------------------------


class MixerTrackConfig(BaseModel):
    """Mixer settings for a synth track."""

    level: int = Field(default=100, ge=0, le=127)
    pan: int = Field(default=64, ge=0, le=127)


# ---------------------------------------------------------------------------
# Top-level song
# ---------------------------------------------------------------------------


class SongSchema(BaseModel):
    """Complete song description for the Circuit Tracks.

    Defines patterns, sounds, FX, mixer, and song structure.
    Use ``get_parameter_reference`` to look up valid synth parameter names,
    mod matrix sources/destinations, and macro destinations.
    """

    name: str = Field(default="Song", description="Song/project name.")
    bpm: int = Field(default=120, ge=40, le=240, description="Tempo in BPM.")
    swing: int = Field(default=50, ge=20, le=80, description="Swing amount.")
    color: int = Field(default=8, ge=0, le=13, description="Pattern color index.")
    scale: ScaleConfig = Field(default_factory=ScaleConfig)
    sounds: dict[TrackName, SynthSoundConfig | DrumSoundConfig] | None = Field(
        default=None,
        description="Per-track sound/patch configuration.",
    )
    fx: FXConfig | None = None
    mixer: dict[Literal["synth1", "synth2"], MixerTrackConfig] | None = None
    patterns: dict[str, PatternConfig] = Field(
        ...,
        min_length=1,
        description="Named patterns. At least one required.",
    )
    song: list[str] | None = Field(
        default=None,
        max_length=16,
        description="Ordered list of pattern names for playback (max 16 scenes).",
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_sounds(cls, values: Any) -> Any:
        """Coerce sound entries to the right type based on track name."""
        if not isinstance(values, dict):
            return values
        sounds = values.get("sounds")
        if not sounds or not isinstance(sounds, dict):
            return values

        coerced: dict[str, Any] = {}
        for track_name, sound_data in sounds.items():
            if not isinstance(sound_data, dict):
                coerced[track_name] = sound_data
                continue
            if isinstance(sound_data, (SynthSoundConfig, DrumSoundConfig)):
                coerced[track_name] = sound_data
                continue

            if track_name in _SYNTH_TRACKS:
                coerced[track_name] = SynthSoundConfig.model_validate(sound_data)
            elif track_name in _DRUM_TRACKS:
                coerced[track_name] = DrumSoundConfig.model_validate(sound_data)
            else:
                coerced[track_name] = sound_data

        values["sounds"] = coerced
        return values

    @model_validator(mode="after")
    def _validate_song_refs(self) -> SongSchema:
        """Validate that song references only defined patterns."""
        if self.song:
            for pat_name in self.song:
                if pat_name not in self.patterns:
                    raise ValueError(
                        f"Song references unknown pattern '{pat_name}'. Defined patterns: {list(self.patterns.keys())}"
                    )
        unique = set(self.song) if self.song else set(self.patterns.keys())
        if len(unique) > 8:
            raise ValueError(f"Too many unique patterns ({len(unique)}). Circuit Tracks supports max 8.")
        return self


# ---------------------------------------------------------------------------
# configure_macro tool input
# ---------------------------------------------------------------------------


class MacroTargetInput(BaseModel):
    """A single parameter target for a macro knob (configure_macro tool)."""

    model_config = {"extra": "forbid"}

    param: str = Field(
        ...,
        description="Exact synth parameter name (e.g. 'filter_frequency'). Use get_parameter_reference.",
    )
    min: int = Field(default=0, ge=0, le=127, description="Value when knob is at 0.")
    max: int = Field(default=127, ge=0, le=127, description="Value when knob is at 127. Use min > max to invert.")


# ---------------------------------------------------------------------------
# Sequencer step/track/pattern models (set_pattern / set_track tools)
# ---------------------------------------------------------------------------


class SequencerStepConfig(BaseModel):
    """A single step for the live sequencer (set_pattern / set_track tools).
    Drum tracks should omit note and gate — only velocity matters."""

    model_config = {"extra": "forbid"}

    note: int | None = Field(
        default=None,
        ge=0,
        le=127,
        description="MIDI note number. Ignored for drum tracks.",
    )
    notes: list[Annotated[int, Field(ge=0, le=127)]] | None = Field(
        default=None,
        description="Multiple MIDI notes for chords. Ignored for drum tracks.",
    )
    velocity: int | None = Field(default=None, ge=0, le=127)
    gate: float | None = Field(
        default=None,
        ge=0.0,
        le=16.0,
        description="Gate length in steps (e.g. 0.5 = half step, 2.0 = two steps). Max 16. NOT used for drum tracks.",
    )
    tie: bool = Field(
        default=False,
        description="Tie forward — sustain this note into the next triggered step.",
    )
    enabled: bool = True
    probability: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    sample: int | None = Field(
        default=None,
        ge=0,
        le=63,
        description="Drum sample override (drum tracks only).",
    )


class SequencerTrackConfig(BaseModel):
    """Track data for the live sequencer (set_pattern tool)."""

    model_config = {"extra": "forbid"}

    steps: dict[str, SequencerStepConfig] = Field(
        default_factory=dict,
        description="Step index (as string, e.g. '0', '4') -> step data.",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_song_json_schema() -> dict:
    """Return the JSON Schema for the song format."""
    return SongSchema.model_json_schema()
