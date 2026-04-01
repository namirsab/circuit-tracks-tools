# circuit-tracks API Reference

## Constants (`circuit_tracks.constants`)

### MIDI Channels (0-indexed)

| Constant | Value | Description |
|----------|-------|-------------|
| `SYNTH1_CHANNEL` | 0 | Synth 1 |
| `SYNTH2_CHANNEL` | 1 | Synth 2 |
| `MIDI1_CHANNEL` | 2 | MIDI track 1 |
| `MIDI2_CHANNEL` | 3 | MIDI track 2 |
| `DRUMS_CHANNEL` | 9 | All 4 drum tracks |
| `PROJECT_CHANNEL` | 15 | Project-level params |

### Parameter Maps

| Map | Type | Description |
|-----|------|-------------|
| `SYNTH_CC` | `dict[str, int]` | Synth CC params (voice, oscillators, filter, envelopes, effects, macros) |
| `SYNTH_NRPN` | `dict[str, tuple[int, int]]` | Synth NRPN params as (MSB, LSB) tuples |
| `DRUM_CC` | `dict[int, dict[str, int]]` | Per-drum CC mappings (1-4) |
| `PROJECT_CC` | `dict[str, int]` | Reverb/delay sends, mixer levels/pans, master filter |
| `PROJECT_NRPN` | `dict[str, tuple[int, int]]` | Reverb, delay, FX bypass, sidechain params |
| `DRUM_NOTES` | `dict[int, int]` | Drum number (1-4) to MIDI note (60, 62, 64, 65) |

### Lookup Tables

| Table | Description |
|-------|-------------|
| `OSC_WAVEFORMS` | 30 oscillator waveforms (index -> name) |
| `FILTER_TYPES` | 6 filter types: LP12, LP24, BP6/6, BP12/12, HP12, HP24 |
| `DISTORTION_TYPES` | 7 distortion types |
| `LFO_WAVEFORMS` | 38 LFO waveforms |
| `MOD_MATRIX_SOURCES` | 13 modulation sources |
| `MOD_MATRIX_DESTINATIONS` | 18 modulation destinations |
| `MACRO_DESTINATIONS` | 71 macro knob destinations (indices 0-70) |

Each has a reverse lookup: `MACRO_DEST_BY_NAME`, `MOD_SOURCE_BY_NAME`, `MOD_DEST_BY_NAME`, `OSC_WAVEFORM_BY_NAME`, `FILTER_TYPE_BY_NAME`, `DISTORTION_TYPE_BY_NAME`, `LFO_WAVEFORM_BY_NAME`.

### FX Presets

| Table | Description |
|-------|-------------|
| `REVERB_PRESETS` | 8 reverb presets (type, decay, damping) |
| `DELAY_PRESETS` | 16 delay presets (time, sync, feedback, width, lr_ratio, slew) |
| `REVERB_TYPES` | Reverb type names |
| `DELAY_LR_RATIOS` | Delay L/R ratio names |

### Functions

```python
def load_drum_sample_names() -> dict[int, str]
```
Load drum sample names from user config, falling back to factory defaults.

```python
def save_drum_sample_names(samples: dict[int, str]) -> Path
```
Save drum sample names to `~/.config/circuit-mcp/drum_samples.json`.

---

## MidiConnection (`circuit_tracks.midi`)

