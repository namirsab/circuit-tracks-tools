# Novation Circuit Tracks .ncs Project File Format

**Status**: Unofficial, reverse-engineered specification
**Version**: 1.0 (March 2026)
**Applies to**: Circuit Tracks firmware (tested with firmware shipping as of 2025)

## Sources

This specification was reverse-engineered from:

- **Binary analysis** of 19+ real .ncs project files created on hardware, with byte-for-byte round-trip verification
- **WASM validator disassembly** (`circuit-tracks-project-validator.wasm` from [CircuitTracksReverseEngineering](https://github.com/userx14/CircuitTracksReverseEngineering)) -- provides authoritative field names
- **Firmware RE gist** by userx14: https://gist.github.com/userx14/664f5e74cc7ced8c29d4a0434ab7be98
- **Captured SysEx transfers** from Novation Components (WebMIDI interception)

### Confidence Annotations

- **Confirmed** -- verified by creating test files on hardware and round-trip testing (parse, serialize, byte-compare)
- **Inferred** -- field name or purpose derived from WASM validator symbols, not yet independently tested
- **Unknown** -- observed in binary data, purpose not determined

---

## 1. Overview

| Property | Value |
|----------|-------|
| File extension | `.ncs` |
| File size | 160,780 bytes (fixed) |
| Magic signature | `USER` (ASCII, 4 bytes) |
| Byte order | Little-endian for multi-byte integers, unless noted |

All .ncs files are exactly 160,780 bytes. Files of any other size are invalid.

### File Layout

| Region | Offset | Size (bytes) | Description |
|--------|--------|-------------|-------------|
| Header | 0x00000 - 0x00033 | 52 | Signature, file size, project color, name |
| Timing | 0x00034 - 0x00038 | 5 | BPM, swing, swing sync rate |
| Scenes & Chains | 0x00039 - 0x002E3 | 683 | 16 scenes, scene chain, pattern chains |
| Pattern Data | 0x002E4 - 0x26CFB | 158,744 | 64 pattern blocks (synth, drum, MIDI) |
| Tail | 0x26CFC - 0x2740B | 1,808 | Project settings, synth patches, drum configs, FX, mixer |

---

## 2. Header (0x00 - 0x33, 52 bytes)

| Offset | Size | WASM Field | Type | Default | Description |
|--------|------|-----------|------|---------|-------------|
| 0x00 | 4 | `header.signature` | ASCII | `USER` | Magic signature. **Confirmed** |
| 0x04 | 4 | `header.totalSessionSize` | LE uint32 | 160780 | Always 160,780. **Confirmed** |
| 0x08 | 4 | `header.sessionColour` | LE uint32 | 1 | Must be 0 or 1 (WASM rejects other values). Purpose unknown. **Unknown** |
| 0x0C | 4 | `header.featureFlags` | LE uint32 | 8 | Project LED color index (0-13). See color table below. Values 14-15 also accepted by WASM. **Confirmed** |
| 0x10 | 32 | -- | ASCII | (space-padded) | Project name, right-padded with spaces. **Confirmed** |
| 0x30 | 4 | -- | bytes | 0x00000000 | Unknown, always zeros. **Unknown** |

### Project Color Table

| Value | Name | RGB (from Components UI) |
|-------|------|--------------------------|
| 0 | Red | (251, 53, 53) |
| 1 | Rose | (250, 52, 116) |
| 2 | Peach | (250, 130, 125) |
| 3 | Orange | (250, 163, 52) |
| 4 | Sand | (250, 195, 125) |
| 5 | Yellow | (250, 242, 52) |
| 6 | Khaki | (219, 250, 125) |
| 7 | Lime | (161, 239, 26) |
| 8 | Green | (52, 250, 55) |
| 9 | Teal | (75, 250, 134) |
| 10 | Cyan | (52, 175, 250) |
| 11 | Blue | (52, 87, 250) |
| 12 | Purple | (110, 52, 250) |
| 13 | Pink | (250, 75, 206) |

---

## 3. Timing Section (0x34 - 0x38, 5 bytes)

| Offset | Size | WASM Field | Range | Default | Description |
|--------|------|-----------|-------|---------|-------------|
| 0x34 | 1 | `timing.tempo` | 40-240 | 120 | BPM. **Confirmed** |
| 0x35 | 1 | `timing.swing` | 20-80 | 50 | Swing percentage. **Confirmed** |
| 0x36 | 1 | `timing.swingSyncRate` | 0-35 | 3 | Swing sync rate. **Confirmed** |
| 0x37 | 1 | `timing.spare1` | -- | 0 | Reserved, must be 0. **Inferred** |
| 0x38 | 1 | `timing.spare2` | -- | 0 | Reserved, must be 0. **Inferred** |

---

## 4. Scenes & Chains (0x39 - 0x2E3, 683 bytes)

All values default to 0 in a new project.

### Layout (offsets relative to 0x39)

| Relative Offset | Size | Description |
|----------------|------|-------------|
| +0 | 640 | 16 scenes, 40 bytes each |
| +640 | 8 | Scene state |
| +648 | 4 | Scene chain entry |
| +652 | 32 | 8 pattern chain entries (4 bytes each) |

**Note**: The D4 (Drum 4) pattern chain entry at +680 (absolute 0x2E1) is 4 bytes (0x2E1-0x2E4) and extends 1 byte past the nominal region boundary at 0x2E3, overlapping with the pattern data prefix area.

### Scene Format (40 bytes)

Each of the 16 scenes contains:

| Byte Offset | Size | Description |
|-------------|------|-------------|
| 0 | 8 | Scene header (purpose partially unknown) |
| 8 | 32 | 8 track chain entries, 4 bytes each |

Track order within a scene: Synth 1, Synth 2, MIDI 1, MIDI 2, Drum 1, Drum 2, Drum 3, Drum 4.

### Chain Entry Format (4 bytes)

Used for scene chains, pattern chains, and per-scene track assignments:

| Byte | Description |
|------|-------------|
| 0 | End index (0-7, inclusive) |
| 1 | Start index (0-7) |
| 2 | Padding (must be 0) |
| 3 | Padding (must be 0) |

When start = end = 0, the chain is inactive (single pattern mode).

---

## 5. Pattern Data (0x2E4 - 0x26CFB)

### Block Organization

The pattern data region contains 64 pattern blocks, 8 per track:

| Block Indices | Track | Block Type | Block Size |
|---------------|-------|------------|------------|
| 0-7 | Synth 1 | Synth | 3,240 bytes |
| 8-15 | Synth 2 | Synth | 3,240 bytes |
| 16-23 | Drum 1 | Drum | 1,704 bytes |
| 24-31 | Drum 2 | Drum | 1,704 bytes |
| 32-39 | Drum 3 | Drum | 1,704 bytes |
| 40-47 | Drum 4 | Drum | 1,704 bytes |
| 48-55 | MIDI 1 | Synth | 3,240 bytes |
| 56-63 | MIDI 2 | Synth | 3,240 bytes |

### Pattern Settings Offset Table

Each pattern block contains a 40-byte settings region at a known absolute offset. The step data immediately precedes the settings. These offsets are not trivially computable due to the mixed synth/drum block sizes; they must be used as a lookup table.

| Block | Offset | Block | Offset | Block | Offset | Block | Offset |
|-------|--------|-------|--------|-------|--------|-------|--------|
| 0 | 0x00664 | 16 | 0x0CDF4 | 32 | 0x13874 | 48 | 0x1A5FC |
| 1 | 0x0130C | 17 | 0x0D49C | 33 | 0x13F1C | 49 | 0x1B2A4 |
| 2 | 0x01FB4 | 18 | 0x0DB44 | 34 | 0x145C4 | 50 | 0x1BF4C |
| 3 | 0x02C5C | 19 | 0x0E1EC | 35 | 0x14C6C | 51 | 0x1CBF4 |
| 4 | 0x03904 | 20 | 0x0E894 | 36 | 0x15314 | 52 | 0x1D89C |
| 5 | 0x045AC | 21 | 0x0EF3C | 37 | 0x159BC | 53 | 0x1E544 |
| 6 | 0x05254 | 22 | 0x0F5E4 | 38 | 0x16064 | 54 | 0x1F1EC |
| 7 | 0x05EFC | 23 | 0x0FC8C | 39 | 0x1670C | 55 | 0x1FE94 |
| 8 | 0x06BA4 | 24 | 0x10334 | 40 | 0x16DB4 | 56 | 0x20B3C |
| 9 | 0x0784C | 25 | 0x109DC | 41 | 0x1745C | 57 | 0x217E4 |
| 10 | 0x084F4 | 26 | 0x11084 | 42 | 0x17B04 | 58 | 0x2248C |
| 11 | 0x0919C | 27 | 0x1172C | 43 | 0x181AC | 59 | 0x23134 |
| 12 | 0x09E44 | 28 | 0x11DD4 | 44 | 0x18854 | 60 | 0x23DDC |
| 13 | 0x0AAEC | 29 | 0x1247C | 45 | 0x18EFC | 61 | 0x24A84 |
| 14 | 0x0B794 | 30 | 0x12B24 | 46 | 0x195A4 | 62 | 0x2572C |
| 15 | 0x0C43C | 31 | 0x131CC | 47 | 0x19C4C | 63 | 0x263D4 |

### Synth/MIDI Pattern Block (3,240 bytes)

Each synth or MIDI pattern block contains:

| Component | Size | Description |
|-----------|------|-------------|
| Pre-data | variable | 0xFF padding / automation data from previous block |
| Step data | 896 bytes | 32 steps x 28 bytes |
| Settings | 40 bytes | Playback range, sync rate, direction |
| Post-data | variable | Automation region (0xFF = no automation data) |

The step data ends at the settings offset. Step data starts at `settings_offset - 896`.

### Synth/MIDI Step Format (28 bytes)

**Confirmed** via WASM validator field names and binary analysis.

```
Offset  Size  Field
------  ----  -----
0x00    1     stepInfo.assignedNoteMask
0x01    1     stepInfo.probability
0x02    2     stepInfo.reserved (must be 0x00 0x00)
0x04    4     notes[0] (noteNumber, gate, delay, velocity)
0x08    4     notes[1]
0x0C    4     notes[2]
0x10    4     notes[3]
0x14    4     notes[4]
0x18    4     notes[5]
```

#### Step Info

| Field | Size | Range | Description |
|-------|------|-------|-------------|
| assignedNoteMask | 1 byte | 0x00-0x3F | Bitmask for 6 note slots. Bit 0 = note[0] active, bit 5 = note[5] active. 0x00 = empty step. |
| probability | 1 byte | 0-7 | Step trigger probability. See Appendix A. Default: 7 (100%). |
| reserved | 2 bytes | -- | Must be 0x00 0x00. |

#### Note Slot (4 bytes)

The Circuit Tracks supports 6-voice polyphony per step. Each note slot:

| Byte | Field | Range | Default | Description |
|------|-------|-------|---------|-------------|
| 0 | noteNumber | 0-127 | 0 | Note number — **offset from standard MIDI by +12** (see note below). 0 when slot is unused. |
| 1 | gate | 0-255 | 0 | Gate length in micro-ticks. 6 micro-ticks = 1 step. |
| 2 | delay | 0-5 | 0 | Micro-step offset (0 = on the beat). |
| 3 | velocity | 0-127 | 96 (0x60) | MIDI velocity. |

**NCS note number vs MIDI note number**: The Circuit Tracks internal sequencer plays NCS note numbers one octave (12 semitones) lower than the same note number received via external MIDI. For example, NCS noteNumber 60 plays the same pitch as external MIDI note 48. To store a note that should sound like MIDI note N, write N + 12 to the NCS. This was confirmed experimentally: a note recorded on the hardware as middle C stores noteNumber 60 in the NCS, but sending MIDI note 60 externally to the same patch produces a pitch one octave higher. The `keyboard_octave` patch parameter (byte 35) does NOT affect internal sequencer playback — tested with values 64 and 69 producing identical results. This appears to be undocumented Novation behavior.

**Gate encoding**: Each sequencer step is divided into 6 micro-ticks. A gate value of 6 means exactly 1 step long. A gate of 24 (0x18) means 4 steps. Values exceeding 192 (32 steps x 6) represent tied/sustained notes that extend beyond the pattern.

**Empty note slot**: `[0x00, 0x00, 0x00, 0x60]` (note=0, gate=0, delay=0, velocity=96).

### Pattern Settings (40 bytes)

**Confirmed**. Located at the offsets listed in the settings offset table above.

| Byte | WASM Field | Range | Default | Description |
|------|-----------|-------|---------|-------------|
| 0 | `playbackRange.end` | 0-31 | 15 | Last step (0-indexed). 15 = 16-step pattern. |
| 1 | `playbackRange.start` | 0-31 | 0 | First step (0-indexed). |
| 2 | `syncRate` | 0-7 | 3 | Sync rate (8 possible values). See Appendix B. |
| 3 | `playbackDirection` | 0-3 | 0 | 0=Forward, 1=Reverse, 2=PingPong, 3=Random. |
| 4-39 | -- | -- | 0 | Reserved (zeros). |

### Drum Pattern Block (1,704 bytes)

Each drum pattern block contains:

| Component | Size | Description |
|-----------|------|-------------|
| Pre-data | variable | 0xFF padding / automation data from previous block |
| Header | 16 bytes | Block header (byte 8 = 0x20 indicates 32-step pattern) |
| Velocity row | 32 bytes | Per-step velocity values |
| Probability row | 32 bytes | Per-step probability values |
| Drum choice row | 32 bytes | Per-step sample selection |
| Rhythm row | 32 bytes | Per-step trigger on/off |
| Settings | 40 bytes | Same format as synth pattern settings |
| Post-data | variable | Automation region |

The step data begins at `settings_offset - 144` (16-byte header + 4 rows of 32 bytes).

#### Drum Step Rows

Each row contains one byte per step (32 bytes total):

| Row | Field | Active Value | Inactive Value | Description |
|-----|-------|-------------|----------------|-------------|
| Velocity | `velocity[32]` | 0x60 (96, default) | 0x00 | Hit velocity (0-127). **Confirmed** |
| Probability | `probabilities[32]` | 0x07 (100%) | 0x07 | Trigger probability (0-7). See Appendix A. **Confirmed** |
| Drum choice | `drumChoice[32]` | 0xFF (default sample) | 0xFF | Per-step sample selection ("sample flip"). Non-0xFF selects an alternate sample for that step. **Confirmed** |
| Rhythm | `drumRhythm[32]` | 0x01 | 0x00 | Trigger flag: 1 = hit, 0 = rest. **Confirmed** |

### Automation Data (P-Locks)

Automation for block N is stored in block N+1's pre-data region (between block N settings end and block N+1 step data start). The region is a contiguous buffer of 0xFF-filled bytes where non-0xFF values represent per-step parameter locks.

**Synth/MIDI blocks**: 2,304 bytes = 72 lanes × 32 positions.

| Slot | Lanes | Parameter |
|------|-------|-----------|
| 0-7 | 0-47 | Macro knobs 1-8 |
| 8 | 48-53 | Reverb send |
| 9 | 54-59 | Delay send |
| 10 | 60-65 | Level |
| 11 | 66-71 | Pan |

**Drum blocks**: 1,520 bytes = 47.5 lanes × 32 positions.

| Slot | Lanes | Parameter |
|------|-------|-----------|
| 0 | 0-5 | Pitch |
| 1 | 6-11 | Decay |
| 2 | 12-17 | Distortion |
| 3 | 18-23 | EQ |
| 4 | 24-29 | Reverb send |
| 5 | 30-35 | Delay send |
| 6 | 36-41 | Level |
| 7 | 42-47 | Pan (truncated: last 16 bytes missing) |

**Layout per slot**: Each slot has 6 lanes of 32 positions = 192 positions total. These form a continuous buffer where positions map to sub-step timing. For a 16-step pattern: 192 / 16 = 12 sub-positions per step. For 32 steps: 6 sub-positions per step.

**Values**: 0x00-0x7F = locked parameter value. 0xFF = no automation (pass-through). For step-level locks, all sub-positions within the step are set to the same value.

---

## 6. Tail Section (0x26CFC - 0x2740B, 1,808 bytes)

### 6.1 Preamble (0x26CFC - 0x26D13, 24 bytes)

| Offset | Size | Default | Description |
|--------|------|---------|-------------|
| 0x26CFC | 16 | 0 | Track info: synth track info, drum mute states, default drum choices, MIDI track info. Exact sub-field layout partially unknown. **Inferred** |
| 0x26D0C | 1 | 0 | Scale root (0-11). See Appendix D. **Confirmed** |
| 0x26D0D | 1 | 0 | Scale type (0-15). See Appendix E. **Confirmed** |
| 0x26D0E | 1 | 0 | Delay preset index (0-15). **Confirmed** |
| 0x26D0F | 1 | 0 | Reverb preset index (0-7). **Confirmed** |
| 0x26D10 | 2 | 0x40, 0x40 | Synth 1 and Synth 2 pan (0-127, 64=center). **Confirmed** |
| 0x26D12 | 2 | 0 | Unknown (zeros). **Unknown** |

### 6.2 Synth 1 Patch (0x26D14 - 0x26E67, 340 bytes)

Full synth patch in the format defined by the Circuit Tracks Programmer's Reference Guide:

| Byte Range | Size | Description |
|-----------|------|-------------|
| 0-15 | 16 | Patch name (ASCII, space-padded) |
| 16 | 1 | Category |
| 17 | 1 | Genre |
| 18-31 | 14 | Reserved |
| 32+ | 308 | Voice, oscillator, filter, envelope, LFO, mod matrix, and macro configuration parameters |

**Confirmed** -- patches extracted from .ncs files match those retrieved via SysEx patch dump.

### 6.3 Synth 2 Patch (0x26E68 - 0x26FBB, 340 bytes)

Same format as Synth 1 patch.

### 6.4 Drum Track Configs (0x26FBC - 0x26FE7, 44 bytes)

4 drum tracks, 11 bytes each. Track order: Drum 1, Drum 2, Drum 3, Drum 4.

| Byte | Field | Range | Default | Description |
|------|-------|-------|---------|-------------|
| 0 | patch_select | 0-63 | D1=0, D2=2, D3=4, D4=8 | Sample index. **Confirmed** |
| 1 | level | 0-127 | 100 | Track level. **Confirmed** |
| 2 | pitch | 0-127 | 64 | Pitch (64=center/no shift). **Confirmed** |
| 3 | decay | 0-127 | 127 | Decay envelope time (127=full length). **Confirmed** |
| 4 | distortion | 0-127 | 0 | Distortion amount. **Confirmed** |
| 5 | eq | 0-127 | 64 | EQ (64=center/flat). **Confirmed** |
| 6 | pan | 0-127 | 64 | Pan (64=center). **Confirmed** |
| 7 | (unknown) | -- | 0 | Always 0 in observed files. **Unknown** |
| 8 | reverb_send | 0-127 | 0 | Reverb send level. **Confirmed** |
| 9 | delay_send | 0-127 | 0 | Delay send level. **Confirmed** |
| 10 | (unknown) | -- | 0 | Always 0 in observed files. **Unknown** |

Default patch_select values correspond to: Kick (0), Snare (2), Closed HH (4), Perc (8).

### 6.5 Reverb Send Levels (0x26FE8 - 0x26FEF, 8 bytes)

Per-track reverb send levels (0-127, default 0):

| Byte | Track |
|------|-------|
| 0 | Synth 1 |
| 1 | Synth 2 |
| 2 | Drum 1 |
| 3 | Drum 2 |
| 4 | Drum 3 |
| 5 | Drum 4 |
| 6 | MIDI 1 |
| 7 | MIDI 2 |

**Confirmed** via test files with individual send level changes.

### 6.6 Reverb Parameters (0x26FF0 - 0x26FF2, 3 bytes)

| Byte | Field | Range | Default | Description |
|------|-------|-------|---------|-------------|
| 0 | reverb_type | 0-5 | 2 | See Appendix C. Default is "Large Room". **Confirmed** |
| 1 | reverb_decay | 0-127 | 64 | Decay time. **Confirmed** |
| 2 | reverb_damping | 0-127 | 64 | High-frequency damping. **Confirmed** |

### 6.7 Gap (0x26FF3 - 0x26FF7, 5 bytes)

Zeros. Purpose unknown.

### 6.8 Delay Send Levels (0x26FF8 - 0x26FFF, 8 bytes)

Same track order as reverb sends (S1, S2, D1, D2, D3, D4, M1, M2). Default: all 0. **Confirmed**.

### 6.9 Delay Parameters (0x27000 - 0x27005, 6 bytes)

| Byte | Field | Range | Default | Description |
|------|-------|-------|---------|-------------|
| 0 | delay_time | 0-127 | 64 | Delay time. **Confirmed** |
| 1 | delay_sync | 0-35 | 20 | Delay time sync rate. **Confirmed** |
| 2 | delay_feedback | 0-127 | 64 | Feedback amount. **Confirmed** |
| 3 | delay_width | 0-127 | 127 | Stereo width. **Confirmed** |
| 4 | delay_lr_ratio | 0-12 | 4 | L/R time ratio. See Appendix C. **Confirmed** |
| 5 | delay_slew | 0-127 | 5 | Slew rate. **Confirmed** |

### 6.10 Gap (0x27006, 1 byte)

Zero. Purpose unknown.

### 6.11 FX Bypass (0x27007, 1 byte)

| Value | Meaning |
|-------|---------|
| 0 | FX enabled (default) |
| 1 | FX bypassed |

**Confirmed** via test file with FX bypass enabled.

### 6.12 Sidechain Settings

#### Synth 1 Sidechain (0x27008 - 0x2700C, 5 bytes)

| Byte | Field | Range | Default | Description |
|------|-------|-------|---------|-------------|
| 0 | source | 0-4 | 0 | 0=Drum1, 1=Drum2, 2=Drum3, 3=Drum4, 4=OFF. **Confirmed** |
| 1 | attack | 0-127 | 0 | Attack time. **Confirmed** |
| 2 | hold | 0-127 | 50 | Hold time. **Confirmed** |
| 3 | decay | 0-127 | 70 | Decay time. **Confirmed** |
| 4 | depth | 0-127 | 0 | Compression depth. **Confirmed** |

#### Synth 2 Sidechain (0x2700D - 0x27013, 7 bytes)

Same 5-byte layout as Synth 1, followed by 2 extra bytes:

| Byte | Field | Default | Description |
|------|-------|---------|-------------|
| 0-4 | (same as S1) | (same) | Source, attack, hold, decay, depth |
| 5-6 | (unknown) | 0 | Always zeros in observed files. **Unknown** |

### 6.13 Gap (0x27014 - 0x2701B, 8 bytes)

Zeros. Purpose unknown.

### 6.14 Mixer Levels (0x2701C - 0x2701F, 4 bytes)

Non-drum track levels (0-127):

| Byte | Track | Default |
|------|-------|---------|
| 0 | Synth 1 | 100 |
| 1 | Synth 2 | 100 |
| 2 | MIDI 1 | 100 |
| 3 | MIDI 2 | 100 |

**Confirmed**. Drum track levels are stored in the per-drum config (section 6.4, byte 1).

### 6.15 Mixer Pans (0x27020 - 0x27023, 4 bytes)

Non-drum track pans (0-127, 64=center):

| Byte | Track | Default |
|------|-------|---------|
| 0 | Synth 1 | 64 |
| 1 | Synth 2 | 64 |
| 2 | MIDI 1 | 64 |
| 3 | MIDI 2 | 64 |

**Confirmed**. Drum track pans are stored in the per-drum config (section 6.4, byte 6). Note: Synth pans also appear in the preamble (section 6.1, offset 0x26D10).

### 6.16 Trailing Region (0x27024 - 0x2740B, 1,000 bytes)

All zeros in every observed file. Reserved or unused. Pads the file to the fixed 160,780-byte size.

---

## 7. SysEx Transfer Protocol

The Circuit Tracks accepts .ncs project files via MIDI SysEx using a file management protocol. This protocol was reverse-engineered from captured Novation Components WebMIDI output.

### 7.1 Message Format

All messages share a common header:

```
F0 00 20 29 01 64 03 <subcmd> [payload...] F7
```

| Field | Bytes | Value | Description |
|-------|-------|-------|-------------|
| SysEx start | 1 | `0xF0` | Standard MIDI SysEx start |
| Manufacturer ID | 3 | `0x00 0x20 0x29` | Novation |
| Product type | 1 | `0x01` | Synth |
| Product number | 1 | `0x64` | Circuit Tracks (decimal 100) |
| Command group | 1 | `0x03` | File management protocol |
| Sub-command | 1 | varies | See sub-command table |
| Payload | variable | varies | Message-specific |
| SysEx end | 1 | `0xF7` | Standard MIDI SysEx end |

### 7.2 Sub-Commands

| Code | Name | Direction | Description |
|------|------|-----------|-------------|
| `0x01` | WRITE_INIT | Host -> Device | Begin file write with metadata |
| `0x02` | WRITE_DATA | Host -> Device | File data chunk |
| `0x03` | WRITE_FINISH | Host -> Device | End file write with CRC32 checksum |
| `0x04` | ACK | Device -> Host | Acknowledge successful reception |
| `0x07` | SET_FILENAME | Host -> Device | Set filename for project slot |
| `0x09` | QUERY_INFO | Host -> Device | Request device/version info |
| `0x0B` | DIR_CONTROL | Host -> Device | Directory listing control |
| `0x0C` | FILE_ENTRY | Device -> Host | File listing entry |
| `0x40` | OPEN_SESSION | Host -> Device | Start file management session |
| `0x41` | CLOSE_SESSION | Host -> Device | End file management session |

### 7.3 Transfer Sequence

A complete project transfer consists of 29 SysEx messages:

```
Step  SubCmd  Size     Description
----- ------- -------- -----------
  1   0x40        9    OPEN_SESSION
  2   0x0B       10    DIR_CONTROL: 0x0B 0x01
  3   0x09       11    QUERY_INFO: 0x09 0x01 0x00
  4   0x0B       10    DIR_CONTROL: 0x0B 0x02
  5   0x0B       11    DIR_CONTROL: 0x0B 0x03 0x00
  6   0x01       29    WRITE_INIT (address 0, file ID, size)
  7   0x02     9383    WRITE_DATA block 1
  8   0x02     9383    WRITE_DATA block 2
  ...
 25   0x02     9383    WRITE_DATA block 19
 26   0x02     5886    WRITE_DATA block 20 (partial)
 27   0x03       28    WRITE_FINISH (CRC32)
 28   0x07       26    SET_FILENAME
 29   0x41        9    CLOSE_SESSION
```

The device responds to each WRITE message with an ACK. Implementations should wait for each ACK before sending the next block.

### 7.4 Block Addressing

Each WRITE message contains an 8-byte address field. Only the last 2 bytes are significant:

```
00 00 00 00 00 00 <page> <offset>
```

- Offsets range from `0x00` to `0x0F` (16 per page), then the page increments
- WRITE_INIT uses address (0, 0)
- WRITE_DATA blocks start at address (0, 1) and increment sequentially
- WRITE_FINISH uses the next address after the last data block

For a 160,780-byte file with 8,192-byte blocks (20 data blocks):
- Blocks 1-15: page 0, offsets 1-15
- Blocks 16-20: page 1, offsets 0-4
- FINISH: page 1, offset 5

### 7.5 File ID

3 bytes identifying the target file:

```
<type> <slot_hi> <slot_lo>
```

- Type `0x03` = project file
- Slot: 0-63 (the 64 project slots on the device)
- Example: `03 00 05` = project slot 5

### 7.6 WRITE_INIT Payload

```
<8-byte address=0> <3-byte file_id> 01 00 00 00 <5 size nibbles>
```

The file size is encoded as 5 hex nibbles, most-significant first:
- 160,780 = 0x2740C -> nibbles: `02 07 04 00 0C`

Full example for slot 1:
```
00 00 00 00 00 00 00 00   03 00 01   01 00 00 00   02 07 04 00 0C
|-------- address -------|  |file ID|  |-- flags --|  |-- size ----|
```

### 7.7 MSB Interleave Encoding

MIDI SysEx requires all data bytes to be < 0x80 (7-bit). The protocol uses MSB interleave encoding to transmit 8-bit data:

**Encoding**: Every 7 raw bytes produce 8 encoded bytes:

```
Input:   d0    d1    d2    d3    d4    d5    d6      (7 bytes)
Output:  MSB   d0'   d1'   d2'   d3'   d4'   d5'   d6'   (8 bytes)
```

- The **MSB header byte** stores the most-significant bits of all 7 data bytes:
  - Bit 0 = MSB of d0
  - Bit 1 = MSB of d1
  - ...
  - Bit 6 = MSB of d6
- Each data byte is transmitted with its MSB cleared: `d' = d & 0x7F`

**Decoding**: `original = (d' & 0x7F) | (((msb_header >> bit_position) & 1) << 7)`

**Example**:
```
Raw:     [0xFF, 0x80, 0x00, 0x7F, 0x81, 0x00, 0x00]
MSB hdr: bit0=1(FF) bit1=1(80) bit2=0(00) bit3=0(7F) bit4=1(81) bit5=0 bit6=0 = 0b00010011 = 0x13
Encoded: [0x13, 0x7F, 0x00, 0x00, 0x7F, 0x01, 0x00, 0x00]
```

**Block sizes**: 8,192 raw bytes per block. Encoded size = ceil(8192 / 7) x 8 = 9,368 bytes (actual last block is smaller since the file is not evenly divisible).

### 7.8 WRITE_DATA Payload

```
<8-byte address> <3-byte file_id> <MSB-interleave-encoded data>
```

Each message carries one block of 8,192 raw bytes (except the last block, which carries the remainder).

### 7.9 WRITE_FINISH Payload

```
<8-byte address> <3-byte file_id> <8 CRC32 nibbles>
```

The CRC32 is a **standard reflected CRC32** (compatible with `zlib.crc32()` in Python, `CRC32` in most languages). It is computed over the entire raw .ncs file data (160,780 bytes).

The checksum is encoded as 8 hex nibbles, most-significant first:
- CRC32 = 0x2D9CB759 -> nibbles: `02 0D 09 0C 0B 07 05 09`

**Important**: This is NOT the non-reflected CRC32 variant mentioned in some firmware RE notes.

### 7.10 SET_FILENAME Payload

```
<3-byte file_id> <ASCII filename>
```

Example: `03 00 01` followed by ASCII bytes for `01_FXFinal.ncs`.

### 7.11 Session Open/Close

No payload beyond the header:

- **Open**: `F0 00 20 29 01 64 03 40 F7`
- **Close**: `F0 00 20 29 01 64 03 41 F7`

### 7.12 Directory Handshake

Before writing, Novation Components performs a directory listing handshake:

1. Send `0x0B 01` -- device responds with `0x0B 01 01` (ACK)
2. Send `0x09 01 00` -- device responds with version info
3. Send `0x0B 02` -- device responds with current project name
4. Send `0x0B 03 00` -- device responds with 64 file entries (one per project slot)

This handshake may be optional for write-only operations. The implementation in this project includes it for compatibility.

---

## Appendix A: Probability Levels

| Value | Probability |
|-------|------------|
| 0 | 12.5% |
| 1 | 25% |
| 2 | 37.5% |
| 3 | 50% |
| 4 | 62.5% |
| 5 | 75% |
| 6 | 87.5% |
| 7 | 100% (default) |

## Appendix B: Sync Rate Values

The sync rate field (0-7) controls the step playback speed. The exact mapping to musical divisions has not been fully documented in this specification. Default value is 3.

## Appendix C: FX Lookup Tables

### Reverb Types

| Value | Name |
|-------|------|
| 0 | Chamber |
| 1 | Small Room |
| 2 | Large Room (default) |
| 3 | Small Hall |
| 4 | Large Hall |
| 5 | Great Hall |

### Delay L/R Ratios

| Value | Ratio |
|-------|-------|
| 0 | 1:1 |
| 1 | 4:3 |
| 2 | 3:4 |
| 3 | 3:2 |
| 4 | 2:3 (default) |
| 5 | 2:1 |
| 6 | 1:2 |
| 7 | 3:1 |
| 8 | 1:3 |
| 9 | 4:1 |
| 10 | 1:4 |
| 11 | 1:OFF |
| 12 | OFF:1 |

## Appendix D: Scale Root

| Value | Note |
|-------|------|
| 0 | C |
| 1 | C# |
| 2 | D |
| 3 | D# |
| 4 | E |
| 5 | F |
| 6 | F# |
| 7 | G |
| 8 | G# |
| 9 | A |
| 10 | A# |
| 11 | B |

## Appendix E: Scale Types

The Circuit Tracks supports 16 scale types (values 0-15). Based on the User Guide:

| Value | Scale |
|-------|-------|
| 0 | Natural Minor |
| 1 | Major |
| 2 | Dorian |
| 3 | Phrygian |
| 4 | Mixolydian |
| 5 | Melodic Minor (ascending) |
| 6 | Harmonic Minor |
| 7 | Bebop Dorian |
| 8 | Blues |
| 9 | Minor Pentatonic |
| 10 | Hungarian Minor |
| 11 | Ukrainian Dorian |
| 12 | Marva |
| 13 | Todi |
| 14 | Whole Tone |
| 15 | Chromatic |

## Appendix F: Known Unknowns

The following fields have been observed in the binary data but their purpose has not been determined:

| Location | Size | Observed Value | Notes |
|----------|------|---------------|-------|
| 0x08 (`header.sessionColour`) | 4 bytes | 0 or 1 | WASM validator rejects values > 1. Not the project color. |
| 0x30 | 4 bytes | 0 | Always zeros in all observed files. |
| Drum config byte 3 | 1 byte | 127 (0x7F) | Per drum track. Possibly EQ max or filter cutoff default. |
| Drum config byte 10 | 1 byte | 0 | Per drum track. Always 0. |
| Scene header bytes 0-7 | 8 bytes | varies | First 8 bytes of each 40-byte scene block. |
| Sidechain S2 extra bytes | 2 bytes | 0 | Two extra bytes after S2 sidechain params. |
| Tail preamble bytes 0-15 | 16 bytes | mostly 0 | WASM references `synthTrackInfo`, `drumMuteStates`, `defaultDrumChoices`, `midiTrackInfo` in this region. |
| ~~Automation data layout~~ | ~~variable~~ | ~~0xFF~~ | **Documented** — see Automation Data (P-Locks) section above. Synth: 12 slots (8 macros + reverb/delay/level/pan). Drum: 8 slots (pitch/decay/distortion/eq + reverb/delay/level/pan). |
| Trailing 1,000 bytes | 1,000 bytes | 0 | 0x27024-0x2740B. Always zeros. |
