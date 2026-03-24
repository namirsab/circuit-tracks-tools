# Plan: Reverse Engineer NCS Format & Build Parser/Writer

## Context

The goal is to understand the Novation Circuit Tracks `.ncs` project file format so we can programmatically read and write project files. This enables generating patterns/sequences from code and loading them onto the Circuit Tracks via its SD card, bypassing real-time MIDI step-by-step entry.

Three example files compared:
- `Empty.ncs` — blank project, 120 BPM, patches "AfterHours" (synth1) + "AlteredState" (synth2)
- `2 notes.ncs` — same project + C4 on synth1 step 1, C6 on synth2 step 1 (default vel/gate)
- `WithDrums.ncs` — same patches + C4 on synth1 (vel=64, gate=4 steps) + C6 on synth2 (default) + drum hits on step 1 & 9 of all 4 drum tracks, tempo=122 BPM

### External Resources
- **Novation Components WASM validator** — validates NCS files before upload, could be disassembled
- **Firmware RE gist**: https://gist.github.com/userx14/664f5e74cc7ced8c29d4a0434ab7be98
- **No existing NCS parser library** — this is novel work

---

## NCS Format Specification (confirmed)

### File size: 160,780 bytes (fixed)

### Header (0x00 - 0x37, 56 bytes)
| Offset | Size | Field | Example |
|--------|------|-------|---------|
| 0x00 | 4 | Magic | `USER` (ASCII) |
| 0x04 | 4 | File size (LE uint32) | 160780 = 0x2740C |
| 0x08 | 4 | Version? | 1 |
| 0x0C | 4 | Unknown | 11 (0x0B) |
| 0x10 | 32 | Project name (ASCII, space-padded) | `Empty` |
| 0x30 | 4 | Unknown | 0 |
| 0x34 | 1 | **BPM** (confirmed: 120→0x78, 122→0x7A) | 120 |
| 0x35 | 3 | Unknown (0x32, 0x03, 0x00) | fixed? |

### Padding: 0x38 - 0x2E3 (684 bytes of zeros)

### Pattern Data Region: 0x2E4 - ~0x26CFC

64 pattern blocks organized by track (8 patterns per track):

| Blocks | Track | Block Size | Step Format |
|--------|-------|------------|-------------|
| 0-7 | Synth 1 | 3240 bytes | 28 bytes/step (confirmed) |
| 8-15 | Synth 2 | 3240 bytes | 28 bytes/step (confirmed) |
| 16-47 | Drums 1-4 | 1704 bytes | Row-based (confirmed) |
| 48-63 | MIDI 1-2 | 3240 bytes | 28 bytes/step (assumed same as synth) |

Each synth/MIDI pattern block (3240 bytes):
- **4 bytes**: prefix/padding (zeros, or 0xFFFFFFFF at track boundaries)
- **896 bytes**: 32 steps × 28 bytes
- **40 bytes**: metadata header (`0F 00 03 00` + 36 zero bytes)
- **2300 bytes**: 0xFF padding

### Synth/MIDI Step Format (28 bytes) — CONFIRMED

```
Bytes 0-3:  Header
  [0] active    — 0x00=empty, 0x01=has note
  [1] param_count — 0x07 (constant)
  [2] reserved  — 0x00
  [3] reserved  — 0x00

Bytes 4-7:  Param 0 (Note)
  [4] note      — MIDI note number (0-127). C4=60=0x3C, C6=84=0x54
  [5] gate      — Gate length in micro-ticks (6 per step)
                   0x06=1 step (default), 0x18=4 steps (confirmed)
  [6] reserved  — 0x00
  [7] velocity  — MIDI velocity (0-127)
                   0x60=96 (empty default), 0x40=64 (confirmed "half")

Bytes 8-27: Params 1-5 (step automation, 4 bytes each)
  Each: [value, ?, 0x00, base_value(0x60)]
  Likely: filter, resonance, FX send, probability, mutation
  Default: 00 00 00 60
```