MIDI I/O abstraction wrapping [mido](https://mido.readthedocs.io/).

### Constructor

```python
MidiConnection()
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `is_connected` | `bool` | True if connected |
| `has_input` | `bool` | True if bidirectional MIDI is available |
| `port_name` | `str \| None` | Current port name |

### Methods

```python
# Discovery
MidiConnection.list_output_ports() -> list[str]   # static
MidiConnection.list_input_ports() -> list[str]     # static

# Connection
connect(port_name: str) -> None        # Auto-opens matching input port
disconnect() -> None

# Notes
note_on(channel: int, note: int, velocity: int = 100) -> None
note_off(channel: int, note: int) -> None
play_note(channel: int, note: int, velocity: int = 100, duration_s: float = 0.5) -> None
all_notes_off(channel: int) -> None

# Control
control_change(channel: int, control: int, value: int) -> None
nrpn(channel: int, msb: int, lsb: int, value: int) -> None
program_change(channel: int, program: int) -> None

# SysEx
send_sysex(data: list[int]) -> None
sysex_request(request_data: list[int], timeout_s: float = 3.0,
              match_fn: Callable | None = None) -> list[int] | None

# Transport
send_clock() -> None
send_realtime(msg_type: str) -> None   # "start", "stop", "continue"

# Low-level
send(msg: mido.Message) -> None
```

---

## SequencerEngine (`circuit_tracks.sequencer`)

Software step sequencer that plays patterns by sending MIDI notes to the Circuit Tracks in real time. This does **not** write to the Circuit Tracks' internal sequencer -- it runs on the host machine and triggers sounds over MIDI. To write patterns to the device's own sequencer, use the NCS export path (`song_to_ncs` / `export_song_to_device`).

### Data Types

```python
class TrackType(Enum):
    SYNTH1 = "synth1"
    SYNTH2 = "synth2"
    DRUM1  = "drum1"
    DRUM2  = "drum2"
    DRUM3  = "drum3"
    DRUM4  = "drum4"
    MIDI1  = "midi1"
    MIDI2  = "midi2"

@dataclass
class Step:
    notes: list[int]          # MIDI note numbers (default: [60])
    velocity: int = 100       # 0-127
    gate: float = 0.5         # fraction of step duration
    enabled: bool = True
    probability: float = 1.0  # 0.0-1.0
    sample: int | None = None # drum sample index

@dataclass
class Track:
    track_type: TrackType
    steps: dict[int, Step]    # step_index -> Step
    muted: bool = False
    num_steps: int = 16

@dataclass
class Pattern:
    tracks: dict[str, Track]  # track_name -> Track
    length: int = 16
```

### Constructor

```python
SequencerEngine(midi: MidiConnection)
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `is_running` | `bool` | True if playing |

### Methods

```python
# Query
get_status() -> dict           # running, current_pattern, current_step, bpm, etc.
get_pattern(name: str) -> Pattern | None
list_patterns() -> list[str]

# Pattern management
set_pattern(name: str, pattern: Pattern) -> None
clear_pattern(name: str) -> None
set_track(pattern_name: str, track_name: str, steps: dict[int, Step],
          clear: bool = True) -> None

# Transport
start(pattern_name: str, bpm: float = 120.0, send_clock: bool = True) -> None
stop() -> None
set_bpm(bpm: float) -> None

# Muting
set_mute(track_name: str, muted: bool) -> None
set_mutes(mutes: dict[str, bool]) -> None

# Queue
queue_patterns(names: list[str]) -> None
set_queue(names: list[str]) -> None
clear_queue() -> None
```

---

## Patch Read/Write (`circuit_tracks.patch`)

SysEx-based patch dump request, parsing, and modification.

```python
def request_current_patch(midi: MidiConnection, synth: int) -> list[int] | None
```
Request current patch via SysEx. `synth`: 1 or 2. Returns raw data or `None` on timeout.

```python
def parse_patch_data(sysex_data: list[int]) -> dict
```
Parse SysEx dump into dict with `name`, `category`, `genre`, `params`, `raw_params_hex_first_100`.

```python
def send_current_patch(midi: MidiConnection, synth: int, patch_bytes: list[int]) -> None
```
Send 340-byte patch to replace the current patch on a synth.

```python
def save_patch_to_slot(midi: MidiConnection, synth: int, slot: int,
                       patch_bytes: list[int]) -> None
```
Save patch to flash slot (0-63).

```python
def read_and_modify_patch(midi: MidiConnection, synth: int,
                          modifications: dict[str, int]) -> dict
```
Read current patch, apply modifications, send back. Returns `{"applied": {...}, "unknown_params": [...]}`.

```python
def modify_patch_bytes(patch_bytes: list[int],
                       modifications: dict[str, int | str]) -> tuple[list[int], dict, list[str]]
```
Modify 340-byte patch binary offline. Special keys: `name` (str), `category`, `genre`. Returns `(modified_bytes, applied_dict, error_list)`.

```python
def parse_patch_file(file_path: str) -> dict
```
Parse `.syx` file from disk. Returns parsed patch dict with `file` and `file_size`.

---

## PatchBuilder (`circuit_tracks.patch_builder`)

Fluent API for building synth patches from scratch.

### Constructor

```python
PatchBuilder(name: str = "Init")
```

### Sound Design (all return `self` for chaining)

```python
.name(n: str)
.category(c: int)
.genre(g: int)

.voice(polyphony=None, portamento=None, pre_glide=None, octave=None)

.osc1(wave=None, interpolate=None, pulse_width=None, sync_depth=None,
      density=None, density_detune=None, semitones=None, cents=None,
      pitchbend=None)
.osc2(...)  # same params as osc1

.mixer(osc1_level=None, osc2_level=None, ring_mod=None, noise=None,
       pre_fx=None, post_fx=None)

.filter(frequency=None, resonance=None, drive=None, drive_type=None,
        filter_type=None, routing=None, tracking=None, q_normalize=None,
        env2_to_freq=None)

.env_amp(attack=None, decay=None, sustain=None, release=None, velocity=None)
.env_filter(...)  # same params
.env3(delay=None, attack=None, decay=None, sustain=None, release=None)

.lfo1(waveform=None, rate=None, phase_offset=None, slew_rate=None,
      delay=None, delay_sync=None, rate_sync=None, one_shot=None,
      key_sync=None, common_sync=None, delay_trigger=None, fade_mode=None)
.lfo2(...)  # same params

.eq(bass_freq=None, bass_level=None, mid_freq=None, mid_level=None,
    treble_freq=None, treble_level=None)

.distortion(level=None, type=None, compensation=None)

.chorus(level=None, type=None, rate=None, rate_sync=None,
        feedback=None, mod_depth=None, delay=None)

.add_mod(source, destination, depth=80, source2=0)
.clear_mods()

.set_macro(macro_num, targets, position=0)
```

### Build

```python
.build() -> bytes        # 340-byte patch binary
.build_syx(synth=1) -> bytes  # Complete .syx file
```

### Presets

```python
preset_pad(name="Pad") -> PatchBuilder       # Warm pad: detuned saws, slow attack
preset_bass(name="Bass") -> PatchBuilder      # Mono bass: saw, LP24, fast envelope
preset_lead(name="Lead") -> PatchBuilder      # Mono lead: bright, portamento, distortion
preset_pluck(name="Pluck") -> PatchBuilder    # Pluck: fast attack, short decay
```

---

## Macros (`circuit_tracks.macros`)

Macro knob configuration and parameter scaling.

```python
@dataclass
class MacroTarget:
    param: str            # Parameter name
    min_val: int = 0      # Value at knob position 0
    max_val: int = 127    # Value at knob position 127
```

### DEFAULT_MACROS

| Knob | Name | Targets |
|------|------|---------|
| 1 | Oscillator | osc1/2 wave_interpolate |
| 2 | Oscillator Mod | osc1/2 virtual_sync_depth |
| 3 | Amp Envelope | env1 attack/release |
| 4 | Filter Envelope | env2 attack/decay |
| 5 | Filter Frequency | filter_frequency |
| 6 | Resonance | filter_resonance |
| 7 | Modulation | lfo1_rate |
| 8 | FX | distortion_level, chorus_level |

### Functions

```python
def scale_value(knob: int, min_val: int, max_val: int) -> int
```
Scale knob position (0-127) to target range.

```python
def apply_macro(macro_num: int, knob_value: int,
                macros: dict | None = None) -> dict[str, int]
```
Compute parameter values for a macro knob position. Returns `{param_name: value}`.

---

## MorphEngine (`circuit_tracks.morph`)

Manages concurrent parameter morph threads for smooth CC/NRPN transitions.

### Constructor

```python
MorphEngine(midi: MidiConnection)
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `active_morphs` | `list[str]` | Currently running morph IDs |

### Methods

```python
def next_id(self) -> str
```
Generate an auto-incremented morph name.

```python
def start(self, morph_id: str, channel: int, start: dict[str, int],
          target: dict[str, int], duration_seconds: float, ping_pong: bool,
          cc_maps: list[dict], nrpn_maps: list[dict]) -> str | None
```
Start a parameter morph. Returns error string if validation fails, `None` on success. Interpolates params linearly in a background thread at ~20 updates/sec.

```python
def stop(self, morph_id: str) -> bool           # Stop by exact ID
def stop_by_prefix(self, prefix: str) -> list[str]  # Stop by ID prefix
def stop_by_name(self, name: str) -> list[str]      # Stop by name suffix
def stop_all(self) -> int                        # Stop all, returns count
```

---

## NCS Parser (`circuit_tracks.ncs_parser`)

Binary parser/writer for the Circuit Tracks `.ncs` project file format (160,780 bytes).

### Constants

| Constant | Value |
|----------|-------|
| `NCS_FILE_SIZE` | 160,780 |
| `STEPS_PER_PATTERN` | 32 |
| `NOTES_PER_STEP` | 6 |
| `PATTERNS_PER_TRACK` | 8 |
| `NUM_SCENES` | 16 |
| `AUTOMATION_REGION_SIZE` | 2,304 (synth/MIDI blocks) |
| `DRUM_AUTOMATION_REGION_SIZE` | 1,520 (drum blocks) |
| `AUTOMATION_LANES_PER_MACRO` | 6 (micro-ticks per step) |

### Automation Slot Mappings

**Synth/MIDI** (`MIXER_AUTOMATION_PARAMS`):

| Slot | Lanes | Parameter |
|------|-------|-----------|
| 0-7 | 0-47 | Macro knobs 1-8 |
| 8 | 48-53 | `reverb_send` |
| 9 | 54-59 | `delay_send` |
| 10 | 60-65 | `level` |
| 11 | 66-71 | `pan` |

**Drums** (`DRUM_AUTOMATION_PARAMS`):

| Slot | Parameter |
|------|-----------|
| 0 | `pitch` |
| 1 | `decay` |
| 2 | `distortion` |
| 3 | `eq` |
| 4 | `reverb_send` |
| 5 | `delay_send` |
| 6 | `level` |
| 7 | `pan` (partially truncated: steps 14-15 may be lost) |

### Key Data Types

```python
@dataclass
class NCSNote:
    note_number: int = 0
    gate: int = 0           # micro-ticks (6 per step)
    delay: int = 0          # micro-step offset 0-5
    velocity: int = 96

@dataclass
class SynthStep:
    assigned_note_mask: int = 0   # bitmask for 6 note slots
    probability: int = 7         # 0-7 (7 = 100%)
    notes: list[NCSNote]         # 6 slots

@dataclass
class DrumStep:
    active: bool = False
    velocity: int = 0
    probability: int = 7
    drum_choice: int = 0xFF      # 0xFF = no sample flip

@dataclass
class SynthPattern:
    steps: list[SynthStep]       # 32 steps
    settings: PatternSettings
    macro_locks: dict[int, dict[float, int]]   # {macro_num: {position: value}}
    mixer_locks: dict[str, dict[float, int]]   # {param_name: {position: value}}

@dataclass
class DrumPattern:
    steps: list[DrumStep]        # 32 steps
    settings: PatternSettings
    param_locks: dict[str, dict[float, int]]   # {param_name: {position: value}}

@dataclass
class PatternSettings:
    playback_end: int = 15
    playback_start: int = 0
    sync_rate: int = 3
    playback_direction: int = 0  # 0=Fwd, 1=Rev, 2=PingPong, 3=Random

@dataclass
class FXSettings:
    reverb_sends: list[int]      # 8 tracks: S1,S2,D1,D2,D3,D4,M1,M2
    reverb_type: int
    reverb_decay: int
    reverb_damping: int
    delay_sends: list[int]       # 8 tracks
    delay_time: int
    delay_sync: int
    delay_feedback: int
    delay_width: int
    delay_lr_ratio: int
    delay_slew: int
    fx_bypass: bool
    sidechain_s1: SidechainSettings
    sidechain_s2: SidechainSettings
    mixer_levels: list[int]      # S1, S2, M1, M2
    mixer_pans: list[int]        # S1, S2, M1, M2

@dataclass
class NCSFile:
    header: NCSHeader
    timing: NCSTimingSection
    scenes: list[Scene]                # 16 scenes
    project_settings: NCSProjectSettings
    fx: FXSettings
    synth1_patch: bytes                # 340 bytes
    synth2_patch: bytes                # 340 bytes
    drum_configs: list[DrumTrackConfig]  # 4 drums
    synth_patterns: list[SynthPattern] # 16 (2 synths x 8 patterns)
    drum_patterns: list[DrumPattern]   # 32 (4 drums x 8 patterns)
    midi_patterns: list[SynthPattern]  # 16 (2 MIDI x 8 patterns)
```

### Functions

```python
def parse_ncs(path: str | Path) -> NCSFile
def serialize_ncs(ncs: NCSFile) -> bytes
def write_ncs(ncs: NCSFile, path: str | Path) -> None

def get_synth_pattern(ncs: NCSFile, synth_idx: int, pattern_idx: int) -> SynthPattern
def get_drum_pattern(ncs: NCSFile, drum_idx: int, pattern_idx: int) -> DrumPattern
def get_midi_pattern(ncs: NCSFile, midi_idx: int, pattern_idx: int) -> SynthPattern

def set_scene(ncs: NCSFile, scene_idx: int, scene: Scene) -> None
def set_scene_chain(ncs: NCSFile, chain: ChainEntry) -> None
```

---

## NCS Transfer (`circuit_tracks.ncs_transfer`)

SysEx file transfer protocol for sending projects and patches to the device.

```python
def send_ncs_project(midi: MidiConnection, ncs_data: bytes, slot: int = 0,
                     filename: str | None = None,
                     progress_callback: Callable[[int, int], None] | None = None
                     ) -> dict
```
Send a 160,780-byte NCS project to a device slot (0-63). Returns `{"status", "slot", "filename", "bytes_sent", "blocks", "crc32"}`.

```python
def send_patch_to_slot(midi: MidiConnection, patch_bytes: bytes | list[int],
                       synth: int, slot: int) -> dict
```
Save a 340-byte patch to a flash slot (0-63). Returns `{"status", "synth", "slot", "patch_name", "bytes_sent"}`.

```python
def list_directory(midi: MidiConnection, file_type: int = 0x03,
                   timeout_s: float = 3.0) -> list[dict]
```
List files on device. `file_type`: 0x03=projects, 0x04=patches, 0x05=drum samples. Returns `[{"slot", "filename"}]`.

### Encoding Utilities

```python
def encode_msb_interleave(data: bytes) -> list[int]
def decode_msb_interleave(encoded: list[int]) -> bytes
def int_to_nibbles(value: int, count: int) -> list[int]
def nibbles_to_int(nibbles: list[int]) -> int
def block_address(block_num: int) -> list[int]
def file_id(slot: int) -> list[int]
```

---

## Song (`circuit_tracks.song`)

High-level song format for defining complete projects as structured data.

### Data Types

```python
@dataclass
class SoundConfig:
    # Synth
    preset: str | None = None      # "pad", "bass", "lead", "pluck"
    name: str | None = None
    params: dict[str, int] | None = None
    mod_matrix: list[dict] | None = None
    macros: dict[str, dict] | None = None
    # Drum
    sample: int | None = None      # 0-63
    level: int | None = None
    pitch: int | None = None
    decay: int | None = None
    distortion: int | None = None
    eq: int | None = None
    pan: int | None = None

@dataclass
class FXConfig:
    reverb: dict[str, int]
    delay: dict[str, int]
    reverb_sends: dict[str, int]   # per track
    delay_sends: dict[str, int]
    sidechain: dict[str, dict]     # per synth
    reverb_preset: str | int | None
    delay_preset: str | int | None

@dataclass
class MixerConfig:
    level: int = 100               # 0-127
    pan: int = 64                  # 0-127 (64=center)

@dataclass
class SongData:
    name: str = "Song"
    bpm: int = 120                 # 40-240
    swing: int = 50                # 20-80
    color: int = 8                 # 0-13
    scale_root: str = "C"
    scale_type: str = "chromatic"
    sounds: dict[str, SoundConfig]
    fx: FXConfig
    mixer: dict[str, MixerConfig]  # synth1, synth2
    patterns: dict[str, PatternData]
    song: list[str]                # pattern names in order (max 16)
```

### Functions

```python
def parse_song(d: dict) -> SongData
```
Parse and validate a song dict. Raises `ValueError` on invalid input.

```python
def load_song_to_sequencer(song: SongData, engine: SequencerEngine,
                           midi: MidiConnection) -> dict
```
Load song into live sequencer and configure sounds/FX via MIDI. Returns `{"patterns_loaded": [...], "sounds_configured": [...]}`.

```python
def song_to_ncs(song: SongData, template_path: Path | None = None) -> bytes
```
Convert SongData to 160,780-byte NCS binary.

```python
def export_song_to_device(song: SongData, midi: MidiConnection, slot: int = 0,
                          filename: str | None = None,
                          progress_callback: Callable[[int, int], None] | None = None
                          ) -> dict
```
Convert song to NCS and send to device. Returns transfer result dict.

### Per-Step Automation (P-Locks)

The song format supports per-step parameter locks at both step and track level.

**Synth/MIDI tracks** -- step-level macros (convenience):
```json
{"steps": {"0": {"note": 48, "macros": {"5": 30, "8": 100}}}}
```

**Synth/MIDI tracks** -- track-level macros (works on any step, supports micro-steps):
```json
{"macros": {"5": {"0": 0, "1": 30, "2.5": 60, "15": 127}}}
```

**Synth/MIDI tracks** -- mixer/FX automation:
```json
{"mixer": {"level": {"0": 50, "15": 127}, "pan": {"0": 0, "15": 127},
           "reverb_send": {"0": 0, "15": 100}, "delay_send": {"0": 100, "15": 0}}}
```

**Drum tracks** -- parameter automation:
```json
{"params": {"pitch": {"0": 30, "8": 90}, "decay": {"0": 127, "15": 20},
            "distortion": {"0": 0, "15": 80}, "eq": {"0": 64, "15": 100},
            "reverb_send": {"0": 0, "15": 100}, "delay_send": {"0": 100, "15": 0},
            "level": {"0": 60, "15": 127}, "pan": {"0": 0, "15": 127}}}
```

**Position format**: Integer keys = step index (fills all micro-ticks). Float keys = micro-step resolution (e.g. `"2.5"` = step 2, halfway). For a 16-step pattern, each step has 12 sub-positions (192 total = 6 lanes x 32).
