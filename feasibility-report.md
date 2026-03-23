# Circuit Tracks + Claude Code: Feasibility Report

## Verdict: Highly Feasible

Controlling a Novation Circuit Tracks from a computer via MIDI is not only possible — it's well-supported by Novation's own design. The Programmer's Reference Guide documents hundreds of controllable parameters, and the USB-C port is MIDI class compliant on macOS (no drivers needed). Claude Code can generate and execute Python scripts that send MIDI messages to the device in real-time.

---

## What Can Be Controlled from a Computer

### 1. Real-Time Note Playing
Send MIDI Note On/Off messages to trigger sounds directly:

| Target | MIDI Channel | Notes |
|--------|-------------|-------|
| Synth 1 | Channel 1 | Any MIDI note (chromatic) |
| Synth 2 | Channel 2 | Any MIDI note (chromatic) |
| Drum 1 | Channel 10 | Note 60 |
| Drum 2 | Channel 10 | Note 62 |
| Drum 3 | Channel 10 | Note 64 |
| Drum 4 | Channel 10 | Note 65 |

This means Claude Code can compose melodies, chords, and drum patterns and play them on the Circuit Tracks in real-time.

### 2. Complete Synth Sound Design (Channels 1 & 2)
Every synth parameter is accessible via CC or NRPN messages:

- **Oscillators**: Waveform (30 types including sine, saw, square, wavetables, digital), pitch, density, detune, pulse width, virtual sync
- **Filter**: Type (low pass, band pass, high pass at various slopes), frequency, resonance, drive, routing
- **Envelopes**: 3 full ADSR envelopes per synth with velocity control
- **LFOs**: 2 per synth with 38 waveform types, rate, sync, delay, fade modes
- **Effects**: Distortion (7 types), chorus/phaser, 3-band EQ
- **Mixer**: Oscillator levels, ring mod, noise, pre/post FX levels
- **Mod Matrix**: 20 modulation slots, each with 2 sources, depth, and destination

This is the full synth engine — Claude can design any sound the Circuit Tracks is capable of producing.

### 3. Drum Sound Shaping (Channel 10)
For each of the 4 drum tracks: level, pitch, decay, distortion, EQ, pan, and sample selection (64 samples per drum slot).

### 4. Project-Level Control (Channel 16)
- **Reverb**: Send levels per track, type (chamber/small room/large room/small hall/large hall/great hall), decay, damping
- **Delay**: Send levels per track, time, sync, feedback, width, left-right ratio, slew rate
- **Mixer**: Synth/drum levels and panning
- **Sidechain**: Source selection, attack, hold, decay, depth per synth
- **Master Filter**: Frequency and resonance (the DJ-style filter knob)
- **FX Bypass**: Enable/disable effects globally

### 5. Patch & Project Management
- **Select synth patches**: Program Change on channels 1-2 (64 patches per synth)
- **Select projects**: Program Change on channel 16 (64 projects, instant or queued)
- **Upload synth patches via SysEx**: Full 340-byte patch format documented — can replace current patch in RAM or overwrite stored patches in flash
- **Download patches via SysEx**: Request a patch dump from the device

### 6. Transport & Timing
- **Start/Stop/Continue**: Standard MIDI realtime messages
- **MIDI Clock**: Can sync the Circuit Tracks' tempo to an external clock
- **Song Position Pointer**: Jump to a specific position in the sequence

---

## What CANNOT Be Controlled (Limitations)

### 1. Step Sequencer Data — Partial Limitation
The Programmer's Reference does **not** document a way to directly write note data into specific sequencer steps via MIDI. You cannot say "put a C4 on step 5 of pattern 3."

**Workaround**: You CAN record notes into the sequencer in real-time by:
1. Sending a MIDI Start message (starts the sequencer)
2. Enabling Record mode (would need to be done physically or via a workaround)
3. Sending notes at the right time — they get quantized into the pattern

**Alternative approach**: Instead of using the internal sequencer, Claude Code can act AS the sequencer — sending notes at precisely timed intervals from the computer. This is actually more flexible since it removes the 32-step limitation.

### 2. Sample Loading
Loading custom audio samples requires Novation Components (web app) or microSD card. Cannot be done via MIDI.

### 3. Physical Button Presses
There's no MIDI message to simulate pressing the Record button, switching views, or other UI interactions. Some features (like entering Record mode) require physical interaction.

### 4. Audio Routing
The USB-C port does NOT carry audio — only MIDI data. Audio comes from the 1/4" outputs and headphone jack. You'll need a separate audio interface if you want to record the output.

