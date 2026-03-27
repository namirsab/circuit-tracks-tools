# Plan: Reverse Engineer NCS Format & Build Parser/Writer

## Context

The goal is to understand the Novation Circuit Tracks `.ncs` project file format so we can programmatically read and write project files. This enables generating patterns/sequences from code and loading them onto the Circuit Tracks via its SD card, bypassing real-time MIDI step-by-step entry.

Three example files compared:
- `Empty.ncs` — blank project, 120 BPM, patches "AfterHours" (synth1) + "AlteredState" (synth2)
- `2 notes.ncs` — same project + C4 on synth1 step 1, C6 on synth2 step 1 (default vel/gate)
- `WithDrums.ncs` — same patches + C4 on synth1 (vel=64, gate=4 steps) + C6 on synth2 (default) + drum hits on step 1 & 9 of all 4 drum tracks, tempo=122 BPM

### External Resources
- **WASM validator** — disassembled to extract complete data model field names (see below)
  - Source: `circuit-tracks-project-validator-e83caa525f3f586024af78cebcb33ad4.wasm`
  - From: https://github.com/userx14/CircuitTracksReverseEngineering
- **Firmware RE gist**: https://gist.github.com/userx14/664f5e74cc7ced8c29d4a0434ab7be98
- **No existing NCS parser library** — this is novel work

---

## NCS Format Specification

**Source**: Binary analysis of 3 test files + WASM validator disassembly (field names from `circuit-tracks-project-validator.wasm`)

### File size: 160,780 bytes (fixed)

### Header (0x00 - 0x33, 52 bytes)
| Offset | Size | WASM Field | Value | Notes |
|--------|------|-----------|-------|-------|
| 0x00 | 4 | `header.signature` | `USER` | ASCII magic |
| 0x04 | 4 | `header.totalSessionSize` | 160780 | LE uint32 |
| 0x08 | 4 | `header.sessionColour` | 1 | Project LED colour (validated "out of range") |
| 0x0C | 4 | `header.featureFlags` | 0x0B=11 | `.midiTracks` must be set; `.reserved` must be 0 |
| 0x10 | 32 | (name) | `Empty` | ASCII, space-padded |
| 0x30 | 4 | (unknown) | 0 | |

### Timing Section (0x34 - 0x38, 5 bytes)
| Offset | WASM Field | Value | Notes |
|--------|-----------|-------|-------|
| 0x34 | `timing.tempo` | 120 | BPM (40-240, confirmed 120→0x78, 122→0x7A) |
| 0x35 | `timing.swing` | 50 | Swing (20-80, 0x32=50 ✓) |
| 0x36 | `timing.swingSyncRate` | 3 | Swing sync rate |
| 0x37 | `timing.spare1` | 0 | Must be 0 |
| 0x38 | `timing.spare2` | 0 | Must be 0 |

### Scenes + Chains Region (0x39 - 0x2E3, 683 bytes, all zeros in default)
Contains (all default to 0):
- `scenes[16].patternChains[8]` — each: `{start, end, padding}` (padding must be 0)
- `sceneChain` — `{start, end, padding}`
- `patternChains[8]` — per track: `{start, end, padding}`
- `scaleRoot` — root note (0-11 = C to B)
- `scaleType` — scale (0-15, 16 scales)
- `delayPreset` — delay preset index (0-15)
- `reverbPreset` — reverb preset index (0-7)
- `midiKeyboardOctaves[2]` — octave per MIDI track

Exact byte offsets within this region TBD — need test files with non-default values.

### Pattern Data Region: 0x2E4 - ~0x26CFC

64 pattern blocks organized by track (8 patterns per track):

| Blocks | Track | Block Size | Step Format |
|--------|-------|------------|-------------|
| 0-7 | Synth 1 | 3240 bytes | 28 bytes/step (confirmed) |
| 8-15 | Synth 2 | 3240 bytes | 28 bytes/step (confirmed) |
| 16-47 | Drums 1-4 | 1704 bytes | Row-based (confirmed) |
| 48-63 | MIDI 1-2 | 3240 bytes | 28 bytes/step (same as synth) |

