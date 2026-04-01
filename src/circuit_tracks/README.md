# circuit-tracks

A Python library for controlling a [Novation Circuit Tracks](https://novationmusic.com/products/circuit-tracks) synthesizer via MIDI.

## Features

- **MIDI connection** -- connect to the Circuit Tracks over USB
- **Synth & drum control** -- play notes/chords, set parameters via CC/NRPN
- **Step sequencer** -- build and play patterns with synth, drum, and MIDI tracks
- **Patch management** -- read, create, and modify synth patches; load `.syx` files
- **Project transfer** -- parse and send `.ncs` project files to the device
- **Macro knobs** -- configure macro destinations and sweep parameters
- **Parameter morphing** -- smooth CC/NRPN transitions with concurrent background threads
- **Per-step automation (p-locks)** -- macro, mixer, and FX automation with micro-step resolution
- **Song format** -- define songs as structured data and export to device

## Installation

```bash
pip install -e .
```

The only runtime dependency is [mido](https://mido.readthedocs.io/) (with rtmidi backend).

## Quick start

```python
from circuit_tracks import MidiConnection, PatchBuilder

# Connect
midi = MidiConnection()
midi.connect("Circuit Tracks MIDI")

# Play a C major chord on Synth 1
midi.note_on(channel=0, note=60, velocity=100)
midi.note_on(channel=0, note=64, velocity=100)
midi.note_on(channel=0, note=67, velocity=100)

# Build a pad patch from scratch
patch = PatchBuilder.preset_pad(cutoff=80, attack=40, release=90)
```

## Modules

| Module | Description |
|--------|-------------|
| `midi` | MIDI connection and message sending |
| `constants` | CC/NRPN mappings, channel assignments, lookup tables |
| `sequencer` | Software step sequencer (plays patterns over MIDI, not the device's internal sequencer) |
| `patch` | Synth patch SysEx read/write |
| `patch_builder` | Fluent API for building patches |
| `macros` | Macro knob configuration |
| `morph` | Parameter morphing engine (concurrent background sweeps) |
| `ncs_parser` | Binary `.ncs` project file parser with automation support |
| `ncs_transfer` | SysEx file transfer protocol |
| `song` | High-level song format and device export |
