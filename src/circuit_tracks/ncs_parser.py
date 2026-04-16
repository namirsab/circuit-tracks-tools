"""
NCS file format parser and writer for Novation Circuit Tracks projects.

File format reverse-engineered from binary analysis + WASM validator disassembly.
See plans/ncs-format-reverse-engineering.md for full specification.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

# Constants
NCS_MAGIC = b"USER"
NCS_FILE_SIZE = 160780
STEPS_PER_PATTERN = 32
NOTES_PER_STEP = 6
SYNTH_STEP_SIZE = 4 + NOTES_PER_STEP * 4  # 28 bytes
SYNTH_STEP_DATA_SIZE = STEPS_PER_PATTERN * SYNTH_STEP_SIZE  # 896 bytes
PATTERN_SETTINGS_SIZE = 40
DRUM_STEP_REGION_SIZE = 16 + 4 * 32  # header(16) + 4 rows of 32 = 144 bytes
NUM_SYNTH_TRACKS = 2
NUM_DRUM_TRACKS = 4
NUM_MIDI_TRACKS = 2
NUM_TRACKS = NUM_SYNTH_TRACKS + NUM_MIDI_TRACKS + NUM_DRUM_TRACKS  # 8
PATTERNS_PER_TRACK = 8
NUM_SCENES = 16
DEFAULT_NOTE_VELOCITY = 96  # 0x60
DEFAULT_PROBABILITY = 7  # 100%
DEFAULT_DRUM_CHOICE = 0xFF  # no sample flip

# Offsets
TIMING_OFFSET = 0x34
SCENES_CHAINS_OFFSET = 0x39
PATTERN_DATA_PREFIX_OFFSET = 0x2E0

# Scenes/chains region layout (relative to SCENES_CHAINS_OFFSET):
# scenes[16] × 40 bytes = 640 bytes at +0
# scene state (8 bytes) at +640
# sceneChain (4 bytes) at +648
# patternChains[8] × 4 bytes at +652
_SCENE_SIZE = 40
_SCENE_TRACK_OFFSET = 8  # track entries start at byte 8 within each scene
_SCENE_CHAIN_ENTRY_SIZE = 4  # {end, start, padding, padding}
_SCENES_TOTAL = NUM_SCENES * _SCENE_SIZE  # 640
_SCENE_STATE_OFFSET = _SCENES_TOTAL  # +640
_SCENE_STATE_SIZE = 8
_SCENE_CHAIN_OFFSET = _SCENE_STATE_OFFSET + _SCENE_STATE_SIZE  # +648
_PATTERN_CHAINS_OFFSET = _SCENE_CHAIN_OFFSET + _SCENE_CHAIN_ENTRY_SIZE  # +652

# The settings/metadata offsets for all 64 pattern blocks
_METADATA_OFFSETS = [
    0x664, 0x130C, 0x1FB4, 0x2C5C, 0x3904, 0x45AC, 0x5254, 0x5EFC,
    0x6BA4, 0x784C, 0x84F4, 0x919C, 0x9E44, 0xAAEC, 0xB794, 0xC43C,
    0xCDF4, 0xD49C, 0xDB44, 0xE1EC, 0xE894, 0xEF3C, 0xF5E4, 0xFC8C,
    0x10334, 0x109DC, 0x11084, 0x1172C, 0x11DD4, 0x1247C, 0x12B24, 0x131CC,
    0x13874, 0x13F1C, 0x145C4, 0x14C6C, 0x15314, 0x159BC, 0x16064, 0x1670C,
    0x16DB4, 0x1745C, 0x17B04, 0x181AC, 0x18854, 0x18EFC, 0x195A4, 0x19C4C,
    0x1A5FC, 0x1B2A4, 0x1BF4C, 0x1CBF4, 0x1D89C, 0x1E544, 0x1F1EC, 0x1FE94,
    0x20B3C, 0x217E4, 0x2248C, 0x23134, 0x23DDC, 0x24A84, 0x2572C, 0x263D4,
]

TAIL_OFFSET = 0x26CFC
_SYNTH_BLOCK_COUNT = NUM_SYNTH_TRACKS * PATTERNS_PER_TRACK  # 16
_DRUM_BLOCK_COUNT = NUM_DRUM_TRACKS * PATTERNS_PER_TRACK  # 32
_DRUM_BLOCK_START = _SYNTH_BLOCK_COUNT  # 16
_DRUM_BLOCK_END = _DRUM_BLOCK_START + _DRUM_BLOCK_COUNT  # 48

# Tail offsets (relative to TAIL_OFFSET = 0x26CFC)
_TAIL_SCALE_ROOT_OFFSET = 16  # 0x26D0C
_TAIL_SCALE_TYPE_OFFSET = 17  # 0x26D0D
_TAIL_DELAY_PRESET_OFFSET = 18  # 0x26D0E
_TAIL_REVERB_PRESET_OFFSET = 19  # 0x26D0F

# FX offsets (relative to TAIL_OFFSET)
_REVERB_SENDS_OFFSET = 748   # 8 bytes: S1,S2,D1,D2,D3,D4,M1,M2
_REVERB_PARAMS_OFFSET = 756  # 3 bytes: type, decay, damping
_DELAY_SENDS_OFFSET = 764    # 8 bytes: S1,S2,D1,D2,D3,D4,M1,M2
_DELAY_PARAMS_OFFSET = 772   # 6 bytes: time, sync, feedback, width, lr_ratio, slew
_FX_BYPASS_OFFSET = 779      # 1 byte: 0=on, 1=bypassed
_SIDECHAIN_S1_OFFSET = 780   # 5 bytes: source, attack, hold, decay, depth
_SIDECHAIN_S2_OFFSET = 785   # 5 bytes: source, attack, hold, decay, depth
_SIDECHAIN_M1_OFFSET = 790   # 5 bytes: source, attack, hold, decay, depth
_SIDECHAIN_M2_OFFSET = 795   # 5 bytes: source, attack, hold, decay, depth

# Sidechain preset index byte positions (0=OFF, 1-7)
# Pattern: every 4 bytes (byte 3, 7, 11, ...)
_SC_PRESET_S1_DRUM_HDR_BYTE = 3    # in drum_patterns[0].raw_header
_SC_PRESET_S2_DRUM_HDR_BYTE = 11   # in drum_patterns[0].raw_header
_SC_PRESET_M1_PREAMBLE_BYTE = 3    # in tail preamble
_SC_PRESET_M2_PREAMBLE_BYTE = 11   # in tail preamble
_MIXER_LEVELS_OFFSET = 800   # 4 bytes: S1, S2, M1, M2
_MIXER_PANS_OFFSET = 804     # 4 bytes: S1, S2, M1, M2

# Synth patches embedded in the tail
_SYNTH1_PATCH_OFFSET = 24    # 340 bytes (full patch per Programmer's Reference)
_SYNTH2_PATCH_OFFSET = 364   # 340 bytes
_PATCH_SIZE = 340

# Drum track configs (4 × 11 bytes)
_DRUM_CONFIGS_OFFSET = 704   # 44 bytes total
_DRUM_CONFIG_SIZE = 11       # per drum track

# Track index constants for send arrays
TRACK_S1 = 0
TRACK_S2 = 1
TRACK_D1 = 2
TRACK_D2 = 3
TRACK_D3 = 4
TRACK_D4 = 5
TRACK_M1 = 6
TRACK_M2 = 7


def _is_drum_block(block_idx: int) -> bool:
    return _DRUM_BLOCK_START <= block_idx < _DRUM_BLOCK_END


def _step_data_start(block_idx: int) -> int:
    """Where step data begins for a given block."""
    meta = _METADATA_OFFSETS[block_idx]
    if _is_drum_block(block_idx):
        return meta - DRUM_STEP_REGION_SIZE
    return meta - SYNTH_STEP_DATA_SIZE


# --- Data structures ---


@dataclass
class NCSNote:
    """A single note within a synth/MIDI step (4 bytes)."""

    note_number: int = 0
    gate: int = 0  # micro-ticks (6 per step)
    delay: int = 0  # micro-step offset 0-5
    velocity: int = DEFAULT_NOTE_VELOCITY

    def to_bytes(self) -> bytes:
        return bytes([self.note_number, self.gate, self.delay, self.velocity])

    @classmethod
    def from_bytes(cls, data: bytes) -> NCSNote:
        return cls(note_number=data[0], gate=data[1], delay=data[2], velocity=data[3])


@dataclass
class SynthStep:
    """A single step in a synth/MIDI pattern (28 bytes)."""

    assigned_note_mask: int = 0
    probability: int = DEFAULT_PROBABILITY  # 0-7 (7=100%)
    reserved: bytes = field(default_factory=lambda: b"\x00\x00")
    notes: list[NCSNote] = field(
        default_factory=lambda: [NCSNote() for _ in range(NOTES_PER_STEP)]
    )

    @property
    def is_active(self) -> bool:
        return self.assigned_note_mask != 0

    @property
    def active_notes(self) -> list[NCSNote]:
        return [
            self.notes[i]
            for i in range(NOTES_PER_STEP)
            if self.assigned_note_mask & (1 << i)
        ]

    def to_bytes(self) -> bytes:
        result = bytes([self.assigned_note_mask, self.probability]) + self.reserved
        for note in self.notes:
            result += note.to_bytes()
        return result

    @classmethod
    def from_bytes(cls, data: bytes) -> SynthStep:
        notes = [
            NCSNote.from_bytes(data[4 + i * 4 : 4 + (i + 1) * 4])
            for i in range(NOTES_PER_STEP)
        ]
        return cls(
            assigned_note_mask=data[0], probability=data[1],
            reserved=data[2:4], notes=notes,
        )


@dataclass
class PatternSettings:
    """Per-pattern playback settings (40 bytes)."""

    playback_end: int = 15
    playback_start: int = 0
    sync_rate: int = 3
    playback_direction: int = 0  # 0=Fwd, 1=Rev, 2=PingPong, 3=Random
    reserved: bytes = field(default_factory=lambda: bytes(36))

    def to_bytes(self) -> bytes:
        return (
            bytes([self.playback_end, self.playback_start, self.sync_rate, self.playback_direction])
            + self.reserved
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> PatternSettings:
        return cls(
            playback_end=data[0], playback_start=data[1],
            sync_rate=data[2], playback_direction=data[3],
            reserved=data[4:40],
        )


AUTOMATION_REGION_SIZE = 2304  # 8 macros × 6 micro-ticks × 32 steps + mixer/FX lanes
DRUM_AUTOMATION_REGION_SIZE = 1520  # 4 params × 6 micro-ticks × 32 steps + padding
AUTOMATION_LANES_PER_MACRO = 6  # 6 micro-ticks per step

# Drum automation slot -> parameter name mapping
DRUM_AUTOMATION_PARAMS = {
    0: "pitch",
    1: "decay",
    2: "distortion",
    3: "eq",
    4: "reverb_send",
    5: "delay_send",
    6: "level",
    7: "pan",
}

# Synth/MIDI mixer automation slots (lanes 48-71, after the 8 macro slots)
MIXER_AUTOMATION_PARAMS = {
    8: "reverb_send",   # lanes 48-53
    9: "delay_send",    # lanes 54-59
    10: "level",        # lanes 60-65
    11: "pan",          # lanes 66-71
}


@dataclass
class SynthPattern:
    """A synth or MIDI pattern: 32 steps + settings + raw surrounding data."""

    steps: list[SynthStep] = field(
        default_factory=lambda: [SynthStep() for _ in range(STEPS_PER_PATTERN)]
    )
    settings: PatternSettings = field(default_factory=PatternSettings)
    pre_data: bytes = b""
    post_data: bytes = b""
    macro_locks: dict[int, dict[float, int]] = field(default_factory=dict)
    """Per-step macro parameter locks.

    Maps macro number (1-8) to {position: value (0-127)}.
    Position can be an int (step index 0-31) or a float for micro-step
    resolution (e.g. 2.5 = step 2, micro-tick 3 out of 6).

    The automation region is a continuous 192-position buffer per macro
    (6 micro-ticks × 32 steps). Position N.F maps to flat index
    N * positions_per_step + round(F * positions_per_step).

    Note: Automation data is stored in the NEXT block's pre_data region
    in the NCS binary (2304 bytes between block N settings and block N+1
    step data).
    """
    mixer_locks: dict[str, dict[float, int]] = field(default_factory=dict)
    """Per-step mixer/FX automation locks.

    Maps parameter name ("reverb_send", "delay_send", "level", "pan") to
    {position: value (0-127)}. Same position format as macro_locks.
    Stored in lanes 48-71 of the automation region (slots 8-11).
    """


@dataclass
class DrumStep:
    """A single step in a drum pattern."""

    active: bool = False
    velocity: int = 0
    probability: int = DEFAULT_PROBABILITY
    drum_choice: int = DEFAULT_DRUM_CHOICE


@dataclass
class DrumPattern:
    """A drum pattern: 32 steps + settings + raw surrounding data."""

    steps: list[DrumStep] = field(
        default_factory=lambda: [DrumStep() for _ in range(STEPS_PER_PATTERN)]
    )
    settings: PatternSettings = field(default_factory=PatternSettings)
    raw_header: bytes = b""
    pre_data: bytes = b""
    post_data: bytes = b""
    param_locks: dict[str, dict[float, int]] = field(default_factory=dict)
    """Per-step parameter locks for drum tracks.

    Maps parameter name ("pitch", "decay", "distortion", "eq") to
    {position: value (0-127)}. Position can be int (step) or float
    (micro-step). Same format as SynthPattern.macro_locks.
    """


@dataclass
class NCSHeader:
    """NCS file header."""

    signature: bytes = NCS_MAGIC
    total_session_size: int = NCS_FILE_SIZE
    session_colour: int = 1  # Must be 0 or 1; purpose unknown
    color: int = 8  # Project LED color index (0-13). 8=Green (default)
    name: str = ""
    unknown_30: bytes = field(default_factory=lambda: bytes(4))


@dataclass
class NCSTimingSection:
    """Timing settings."""

    tempo: int = 120
    swing: int = 50
    swing_sync_rate: int = 3
    spare1: int = 0
    spare2: int = 0

    def to_bytes(self) -> bytes:
        return bytes([self.tempo, self.swing, self.swing_sync_rate, self.spare1, self.spare2])

    @classmethod
    def from_bytes(cls, data: bytes) -> NCSTimingSection:
        return cls(tempo=data[0], swing=data[1], swing_sync_rate=data[2], spare1=data[3], spare2=data[4])


@dataclass
class ChainEntry:
    """A 4-byte chain entry used for scene tracks, pattern chains, and scene chain.

    Byte layout for scene track entries and pattern chains:
      byte[0] = chain end index (0-based, inclusive)
      byte[1] = 0
      byte[2] = 0
      byte[3] = chain start index (0-based)

    For scene chain entries, byte[1] is the start (always 0) and byte[3]
    has a different meaning.  Use ``scene_chain_start`` for scene chains.
    """

    end: int = 0    # byte[0]: end index
    start: int = 0  # byte[3]: start index (for scene tracks / pattern chains)
    _byte1: int = 0  # byte[1]: scene chain start (always 0)
    _byte2: int = 0  # byte[2]: always 0

    @property
    def scene_chain_start(self) -> int:
        """Start index for scene chain entries (byte[1])."""
        return self._byte1

    @scene_chain_start.setter
    def scene_chain_start(self, value: int) -> None:
        self._byte1 = value

    def to_bytes(self) -> bytes:
        return bytes([self.end, self._byte1, self._byte2, self.start])

    @classmethod
    def from_bytes(cls, data: bytes) -> ChainEntry:
        return cls(end=data[0], start=data[3], _byte1=data[1], _byte2=data[2])


@dataclass
class Scene:
    """A scene: header + 8 track pattern chain assignments."""

    header: bytes = field(default_factory=lambda: bytes(_SCENE_TRACK_OFFSET))
    track_chains: list[ChainEntry] = field(
        default_factory=lambda: [ChainEntry() for _ in range(NUM_TRACKS)]
    )

    def to_bytes(self) -> bytes:
        result = self.header
        for chain in self.track_chains:
            result += chain.to_bytes()
        return result

    @classmethod
    def from_bytes(cls, data: bytes) -> Scene:
        header = data[:_SCENE_TRACK_OFFSET]
        chains = [
            ChainEntry.from_bytes(data[_SCENE_TRACK_OFFSET + i * 4 : _SCENE_TRACK_OFFSET + (i + 1) * 4])
            for i in range(NUM_TRACKS)
        ]
        return cls(header=header, track_chains=chains)


@dataclass
class SidechainSettings:
    """Sidechain compressor settings for a synth or MIDI track."""

    preset: int = 0    # 0=OFF, 1-7 = sidechain preset index
    source: int = 0    # 0=D1, 1=D2, 2=D3, 3=D4, 4=OFF
    attack: int = 0    # 0-127
    hold: int = 50     # 0-127, default 50
    decay: int = 70    # 0-127, default 70
    depth: int = 0     # 0-127


@dataclass
class FXSettings:
    """Project-level FX settings."""

    # Reverb
    reverb_sends: list[int] = field(default_factory=lambda: [0] * 8)  # S1,S2,D1,D2,D3,D4,M1,M2
    reverb_type: int = 2       # internal type param (changes with preset)
    reverb_decay: int = 64     # 0-127
    reverb_damping: int = 64   # 0-127

    # Delay
    delay_sends: list[int] = field(default_factory=lambda: [0] * 8)   # S1,S2,D1,D2,D3,D4,M1,M2
    delay_time: int = 64       # 0-127
    delay_sync: int = 20       # 0-35
    delay_feedback: int = 64   # 0-127
    delay_width: int = 127     # 0-127
    delay_lr_ratio: int = 4    # 0-12
    delay_slew: int = 5        # 0-127

    # FX bypass
    fx_bypass: bool = False    # True = FX disabled

    # Sidechain (4 tracks: S1, S2, M1, M2)
    sidechain_s1: SidechainSettings = field(default_factory=SidechainSettings)
    sidechain_s2: SidechainSettings = field(default_factory=SidechainSettings)
    sidechain_m1: SidechainSettings = field(default_factory=SidechainSettings)
    sidechain_m2: SidechainSettings = field(default_factory=SidechainSettings)

    # Mixer (synth/MIDI tracks — drum levels/pans are in per-drum config)
    mixer_levels: list[int] = field(default_factory=lambda: [100, 100, 100, 100])  # S1,S2,M1,M2
    mixer_pans: list[int] = field(default_factory=lambda: [64, 64, 64, 64])        # S1,S2,M1,M2


@dataclass
class DrumTrackConfig:
    """Per-drum-track settings stored in the NCS tail (11 bytes each)."""

    patch_select: int = 0    # drum sample index
    level: int = 100
    pitch: int = 64          # center = 64
    decay: int = 127
    distortion: int = 0
    eq: int = 64             # center = 64
    pan: int = 64            # center = 64
    unknown1: int = 0
    reverb_send: int = 0
    delay_send: int = 0
    unknown2: int = 0

    def to_bytes(self) -> bytes:
        return bytes([
            self.patch_select, self.level, self.pitch, self.decay,
            self.distortion, self.eq, self.pan, self.unknown1,
            self.reverb_send, self.delay_send, self.unknown2,
        ])

    @classmethod
    def from_bytes(cls, data: bytes) -> DrumTrackConfig:
        return cls(
            patch_select=data[0], level=data[1], pitch=data[2], decay=data[3],
            distortion=data[4], eq=data[5], pan=data[6], unknown1=data[7],
            reverb_send=data[8], delay_send=data[9], unknown2=data[10],
        )


@dataclass
class NCSProjectSettings:
    """Project-level settings stored in the tail preamble."""

    scale_root: int = 0    # 0-11 (0=C, 1=C#, ..., 7=G, ..., 11=B)
    scale_type: int = 0    # 0-15 (0=NatMinor, 1=Major, ...)
    delay_preset: int = 0  # 0-15
    reverb_preset: int = 0 # 0-7


@dataclass
class NCSFile:
    """Complete NCS project file."""

    header: NCSHeader = field(default_factory=NCSHeader)
    timing: NCSTimingSection = field(default_factory=NCSTimingSection)

    # Scenes and chains
    scenes: list[Scene] = field(default_factory=lambda: [Scene() for _ in range(NUM_SCENES)])
    scene_state: bytes = field(default_factory=lambda: bytes(_SCENE_STATE_SIZE))
    scene_chain: ChainEntry = field(default_factory=ChainEntry)
    pattern_chains: list[ChainEntry] = field(
        default_factory=lambda: [ChainEntry() for _ in range(NUM_TRACKS)]
    )
    # Raw bytes for the D4 pattern chain that spills past the region boundary
    _d4_chain_spillover: bytes = field(default_factory=lambda: bytes(4))

    project_settings: NCSProjectSettings = field(default_factory=NCSProjectSettings)
    fx: FXSettings = field(default_factory=FXSettings)
    synth1_patch: bytes = field(default_factory=lambda: bytes(_PATCH_SIZE))
    synth2_patch: bytes = field(default_factory=lambda: bytes(_PATCH_SIZE))
    drum_configs: list[DrumTrackConfig] = field(
        default_factory=lambda: [DrumTrackConfig() for _ in range(NUM_DRUM_TRACKS)]
    )
    synth_patterns: list[SynthPattern] = field(default_factory=list)
    drum_patterns: list[DrumPattern] = field(default_factory=list)
    midi_patterns: list[SynthPattern] = field(default_factory=list)
    tail: bytes = b""


# --- Parser ---


def parse_ncs(path: str | Path) -> NCSFile:
    """Parse an NCS file into an NCSFile structure."""
    data = Path(path).read_bytes()
    if len(data) != NCS_FILE_SIZE:
        raise ValueError(f"Invalid NCS file size: {len(data)} (expected {NCS_FILE_SIZE})")
    if data[:4] != NCS_MAGIC:
        raise ValueError(f"Invalid NCS signature: {data[:4]!r}")

    ncs = NCSFile()
    sc_base = SCENES_CHAINS_OFFSET

    # Header
    ncs.header = NCSHeader(
        signature=data[0:4],
        total_session_size=struct.unpack_from("<I", data, 4)[0],
        session_colour=struct.unpack_from("<I", data, 8)[0],
        color=struct.unpack_from("<I", data, 12)[0],
        name=data[16:48].decode("ascii", errors="replace").rstrip(),
        unknown_30=data[48:52],
    )

    # Timing
    ncs.timing = NCSTimingSection.from_bytes(data[TIMING_OFFSET:TIMING_OFFSET + 5])

    # Scenes
    ncs.scenes = [
        Scene.from_bytes(data[sc_base + i * _SCENE_SIZE : sc_base + (i + 1) * _SCENE_SIZE])
        for i in range(NUM_SCENES)
    ]

    # Scene state
    ncs.scene_state = data[sc_base + _SCENE_STATE_OFFSET : sc_base + _SCENE_STATE_OFFSET + _SCENE_STATE_SIZE]

    # Scene chain
    ncs.scene_chain = ChainEntry.from_bytes(
        data[sc_base + _SCENE_CHAIN_OFFSET : sc_base + _SCENE_CHAIN_OFFSET + 4]
    )

    # Pattern chains (7 tracks fit in the region, D4 spills into pattern data)
    ncs.pattern_chains = []
    for i in range(NUM_TRACKS - 1):  # S1-D3
        off = sc_base + _PATTERN_CHAINS_OFFSET + i * 4
        ncs.pattern_chains.append(ChainEntry.from_bytes(data[off:off + 4]))
    # D4: starts at sc_base + _PATTERN_CHAINS_OFFSET + 7*4 = 0x39 + 652 + 28 = 0x39 + 680 = 0x2E1
    d4_off = sc_base + _PATTERN_CHAINS_OFFSET + 7 * 4
    ncs.pattern_chains.append(ChainEntry.from_bytes(data[d4_off:d4_off + 4]))

    # Parse 64 pattern blocks
    ncs.synth_patterns = []
    ncs.drum_patterns = []
    ncs.midi_patterns = []

    for block_idx in range(64):
        meta_offset = _METADATA_OFFSETS[block_idx]

        if block_idx == 0:
            # First block: pre_data starts after D4 chain spillover
            prev_end = d4_off + 4
        else:
            prev_end = _METADATA_OFFSETS[block_idx - 1] + PATTERN_SETTINGS_SIZE
        step_start = _step_data_start(block_idx)
        pre_data = data[prev_end:step_start]

        settings_end = meta_offset + PATTERN_SETTINGS_SIZE
        if block_idx < 63:
            post_data = b""
        else:
            post_data = data[settings_end:TAIL_OFFSET]

        if _is_drum_block(block_idx):
            raw_header = data[step_start:step_start + 16]
            row_base = step_start + 16
            velocity_row = data[row_base:row_base + 32]
            probability_row = data[row_base + 32:row_base + 64]
            drum_choice_row = data[row_base + 64:row_base + 96]
            rhythm_row = data[row_base + 96:row_base + 128]

            steps = [
                DrumStep(
                    active=rhythm_row[i] != 0,
                    velocity=velocity_row[i],
                    probability=probability_row[i],
                    drum_choice=drum_choice_row[i],
                )
                for i in range(STEPS_PER_PATTERN)
            ]
            settings = PatternSettings.from_bytes(data[meta_offset:meta_offset + PATTERN_SETTINGS_SIZE])
            ncs.drum_patterns.append(DrumPattern(
                steps=steps, settings=settings,
                raw_header=raw_header, pre_data=pre_data, post_data=post_data,
            ))
        else:
            steps = [
                SynthStep.from_bytes(data[step_start + i * SYNTH_STEP_SIZE:step_start + (i + 1) * SYNTH_STEP_SIZE])
                for i in range(STEPS_PER_PATTERN)
            ]
            settings = PatternSettings.from_bytes(data[meta_offset:meta_offset + PATTERN_SETTINGS_SIZE])
            pattern = SynthPattern(
                steps=steps, settings=settings,
                pre_data=pre_data, post_data=post_data,
            )
            if block_idx < _SYNTH_BLOCK_COUNT:
                ncs.synth_patterns.append(pattern)
            else:
                ncs.midi_patterns.append(pattern)

    ncs.tail = data[TAIL_OFFSET:]

    # Extract synth patches and drum configs from tail
    t = ncs.tail
    ncs.synth1_patch = bytes(t[_SYNTH1_PATCH_OFFSET:_SYNTH1_PATCH_OFFSET + _PATCH_SIZE])
    ncs.synth2_patch = bytes(t[_SYNTH2_PATCH_OFFSET:_SYNTH2_PATCH_OFFSET + _PATCH_SIZE])
    ncs.drum_configs = [
        DrumTrackConfig.from_bytes(t[_DRUM_CONFIGS_OFFSET + i * _DRUM_CONFIG_SIZE:
                                     _DRUM_CONFIGS_OFFSET + (i + 1) * _DRUM_CONFIG_SIZE])
        for i in range(NUM_DRUM_TRACKS)
    ]

    # Extract project settings from tail preamble
    ncs.project_settings = NCSProjectSettings(
        scale_root=t[_TAIL_SCALE_ROOT_OFFSET],
        scale_type=t[_TAIL_SCALE_TYPE_OFFSET],
        delay_preset=t[_TAIL_DELAY_PRESET_OFFSET],
        reverb_preset=t[_TAIL_REVERB_PRESET_OFFSET],
    )

    # Extract FX settings from tail
    ncs.fx = FXSettings(
        reverb_sends=list(t[_REVERB_SENDS_OFFSET:_REVERB_SENDS_OFFSET + 8]),
        reverb_type=t[_REVERB_PARAMS_OFFSET],
        reverb_decay=t[_REVERB_PARAMS_OFFSET + 1],
        reverb_damping=t[_REVERB_PARAMS_OFFSET + 2],
        delay_sends=list(t[_DELAY_SENDS_OFFSET:_DELAY_SENDS_OFFSET + 8]),
        delay_time=t[_DELAY_PARAMS_OFFSET],
        delay_sync=t[_DELAY_PARAMS_OFFSET + 1],
        delay_feedback=t[_DELAY_PARAMS_OFFSET + 2],
        delay_width=t[_DELAY_PARAMS_OFFSET + 3],
        delay_lr_ratio=t[_DELAY_PARAMS_OFFSET + 4],
        delay_slew=t[_DELAY_PARAMS_OFFSET + 5],
        fx_bypass=t[_FX_BYPASS_OFFSET] != 0,
        sidechain_s1=SidechainSettings(
            source=t[_SIDECHAIN_S1_OFFSET],
            attack=t[_SIDECHAIN_S1_OFFSET + 1],
            hold=t[_SIDECHAIN_S1_OFFSET + 2],
            decay=t[_SIDECHAIN_S1_OFFSET + 3],
            depth=t[_SIDECHAIN_S1_OFFSET + 4],
        ),
        sidechain_s2=SidechainSettings(
            source=t[_SIDECHAIN_S2_OFFSET],
            attack=t[_SIDECHAIN_S2_OFFSET + 1],
            hold=t[_SIDECHAIN_S2_OFFSET + 2],
            decay=t[_SIDECHAIN_S2_OFFSET + 3],
            depth=t[_SIDECHAIN_S2_OFFSET + 4],
        ),
        sidechain_m1=SidechainSettings(
            source=t[_SIDECHAIN_M1_OFFSET],
            attack=t[_SIDECHAIN_M1_OFFSET + 1],
            hold=t[_SIDECHAIN_M1_OFFSET + 2],
            decay=t[_SIDECHAIN_M1_OFFSET + 3],
            depth=t[_SIDECHAIN_M1_OFFSET + 4],
        ),
        sidechain_m2=SidechainSettings(
            source=t[_SIDECHAIN_M2_OFFSET],
            attack=t[_SIDECHAIN_M2_OFFSET + 1],
            hold=t[_SIDECHAIN_M2_OFFSET + 2],
            decay=t[_SIDECHAIN_M2_OFFSET + 3],
            depth=t[_SIDECHAIN_M2_OFFSET + 4],
        ),
        mixer_levels=list(t[_MIXER_LEVELS_OFFSET:_MIXER_LEVELS_OFFSET + 4]),
        mixer_pans=list(t[_MIXER_PANS_OFFSET:_MIXER_PANS_OFFSET + 4]),
    )

    # Read sidechain preset indices
    # S1/S2 presets are in drum block 0's raw_header; M1/M2 in the tail preamble
    if ncs.drum_patterns:
        hdr = ncs.drum_patterns[0].raw_header
        if len(hdr) >= 12:
            ncs.fx.sidechain_s1.preset = hdr[_SC_PRESET_S1_DRUM_HDR_BYTE]
            ncs.fx.sidechain_s2.preset = hdr[_SC_PRESET_S2_DRUM_HDR_BYTE]
    ncs.fx.sidechain_m1.preset = t[_SC_PRESET_M1_PREAMBLE_BYTE]
    ncs.fx.sidechain_m2.preset = t[_SC_PRESET_M2_PREAMBLE_BYTE]

    # Parse macro automation from pre_data of subsequent blocks
    _parse_automation_locks(ncs)

    return ncs


def parse_ncs_from_bytes(data: bytes) -> NCSFile:
    """Parse NCS data from raw bytes.

    Writes to a temporary file and delegates to parse_ncs.

    Args:
        data: Raw NCS file bytes (160,780 bytes).

    Returns:
        Parsed NCSFile structure.

    Raises:
        ValueError: If data size or magic signature is invalid.
    """
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(suffix=".ncs", delete=False) as f:
        f.write(data)
        tmp_path = f.name
    try:
        return parse_ncs(tmp_path)
    finally:
        os.unlink(tmp_path)


def _parse_automation_locks(ncs: NCSFile) -> None:
    """Extract automation lock data from pre_data regions.

    Automation for block N is stored in block N+1's pre_data.
    - Synth/MIDI blocks: 2304 bytes, 8 macros × 6 micro-tick lanes × 32 steps.
    - Drum blocks: 1520 bytes, 4 params × 6 micro-tick lanes × 32 steps.
    """
    # Build ordered list of all blocks
    synth_i = drum_i = midi_i = 0
    blocks: list[tuple[int, SynthPattern | DrumPattern]] = []
    for block_idx in range(64):
        if _is_drum_block(block_idx):
            blocks.append((block_idx, ncs.drum_patterns[drum_i]))
            drum_i += 1
        elif block_idx < _SYNTH_BLOCK_COUNT:
            blocks.append((block_idx, ncs.synth_patterns[synth_i]))
            synth_i += 1
        else:
            blocks.append((block_idx, ncs.midi_patterns[midi_i]))
            midi_i += 1

    for block_idx, pattern in blocks:
        next_block_idx = block_idx + 1
        if next_block_idx >= 64:
            continue
        next_pre_data = _get_block_pre_data(ncs, next_block_idx)

        if isinstance(pattern, DrumPattern):
            if len(next_pre_data) != DRUM_AUTOMATION_REGION_SIZE:
                continue
            _parse_drum_locks(pattern, next_pre_data)
        else:
            if len(next_pre_data) != AUTOMATION_REGION_SIZE:
                continue
            _parse_synth_locks(pattern, next_pre_data)


def _parse_synth_locks(pattern: SynthPattern, pre_data: bytes) -> None:
    """Parse synth/MIDI macro and mixer locks from automation region."""
    num_steps = pattern.settings.playback_end + 1
    total_positions = AUTOMATION_LANES_PER_MACRO * STEPS_PER_PATTERN  # 192
    positions_per_step = total_positions // num_steps if num_steps > 0 else 6

    # Macros (slots 0-7, lanes 0-47)
    for macro in range(1, 9):
        base_offset = (macro - 1) * total_positions
        locks = {}
        for step in range(num_steps):
            flat_idx = step * positions_per_step
            lane = flat_idx // STEPS_PER_PATTERN
            pos = flat_idx % STEPS_PER_PATTERN
            val = pre_data[base_offset + lane * STEPS_PER_PATTERN + pos]
            if val != 0xFF:
                locks[step] = val
        if locks:
            pattern.macro_locks[macro] = locks

    # Mixer/FX (slots 8-11, lanes 48-71)
    for slot, param_name in MIXER_AUTOMATION_PARAMS.items():
        base_offset = slot * total_positions
        if base_offset >= len(pre_data):
            break
        locks = {}
        for step in range(num_steps):
            flat_idx = step * positions_per_step
            lane = flat_idx // STEPS_PER_PATTERN
            pos = flat_idx % STEPS_PER_PATTERN
            offset = base_offset + lane * STEPS_PER_PATTERN + pos
            if offset < len(pre_data):
                val = pre_data[offset]
                if val != 0xFF:
                    locks[step] = val
        if locks:
            pattern.mixer_locks[param_name] = locks


def _parse_drum_locks(pattern: DrumPattern, pre_data: bytes) -> None:
    """Parse drum parameter locks from automation region."""
    num_steps = pattern.settings.playback_end + 1
    total_positions = AUTOMATION_LANES_PER_MACRO * STEPS_PER_PATTERN  # 192
    positions_per_step = total_positions // num_steps if num_steps > 0 else 6

    for slot, param_name in DRUM_AUTOMATION_PARAMS.items():
        base_offset = slot * total_positions
        if base_offset >= len(pre_data):
            break
        locks = {}
        for step in range(num_steps):
            flat_idx = step * positions_per_step
            lane = flat_idx // STEPS_PER_PATTERN
            pos = flat_idx % STEPS_PER_PATTERN
            offset = base_offset + lane * STEPS_PER_PATTERN + pos
            if offset < len(pre_data):
                val = pre_data[offset]
                if val != 0xFF:
                    locks[step] = val
        if locks:
            pattern.param_locks[param_name] = locks


def _get_block_pre_data(ncs: NCSFile, block_idx: int) -> bytes:
    """Get the pre_data of a specific block by index."""
    synth_i = drum_i = midi_i = 0
    for bi in range(64):
        if _is_drum_block(bi):
            pat = ncs.drum_patterns[drum_i]
            drum_i += 1
        elif bi < _SYNTH_BLOCK_COUNT:
            pat = ncs.synth_patterns[synth_i]
            synth_i += 1
        else:
            pat = ncs.midi_patterns[midi_i]
            midi_i += 1
        if bi == block_idx:
            return pat.pre_data
    return b""


def _write_locks_to_region(
    locks: dict[int | str, dict[float, int]],
    slot_mapping: dict[int | str, int],
    region_size: int,
    num_steps: int,
) -> bytes:
    """Write automation locks into a pre_data region.

    Args:
        locks: {key: {position: value}} where key is macro number or param name.
        slot_mapping: {key: slot_index} mapping lock keys to automation slot indices.
        region_size: Size of the automation region in bytes.
        num_steps: Number of active steps in the pattern.

    Returns the automation region bytes.
    """
    buf = bytearray(b'\xff' * region_size)
    total_positions = AUTOMATION_LANES_PER_MACRO * STEPS_PER_PATTERN  # 192
    positions_per_step = total_positions // num_steps if num_steps > 0 else 6

    for key, positions in locks.items():
        slot = slot_mapping.get(key)
        if slot is None:
            continue
        base_offset = slot * total_positions
        if base_offset >= region_size:
            continue

        for position, value in positions.items():
            clamped = max(0, min(127, value))
            pos_f = float(position)
            step_int = int(pos_f)
            frac = pos_f - step_int

            if step_int < 0 or step_int >= num_steps:
                continue

            if frac == 0.0 and pos_f == int(pos_f):
                start_pos = step_int * positions_per_step
                for p in range(positions_per_step):
                    flat_idx = start_pos + p
                    if flat_idx < total_positions:
                        lane = flat_idx // STEPS_PER_PATTERN
                        pos = flat_idx % STEPS_PER_PATTERN
                        idx = base_offset + lane * STEPS_PER_PATTERN + pos
                        if idx < region_size:
                            buf[idx] = clamped
            else:
                flat_idx = int(round(pos_f * positions_per_step))
                if 0 <= flat_idx < total_positions:
                    lane = flat_idx // STEPS_PER_PATTERN
                    pos = flat_idx % STEPS_PER_PATTERN
                    idx = base_offset + lane * STEPS_PER_PATTERN + pos
                    if idx < region_size:
                        buf[idx] = clamped

    return bytes(buf)


def write_automation_to_pre_data(
    pattern: SynthPattern | DrumPattern, next_block_pre_data: bytes,
) -> bytes:
    """Write automation locks from a pattern into the next block's pre_data.

    Handles both synth/MIDI macro locks and drum parameter locks.
    Returns the modified pre_data bytes.
    """
    if isinstance(pattern, DrumPattern):
        if not pattern.param_locks:
            return next_block_pre_data
        slot_mapping = {name: slot for slot, name in DRUM_AUTOMATION_PARAMS.items()}
        return _write_locks_to_region(
            pattern.param_locks, slot_mapping,
            DRUM_AUTOMATION_REGION_SIZE,
            pattern.settings.playback_end + 1,
        )
    else:
        if not pattern.macro_locks and not pattern.mixer_locks:
            return next_block_pre_data
        # Combine macro locks (slots 0-7) and mixer locks (slots 8-11)
        all_locks: dict[int | str, dict[float, int]] = {}
        slot_mapping: dict[int | str, int] = {}
        # Macros
        for macro, locks in pattern.macro_locks.items():
            all_locks[macro] = locks
            slot_mapping[macro] = macro - 1
        # Mixer/FX
        mixer_slot_mapping = {name: slot for slot, name in MIXER_AUTOMATION_PARAMS.items()}
        for param_name, locks in pattern.mixer_locks.items():
            all_locks[param_name] = locks
            slot_mapping[param_name] = mixer_slot_mapping[param_name]
        return _write_locks_to_region(
            all_locks, slot_mapping,
            AUTOMATION_REGION_SIZE,
            pattern.settings.playback_end + 1,
        )


# --- Writer ---


def write_ncs(ncs: NCSFile, path: str | Path) -> None:
    """Write an NCSFile to disk."""
    Path(path).write_bytes(serialize_ncs(ncs))


def _write_automation_to_blocks(ncs: NCSFile) -> None:
    """Inject automation locks from all pattern types into next block's pre_data."""
    block_patterns: dict[int, SynthPattern | DrumPattern] = {}
    synth_i = drum_i = midi_i = 0
    for block_idx in range(64):
        if _is_drum_block(block_idx):
            block_patterns[block_idx] = ncs.drum_patterns[drum_i]
            drum_i += 1
        elif block_idx < _SYNTH_BLOCK_COUNT:
            block_patterns[block_idx] = ncs.synth_patterns[synth_i]
            synth_i += 1
        else:
            block_patterns[block_idx] = ncs.midi_patterns[midi_i]
            midi_i += 1

    for block_idx, pattern in block_patterns.items():
        has_locks = (
            (isinstance(pattern, DrumPattern) and pattern.param_locks) or
            (isinstance(pattern, SynthPattern) and (pattern.macro_locks or pattern.mixer_locks))
        )
        if not has_locks:
            continue
        next_idx = block_idx + 1
        if next_idx >= 64:
            continue
        next_pre = _get_block_pre_data(ncs, next_idx)
        updated = write_automation_to_pre_data(pattern, next_pre)
        _set_block_pre_data(ncs, next_idx, updated)