Each synth/MIDI pattern block (3240 bytes):
- **4 bytes**: prefix/padding (zeros, or 0xFFFFFFFF at track boundaries)
- **896 bytes**: 32 steps × 28 bytes (WASM: `steps[32]`)
- **40 bytes**: pattern settings (see below)
- **2300 bytes**: 0xFF = automation data region (WASM: `automation[N].values[32]`)

### Synth/MIDI Step Format (28 bytes) — CONFIRMED via WASM

```
Bytes 0-3: stepInfo
  [0] assignedNoteMask — bitmask for 6 note slots (0x01=note0 only, 0x3F=all 6)
  [1] probability      — 0-7 (8 levels: 0=12.5%, 7=100%. Default=0x07)
  [2] reserved         — 0x00
  [3] reserved         — 0x00

Bytes 4-27: notes[6], 4 bytes each (6-voice polyphony!)
  Per note:
  [0] noteNumber — MIDI note 0-127 (C4=60, C6=84)
  [1] gate       — gate length in micro-ticks (6 per step)
                   0x06=1 step (default), 0x18=4 steps (confirmed)
  [2] delay      — micro-step offset 0-5 (0=on beat)
  [3] velocity   — MIDI velocity 0-127 (default 0x60=96)

Empty note slot default: [0x00, 0x00, 0x00, 0x60]
```

### Synth/MIDI Pattern Settings (40 bytes, after step data)
```
  [0] playbackRange.end    — 0x0F=15 (0-indexed, default=step 16)
  [1] playbackRange.start  — 0x00=0 (0-indexed, default=step 1)
  [2] syncRate             — 0x03 (default, 8 possible values)
  [3] playbackDirection    — 0x00 (0=Forward, 1=Reverse, 2=PingPong, 3=Random)
  [4-39] zeros (reserved / automation lane metadata?)
```

### Drum Pattern Format (1704 bytes) — CONFIRMED via WASM

Each drum pattern block:
- **FF padding** (fills start of block)
- **16 bytes**: header (byte 8 = 0x20 = step count indicator)
- **32 bytes**: `velocity[32]` — 0x60=default active vel, 0x00=empty
- **32 bytes**: `probabilities[32]` — 0x07=100% (0-7, 8 levels)
- **32 bytes**: `drumChoice[32]` — 0xFF=default sample (sample flip per step!)
- **32 bytes**: `drumRhythm[32]` — 0x01=active trigger, 0x00=inactive
- **40 bytes**: pattern settings (same format as synth: playbackRange, syncRate, direction)

Additional WASM fields for drum patterns: `automation[N].values[32]` (in FF region)

### Drum Step Rows (4 × 32 bytes after header)
```
  Row 0: Velocity per step (0x60=default active velocity, 0x00=empty)
  Row 1: Probability per step (0x07=level 8=100% default, 0-indexed)
  Row 2: 0xFF for all steps (micro-step/sample-flip bitmask?)
  Row 3: Trigger flag per step (0x01=active, 0x00=inactive)
```

### Tail Section (0x26CFC - 0x2740C, 1808 bytes) — DECODED

#### Preamble (0x26CFC - 0x26D13, 24 bytes)
```
  [0-19] 20 zeros — synthTrackInfo / drumMuteStates / defaultDrumChoices? (all default=0)
  [20-21] 0x40 0x40 = 64, 64 — synth pans? or part of synthTrackInfo
  [22-23] 0x00 0x00 — unknown
```
WASM fields in this region (exact offsets TBD):
- `synthTrackInfo[2]` — `.patch`, `.muteState`, `.sidechainPreset`
- `drumMuteStates[4]`
- `defaultDrumChoices[4]` — default drum sample per track
- `midiTrackInfo[2]` — `.patch`, `.muteState`, `.sidechainPreset`

#### Synth 1 Patch (0x26D14 - 0x26E67, 340 bytes)
Full synth patch per Programmer's Reference format:
- Bytes 0-15: Patch name (ASCII, "AfterHours")
- Bytes 16-17: Category, Genre
- Bytes 18-31: Reserved
- Bytes 32+: Voice, Oscillator, Filter, Envelope, LFO, Mod Matrix, Macro config