### Drum Pattern Format (1704 bytes) — CONFIRMED

Each drum pattern block:
- **FF padding** (variable, fills start of block)
- **16 bytes**: header/prefix (includes 0x20 at byte 8)
- **32 bytes**: Row 0 — velocity per step (0x60=default vel, 0x00=empty)
- **32 bytes**: Row 1 — parameter (0x07 default for all 32 steps)
- **32 bytes**: Row 2 — 0xFF for all steps
- **32 bytes**: Row 3 — trigger flag (0x01=active, 0x00=inactive)
- **40 bytes**: metadata (`0F 00 03 00` + zeros)

Steps 1 and 9 (indices 0 and 8) confirmed active with 0x60 velocity and 0x01 trigger.

### Pattern Metadata (40 bytes per pattern, `0F 00 03 00` header)
Located after step data in each of the 64 pattern blocks. All 64 blocks identical in test files.
```
  [0] 0x0F = 15 → pattern end point (0-indexed, default=15 = step 16)
  [1] 0x00 = 0  → pattern start point (0-indexed, default=0 = step 1)
  [2] 0x03       → likely: play order (2 bits) + sync rate (6 bits), or similar
  [3] 0x00       → unknown
  [4-39] all zeros → reserved (probability? automation data pointers?)
```

### Drum Pattern Header (16 bytes, before step rows)
```
  [0-7] zeros
  [8] 0x20 = 32 → step count / pattern length indicator
  [9-15] zeros
```

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
  [0-19] 20 zeros — scene data? scale/root settings? (all default=0)
  [20-21] 0x40 0x40 = 64, 64 — synth1 pan, synth2 pan (center=64)
  [22-23] 0x00 0x00 — unknown
```

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
- ✅ **BPM/Tempo** (40-240, byte at 0x34, default 120)
- ❓ **Swing** (20-80, default 50 — not found in tail, maybe in padding 0x38-0x2E3)
- ❓ **Scale selection** (16 scales — likely in the 20 zero-byte preamble at 0x26CFC)
- ❓ **Root note** (C through B — likely in preamble)
- ❓ **Project colour** (RGB LED colour — likely in preamble or padding)

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

### Per-Pattern Settings (in 40-byte metadata block per pattern)
- ✅ **Pattern end point** (byte 0, 0-indexed, default=0x0F=15 → step 16)
- ✅ **Pattern start point** (byte 1, 0-indexed, default=0x00 → step 1)
- 🔶 **Play order + Sync rate** (byte 2 = 0x03, encoding TBD — needs test file with changed values)

### Synth Step Data (per step, 28 bytes)
- ✅ **Active flag** (byte 0)
- ✅ **Note** (byte 4, MIDI note 0-127; up to 6 notes per step for polyphony — need multi-note test)
- ✅ **Velocity** (byte 7, 0-127)
- ✅ **Gate** (byte 5, micro-ticks, 6 per step, range 1-96)
- ✅ **Macro automation** (bytes 8-27: 5 × 4-byte param slots)
- ❓ **Probability** (likely in one of the 5 automation param slots, or in byte 6)
- ❓ **Micro step offset** (0-5 ticks per note — likely byte 6 or within automation params)
- ❓ **Tie-forward** (drone/legato — likely a flag in byte 2 or 3)

### Drum Step Data (4 rows of 32 bytes per pattern)
- ✅ **Trigger flag** (Row 3: 0x01=active)
- ✅ **Velocity** (Row 0: 0x60=default active vel)
- ✅ **Probability** (Row 1: 0x07=100%, 0-indexed so 0=12.5% through 7=100%)
- 🔶 **Row 2** (all 0xFF — micro-step? sample-flip bitmask? needs test)
- ❓ **Sample flip** (per-step alternative sample — likely encoded in rows or drum header)

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