### 5. Pattern/Scene Management
While you can select projects via Program Change, there's no documented way to create, modify, or chain patterns/scenes via MIDI. These are internal sequencer features.

---

## Proposed Architecture

```
┌─────────────────────────────────────────────────┐
│                  Claude Code                     │
│                                                  │
│  User Prompt ──► AI Interprets ──► Python Script │
│                                                  │
│  "Play a dark techno bass line    Generates:     │
│   at 128 BPM with sidechained     - MIDI CCs    │
│   kick"                           - Notes        │
│                                   - SysEx        │
│                                   - Timing       │
└────────────────────┬────────────────────────────┘
                     │ USB-MIDI (python-rtmidi)
                     ▼
┌─────────────────────────────────────────────────┐
│             Novation Circuit Tracks              │
│                                                  │
│  Ch 1: Synth 1 (bass)     Ch 10: Drums          │
│  Ch 2: Synth 2 (lead)     Ch 16: Project/FX     │
│                                                  │
│  Internal audio engine produces sound            │
└────────────────────┬────────────────────────────┘
                     │ Audio (1/4" TRS)
                     ▼
              Speakers / Headphones
```

### Software Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| AI Engine | Claude Code (CLI) | Interpret prompts, generate music logic |
| MIDI Library | `python-rtmidi` or `mido` | Send/receive MIDI over USB |
| Timing | Python `time` + threading | Precise note scheduling |
| Patch Design | Python + SysEx | Create and upload synth patches |
| Project Config | Python module | Store Circuit Tracks MIDI mappings |

### Key Python Libraries
- **[python-rtmidi](https://github.com/SpotlightKid/python-rtmidi)**: Mature, cross-platform, works with macOS CoreMIDI natively. Pre-built wheels for Apple Silicon.
- **[mido](https://mido.readthedocs.io/)**: Higher-level MIDI library that can use rtmidi as backend. Friendlier API for message construction.

---

## Implementation Phases

### Phase 1: Foundation (Proof of Concept)
- Connect Circuit Tracks via USB, detect it as a MIDI device
- Send a single note to Synth 1 — confirm it plays
- Send CC messages to change a synth parameter (e.g., filter frequency)
- Build a basic Python module with Circuit Tracks MIDI mappings

### Phase 2: Sound Design via Prompts
- Map all CC/NRPN parameters to a structured data model
- Create preset "recipes" (e.g., "acid bass" = specific oscillator + filter settings)
- Enable Claude to translate descriptions like "warm pad" into parameter sets
- Implement SysEx patch upload for complete sound replacement

### Phase 3: Composition & Sequencing
- Build a software sequencer that sends timed MIDI notes
- Implement pattern concepts (16/32 step loops)
- Add support for velocity, gate length, and probability
- Enable Claude to generate drum patterns and melodies from prompts

### Phase 4: Full Production & Live Performance
- Multi-track arrangement (intro, verse, chorus, etc.)
- Real-time parameter automation (filter sweeps, FX builds)
- Interactive mode: Claude listens to prompts and modifies the running track
- Scene management: crossfade between different musical sections

---

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| MIDI timing jitter from Python | Medium | Use dedicated timing thread; keep messages small; consider `rtmidi`'s low-latency callbacks |
| No direct sequencer write access | Medium | Use Claude Code as the sequencer instead — more flexible anyway |
| Record mode requires physical press | Low | Design around it — external sequencing avoids this |
| USB MIDI bandwidth limits | Low | Circuit Tracks handles standard MIDI bandwidth fine for this use case |
| Complex NRPN message formatting | Low | Build a helper library once, reuse everywhere |

---

## Conclusion

This project is **highly feasible**. The Circuit Tracks was designed with external MIDI control in mind — Novation published a detailed programmer's reference precisely to enable this kind of integration. The most powerful approach will be to use Claude Code as an external brain + sequencer, sending both sound design parameters and timed note data over USB-MIDI.

The main creative constraint is that you'll be working within the Circuit Tracks' sound engine (its synth engine and loaded samples), but that engine is quite capable — and the fact that you have full programmatic access to every parameter means Claude can explore the sonic space far more thoroughly than manual knob-twiddling ever could.

**Recommended first step**: Install `python-rtmidi`, connect the Circuit Tracks via USB, and run a simple script that plays a C major chord on Synth 1. Once you hear sound, you'll know the entire pipeline works.