#### Synth 2 Patch (0x26E68 - 0x26FBB, 340 bytes)
Same format, name = "AlteredState"

#### Drum Track Configs (0x26FBC - 0x26FE7, 44 bytes = 4 × 11)
Per drum track (confirmed against Programmer's Reference defaults):
```
  [0]  patch_select  — 0=Kick1, 2=Snare1, 4=ClosedHat1, 8=Perc1 ✓
  [1]  level         — 100 (0x64) ✓
  [2]  pitch         — 64 (0x40, center=0) ✓
  [3]  unknown       — 127 (0x7F) — possibly EQ max or filter default
  [4]  decay         — 0 ✓
  [5]  EQ            — 64 (0x40, center) ✓
  [6]  pan           — 64 (0x40, center) ✓
  [7]  distortion    — 0 ✓
  [8]  reverb_send   — 0 ✓
  [9]  delay_send    — 0 ✓
  [10] unknown       — 0
```

#### MIDI/Audio Settings (0x26FE8 - 0x26FEF, 8 bytes) — zeros

#### Reverb Settings (0x26FF0 - 0x26FF2, 3 bytes)
```
  [0] reverb_type    — 2 (Small Room, NRPN 1:18 default=2) ✓
  [1] reverb_decay   — 64 (NRPN 1:19 default=64) ✓
  [2] reverb_damping — 64 (NRPN 1:20 default=64) ✓
```

#### Gap (0x26FF3 - 0x26FFF, 13 zeros)

#### Delay Settings (0x27000 - 0x27005, 6 bytes)
```
  [0] delay_time         — 64 (NRPN 1:6 default=64) ✓
  [1] delay_time_sync    — 20 (NRPN 1:7 default=20) ✓
  [2] delay_feedback     — 64 (NRPN 1:8 default=64) ✓
  [3] delay_width        — 127 (NRPN 1:9 default=127) ✓
  [4] delay_lr_ratio     — 4 (NRPN 1:10 default=4) ✓
  [5] delay_slew_rate    — 5 (NRPN 1:11 default=5) ✓
```

#### Sidechain Settings (0x2700A - 0x2701B, 4 × 5 bytes)
4 blocks of: `[hold=50, decay=70, 0, 0, 0]`
Matches sidechain hold (NRPN 2:57 default=50) and decay (NRPN 2:58 default=70)

#### Mixer: Drum Levels + Pans (0x2701C - 0x27023, 8 bytes)
```
  [0-3] drum levels: 100, 100, 100, 100 (4 × 0x64) ✓
  [4-7] drum pans:   64, 64, 64, 64   (4 × 0x40, center) ✓
```

#### Trailing Zeros (0x27024 - 0x2740B, 1000 bytes)
Reserved / scene chain data / padding to fixed file size

---

## Project Features Inventory (from User Guide & Programmer's Reference)

All features below are saved per-project in the NCS file. Features marked with ✅ are already mapped in our binary analysis. Features marked with ❓ need additional test files or binary analysis to locate.

### Global Project Settings
- ✅ **Project name** (32 bytes ASCII at 0x10)
- ✅ **BPM/Tempo** (`timing.tempo` at 0x34, 40-240, default 120)
- ✅ **Swing** (`timing.swing` at 0x35, 20-80, default 50)
- ✅ **Swing sync rate** (`timing.swingSyncRate` at 0x36, default 3)
- ✅ **Session colour** (`header.sessionColour` at 0x08, default 1)
- ✅ **Feature flags** (`header.featureFlags` at 0x0C, default 0x0B)
- ❓ **Scale type** (`scaleType`, 0-15, 16 scales — in scenes/chains region 0x39-0x2E3)
- ❓ **Scale root** (`scaleRoot`, 0-11 = C to B — in scenes/chains region)
- ❓ **Delay preset** (`delayPreset`, 0-15 — in scenes/chains region or tail)
- ❓ **Reverb preset** (`reverbPreset`, 0-7 — in scenes/chains region or tail)

### Per-Track Mixer Settings
- ✅ **Drum levels** (4 × byte at 0x2701C, default 100)
- ✅ **Drum pans** (4 × byte at 0x27020, default 64=center)
- ✅ **Synth pans** (2 bytes at 0x26D10, default 64=center)
- ❓ **Synth levels** (not found yet — may be in the 8 zero bytes at 0x26FE8, or preamble)
- ❓ **MIDI track levels/pans** (not found — likely in zeros regions)
- ❓ **Reverb send levels** per track (0-127, default 0 — in zeros regions)
- ❓ **Delay send levels** per track (0-127, default 0 — in zeros regions)
- ❓ **Mute state** per track (not saved? or in zeros)

### Per-Synth Track (2 tracks)
- ✅ **Full synth patch data** (340 bytes each at 0x26D14 and 0x26E68 — osc, filter, envelope, LFO, mod matrix, macros)
- ❓ **Macro knob positions** (may be encoded within the 340-byte patch)

### Per-Drum Track (4 tracks)
- ✅ **Sample/patch selection** (11-byte config at 0x26FBC: drum1=0, drum2=2, drum3=4, drum4=8)
- ✅ **Level** (byte 1: default 100)
- ✅ **Pitch** (byte 2: default 64=center)
- ✅ **Decay** (byte 4: default 0)
- ✅ **EQ** (byte 5: default 64=center)
- ✅ **Pan** (byte 6: default 64=center)
- ✅ **Distortion** (byte 7: default 0)
- ✅ **Reverb send** (byte 8: default 0)
- ✅ **Delay send** (byte 9: default 0)

### Per-Pattern Settings (in 40-byte block per pattern, WASM-confirmed)
- ✅ **`playbackRange.end`** (byte 0, 0-indexed, default=0x0F=15 → step 16)
- ✅ **`playbackRange.start`** (byte 1, 0-indexed, default=0x00 → step 1)
- ✅ **`syncRate`** (byte 2, default=0x03, 8 possible values)
- ✅ **`playbackDirection`** (byte 3, default=0x00, 0=Fwd/1=Rev/2=PingPong/3=Random)

### Synth/MIDI Step Data (28 bytes, WASM-confirmed field names)
- ✅ **`stepInfo.assignedNoteMask`** (byte 0) — bitmask for 6 note slots (0x01=1 note, 0x3F=all 6)
- ✅ **`stepInfo.probability`** (byte 1) — 0-7, 8 levels (0x07=100%)
- ✅ **`notes[6].noteNumber`** (byte 0 of each 4-byte note) — MIDI note 0-127
- ✅ **`notes[6].gate`** (byte 1) — micro-ticks (6 per step, 1-96)
- ✅ **`notes[6].delay`** (byte 2) — micro-step offset 0-5
- ✅ **`notes[6].velocity`** (byte 3) — MIDI velocity 0-127 (default 96=0x60)
- ✅ **Automation** — `automation[N].values[32]` in the 2300-byte FF region (0xFF = no data)
- ❓ **Tie-forward** — not mentioned in WASM validator; may be encoded in gate value or separate field

### Drum Step Data (4 rows of 32 bytes, WASM-confirmed)
- ✅ **`velocity[32]`** (Row 0: 0x60=default, 0x00=empty)
- ✅ **`probabilities[32]`** (Row 1: 0x07=100%)
- ✅ **`drumChoice[32]`** (Row 2: 0xFF=default sample — sample flip per step!)
- ✅ **`drumRhythm[32]`** (Row 3: 0x01=trigger, 0x00=off)
- ✅ **Automation** — `automation[N].values[32]` in FF region

### FX Settings (in tail section)
- ✅ **Reverb type** (0x26FF0: default=2=Small Room)
- ✅ **Reverb decay** (0x26FF1: default=64)
- ✅ **Reverb damping** (0x26FF2: default=64)
- ✅ **Delay time** (0x27000: default=64)
- ✅ **Delay time sync** (0x27001: default=20)
- ✅ **Delay feedback** (0x27002: default=64)
- ✅ **Delay width** (0x27003: default=127)
- ✅ **Delay L/R ratio** (0x27004: default=4)
- ✅ **Delay slew rate** (0x27005: default=5)
- ❓ **FX Bypass** (on/off — likely in zeros region)
- ❓ **Master compressor** (on/off)

### Sidechain Settings (0x2700A, 4 × 5 bytes)
- ✅ **Hold** (default=50) — per sidechain block
- ✅ **Decay** (default=70) — per sidechain block
- ❓ **Source, Attack, Depth** (zeros in default — present but 0)

### Scenes (16 scenes)
- ❓ **Scene assignments** — likely in the 1000 trailing zero bytes (0x27024-end) or the 20-byte preamble at 0x26CFC

### MIDI Track Specifics
- ❓ **MIDI template selection** (1-8 per MIDI track — likely in zeros regions)
- ❓ **MIDI channel assignment** per track

---

## Implementation Plan

### Phase 1: Core Data Structures
Create `src/circuit_mcp/ncs_parser.py`:

```python
@dataclass
class NCSHeader:
    magic: bytes        # b'USER'
    file_size: int
    version: int
    unknown_0c: int
    name: str           # 32 chars, space-padded
    unknown_30: int
    bpm: int            # 0-255
    unknown_35: bytes   # 3 bytes

@dataclass
class SynthStep:
    active: bool
    note: int           # MIDI note 0-127
    gate: int           # micro-ticks (6 per step)
    velocity: int       # 0-127
    params: list[bytes] # 5 x 4-byte automation params (preserved raw)

@dataclass
class DrumStep:
    active: bool
    velocity: int       # 0x60 = default

@dataclass
class SynthPattern:
    steps: list[SynthStep]  # 32 steps
    prefix: bytes           # 4 bytes before steps
    metadata: bytes         # 40 bytes after steps

@dataclass
class DrumPattern:
    steps: list[DrumStep]   # 32 steps
    raw_data: bytes         # preserve entire block for round-trip

@dataclass
class NCSFile:
    header: NCSHeader
    padding: bytes                    # 0x38-0x2E3
    synth_patterns: list[SynthPattern]  # 16 (2 tracks × 8 patterns)
    drum_patterns: list[DrumPattern]    # 32 (4 tracks × 8 patterns)
    midi_patterns: list[SynthPattern]   # 16 (2 tracks × 8 patterns)
    tail: bytes                         # everything after last pattern block
```

### Phase 2: Parser
- `parse_ncs(path: str) -> NCSFile` — read and decode
- Parse header, validate magic `USER`
- Walk 64 pattern blocks using known offsets and sizes
- Decode synth steps (note, gate, velocity, automation params)
- Preserve drum blocks as raw bytes + decoded steps for the known fields
- Capture tail verbatim

### Phase 3: Writer
- `write_ncs(ncs: NCSFile, path: str)` — encode and write
- Reconstruct header with correct file size and BPM
- Write pattern blocks with correct FF padding per block size
- Append tail verbatim

### Phase 4: Tests
Create `tests/test_ncs_parser.py`:
1. **Round-trip test**: parse each .ncs → write to temp → byte-compare (must be identical)
2. **Note decode test**: parse "2 notes.ncs", verify synth1 step 0 = C4/vel=98/gate=6, synth2 step 0 = C6/vel=62/gate=6
3. **Drum decode test**: parse "WithDrums.ncs", verify drum steps 0 and 8 active on all 4 tracks
4. **BPM test**: parse WithDrums, verify bpm=122
5. **Note insertion test**: parse Empty, add C4 note to synth1 step 0, compare with "2 notes.ncs" (match except name + synth2 note)

---

## Files to Create
- `src/circuit_mcp/ncs_parser.py` — parser and writer
- `tests/test_ncs_parser.py` — round-trip and decode tests

## Verification
1. `python -m pytest tests/test_ncs_parser.py -v` — all tests pass
2. Round-trip all 3 example files byte-for-byte
3. (Future) Generate a project, load onto Circuit Tracks via SD card, verify playback