def _set_block_pre_data(ncs: NCSFile, block_idx: int, data: bytes) -> None:
    """Set the pre_data of a specific block by index."""
    synth_i = drum_i = midi_i = 0
    for bi in range(64):
        if _is_drum_block(bi):
            if bi == block_idx:
                ncs.drum_patterns[drum_i] = DrumPattern(
                    steps=ncs.drum_patterns[drum_i].steps,
                    settings=ncs.drum_patterns[drum_i].settings,
                    raw_header=ncs.drum_patterns[drum_i].raw_header,
                    pre_data=data,
                    post_data=ncs.drum_patterns[drum_i].post_data,
                )
                return
            drum_i += 1
        elif bi < _SYNTH_BLOCK_COUNT:
            if bi == block_idx:
                ncs.synth_patterns[synth_i].pre_data = data
                return
            synth_i += 1
        else:
            if bi == block_idx:
                ncs.midi_patterns[midi_i].pre_data = data
                return
            midi_i += 1


def serialize_ncs(ncs: NCSFile) -> bytes:
    """Serialize an NCSFile to bytes."""
    buf = bytearray(NCS_FILE_SIZE)
    sc_base = SCENES_CHAINS_OFFSET

    # Header
    buf[0:4] = ncs.header.signature
    struct.pack_into("<I", buf, 4, ncs.header.total_session_size)
    struct.pack_into("<I", buf, 8, ncs.header.session_colour)
    struct.pack_into("<I", buf, 12, ncs.header.color)
    buf[16:48] = ncs.header.name.encode("ascii")[:32].ljust(32)
    buf[48:52] = ncs.header.unknown_30

    # Timing
    buf[TIMING_OFFSET:TIMING_OFFSET + 5] = ncs.timing.to_bytes()

    # Scenes
    for i, scene in enumerate(ncs.scenes):
        off = sc_base + i * _SCENE_SIZE
        buf[off:off + _SCENE_SIZE] = scene.to_bytes()

    # Scene state
    buf[sc_base + _SCENE_STATE_OFFSET:sc_base + _SCENE_STATE_OFFSET + _SCENE_STATE_SIZE] = ncs.scene_state

    # Scene chain
    buf[sc_base + _SCENE_CHAIN_OFFSET:sc_base + _SCENE_CHAIN_OFFSET + 4] = ncs.scene_chain.to_bytes()

    # Pattern chains (including D4 which spills past region)
    for i, chain in enumerate(ncs.pattern_chains):
        off = sc_base + _PATTERN_CHAINS_OFFSET + i * 4
        buf[off:off + 4] = chain.to_bytes()

    # Write macro locks into next block's pre_data before serializing
    _write_automation_to_blocks(ncs)

    # Pattern blocks
    d4_off = sc_base + _PATTERN_CHAINS_OFFSET + 7 * 4
    synth_idx = drum_idx = midi_idx = 0
    for block_idx in range(64):
        meta_offset = _METADATA_OFFSETS[block_idx]

        if block_idx == 0:
            prev_end = d4_off + 4
        else:
            prev_end = _METADATA_OFFSETS[block_idx - 1] + PATTERN_SETTINGS_SIZE

        step_start = _step_data_start(block_idx)
        settings_end = meta_offset + PATTERN_SETTINGS_SIZE

        if _is_drum_block(block_idx):
            pat = ncs.drum_patterns[drum_idx]
            drum_idx += 1
            buf[prev_end:step_start] = pat.pre_data
            buf[step_start:step_start + 16] = pat.raw_header
            row_base = step_start + 16
            for i, step in enumerate(pat.steps):
                buf[row_base + i] = step.velocity
                buf[row_base + 32 + i] = step.probability
                buf[row_base + 64 + i] = step.drum_choice
                buf[row_base + 96 + i] = 1 if step.active else 0
            buf[meta_offset:settings_end] = pat.settings.to_bytes()
            if pat.post_data:
                buf[settings_end:settings_end + len(pat.post_data)] = pat.post_data
        else:
            if block_idx < _SYNTH_BLOCK_COUNT:
                pat = ncs.synth_patterns[synth_idx]
                synth_idx += 1
            else:
                pat = ncs.midi_patterns[midi_idx]
                midi_idx += 1
            buf[prev_end:step_start] = pat.pre_data
            for i, step in enumerate(pat.steps):
                off = step_start + i * SYNTH_STEP_SIZE
                buf[off:off + SYNTH_STEP_SIZE] = step.to_bytes()
            buf[meta_offset:settings_end] = pat.settings.to_bytes()
            if pat.post_data:
                buf[settings_end:settings_end + len(pat.post_data)] = pat.post_data

    # Tail
    tail = bytearray(ncs.tail)

    # Synth patches
    tail[_SYNTH1_PATCH_OFFSET:_SYNTH1_PATCH_OFFSET + _PATCH_SIZE] = ncs.synth1_patch
    tail[_SYNTH2_PATCH_OFFSET:_SYNTH2_PATCH_OFFSET + _PATCH_SIZE] = ncs.synth2_patch

    # Drum configs
    for i, dc in enumerate(ncs.drum_configs):
        off = _DRUM_CONFIGS_OFFSET + i * _DRUM_CONFIG_SIZE
        tail[off:off + _DRUM_CONFIG_SIZE] = dc.to_bytes()

    # Project settings
    tail[_TAIL_SCALE_ROOT_OFFSET] = ncs.project_settings.scale_root
    tail[_TAIL_SCALE_TYPE_OFFSET] = ncs.project_settings.scale_type
    tail[_TAIL_DELAY_PRESET_OFFSET] = ncs.project_settings.delay_preset
    tail[_TAIL_REVERB_PRESET_OFFSET] = ncs.project_settings.reverb_preset

    # FX settings
    fx = ncs.fx
    tail[_REVERB_SENDS_OFFSET:_REVERB_SENDS_OFFSET + 8] = fx.reverb_sends
    tail[_REVERB_PARAMS_OFFSET] = fx.reverb_type
    tail[_REVERB_PARAMS_OFFSET + 1] = fx.reverb_decay
    tail[_REVERB_PARAMS_OFFSET + 2] = fx.reverb_damping
    tail[_DELAY_SENDS_OFFSET:_DELAY_SENDS_OFFSET + 8] = fx.delay_sends
    tail[_DELAY_PARAMS_OFFSET] = fx.delay_time
    tail[_DELAY_PARAMS_OFFSET + 1] = fx.delay_sync
    tail[_DELAY_PARAMS_OFFSET + 2] = fx.delay_feedback
    tail[_DELAY_PARAMS_OFFSET + 3] = fx.delay_width
    tail[_DELAY_PARAMS_OFFSET + 4] = fx.delay_lr_ratio
    tail[_DELAY_PARAMS_OFFSET + 5] = fx.delay_slew
    tail[_FX_BYPASS_OFFSET] = 1 if fx.fx_bypass else 0
    # Sidechain parameters (4 tracks × 5 bytes)
    for sc, offset in [
        (fx.sidechain_s1, _SIDECHAIN_S1_OFFSET),
        (fx.sidechain_s2, _SIDECHAIN_S2_OFFSET),
        (fx.sidechain_m1, _SIDECHAIN_M1_OFFSET),
        (fx.sidechain_m2, _SIDECHAIN_M2_OFFSET),
    ]:
        tail[offset] = sc.source
        tail[offset + 1] = sc.attack
        tail[offset + 2] = sc.hold
        tail[offset + 3] = sc.decay
        tail[offset + 4] = sc.depth
    # Sidechain preset indices — M1/M2 in tail preamble
    tail[_SC_PRESET_M1_PREAMBLE_BYTE] = fx.sidechain_m1.preset
    tail[_SC_PRESET_M2_PREAMBLE_BYTE] = fx.sidechain_m2.preset
    tail[_MIXER_LEVELS_OFFSET:_MIXER_LEVELS_OFFSET + 4] = fx.mixer_levels
    tail[_MIXER_PANS_OFFSET:_MIXER_PANS_OFFSET + 4] = fx.mixer_pans

    buf[TAIL_OFFSET:] = tail

    # Sidechain preset indices — S1/S2 in drum block 0 raw_header
    drum0_step_start = _step_data_start(_DRUM_BLOCK_START)
    buf[drum0_step_start + _SC_PRESET_S1_DRUM_HDR_BYTE] = fx.sidechain_s1.preset
    buf[drum0_step_start + _SC_PRESET_S2_DRUM_HDR_BYTE] = fx.sidechain_s2.preset

    return bytes(buf)


# --- Convenience helpers ---


def get_synth_pattern(ncs: NCSFile, track: int, pattern: int) -> SynthPattern:
    """Get a synth pattern (track 0-1, pattern 0-7)."""
    return ncs.synth_patterns[track * PATTERNS_PER_TRACK + pattern]


def get_drum_pattern(ncs: NCSFile, track: int, pattern: int) -> DrumPattern:
    """Get a drum pattern (track 0-3, pattern 0-7)."""
    return ncs.drum_patterns[track * PATTERNS_PER_TRACK + pattern]


def get_midi_pattern(ncs: NCSFile, track: int, pattern: int) -> SynthPattern:
    """Get a MIDI pattern (track 0-1, pattern 0-7)."""
    return ncs.midi_patterns[track * PATTERNS_PER_TRACK + pattern]


def set_pattern_chain(ncs: NCSFile, track: int, start: int = 0, end: int = 0) -> None:
    """Set a pattern chain for a track (track 0-7: S1,S2,M1,M2,D1,D2,D3,D4)."""
    ncs.pattern_chains[track].start = start
    ncs.pattern_chains[track].end = end


def set_scene(ncs: NCSFile, scene_index: int, track_chains: dict[int, tuple[int, int]]) -> None:
    """Set a scene's per-track pattern chain assignments.

    Hardware byte layout per track entry:
      byte[0] = chain end index (0-based, inclusive)
      byte[1] = 0
      byte[2] = 0
      byte[3] = chain start index (0-based)

    Args:
        scene_index: 0-15
        track_chains: dict of {track_index: (start, end)} for each track
    """
    scene = ncs.scenes[scene_index]
    max_start = 0
    for track, (start, end) in track_chains.items():
        scene.track_chains[track].start = start
        scene.track_chains[track].end = end
        if start > max_start:
            max_start = start
    # Scene header byte[7] stores the start index of the first track (or max)
    hdr = bytearray(scene.header)
    hdr[7] = max_start
    scene.header = bytes(hdr)


def set_scene_chain(ncs: NCSFile, start: int = 0, end: int = 0) -> None:
    """Set the scene chain range (uses byte[1] for start, byte[0] for end)."""
    ncs.scene_chain.scene_chain_start = start
    ncs.scene_chain.end = end
