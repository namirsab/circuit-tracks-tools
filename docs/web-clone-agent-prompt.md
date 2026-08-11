# Task: Build a Web Clone of the Novation Circuit Tracks

## Goal

Build a fully functional web app that clones the Novation Circuit Tracks groovebox — playable in a browser with mouse, touch, and keyboard.

## Documentation — read these before writing any code

- `circuit_tracks_user_guide_v3_en.pdf` — hardware layout, interaction model, all features
- `circuit_tracks_programmer_s_reference_guide_v3.pdf` — synth parameters, patch format, SysEx
- `docs/ncs-format.md` — reverse-engineered .ncs project file format
- `docs/sysex-file-protocol.md` — SysEx file transfer protocol

## Assets

- `CircuitFactorySamples.zip` — 64 factory drum WAV samples; map to indices in `docs/ncs-format.md` Appendix G
- `example-patches/` — real `.syx` synth patch files captured from hardware
- `example-projects-ncs/` — real `.ncs` project files, including targeted test files:
  - `Circuit Coade Intro.ncs` — main feature-complete project
  - `WithDrums.ncs`, `FXBypass.ncs`, `FXFinal.ncs`, `MixerAndSends.ncs`
  - `SceneAndChains.ncs`, `TiedNote.ncs`, `Empty.ncs`, `1Note.ncs`

## Features

### Look & Feel

Faithful recreation of the Circuit Tracks front panel: 32 RGB pads, 8 macro knobs, Master Volume, Master Filter, and all buttons (track selectors, Scales, Note/Velocity/Gate/Probability/Pattern Settings, Mixer, FX, Patterns, Rec, Play, Shift). Pad colours must reflect the current mode and state exactly as described in the user guide.

### Input

- Mouse and touch: click/tap pads, drag knobs, click buttons
- Keyboard: mimic the physical Circuit Tracks layout — the 32 pads map to the four main keyboard rows (number row, Q-row, A-row, Z-row). Function buttons map to logical keys. Include a toggleable overlay showing the key mapping.

### Synth

Full emulation of the Circuit Tracks synth engine as documented in the Programmer's Reference Guide: two oscillators, filter, three envelopes, two LFOs, 12-slot mod matrix, 8 macro knobs (macros add to base parameter values — they don't replace them), per-patch distortion/chorus/EQ, and global reverb and delay.

### Drums

Four drum tracks playing the factory WAV samples. Per-step velocity, probability, sample flip (drum choice), and on/off. Per-track pitch, decay, distortion, EQ, pan, level, reverb send, delay send. Sidechain compression from any drum track to any synth/MIDI track.

### Sequencer

- 8 tracks: Synth 1, Synth 2, MIDI 1, MIDI 2, Drum 1–4
- 8 patterns per track, 16 or 32 steps each
- Step editing: note, velocity, gate length, tie/drone, probability, micro-step offset
- P-lock automation (per-step parameter locks)
- Pattern settings: start/end step, sync rate, playback direction
- Pattern chaining per track; 16 scenes each holding a chain assignment per track; scene chaining for arrangements
- Mutate function
- Scale quantisation (16 scale types × 12 root notes)

### Project & Patch Loading

- Drag-and-drop `.ncs` files to load a full project: patterns, patches, drum configs, FX settings, BPM, swing, mixer, scenes, sidechain
- Drag-and-drop `.syx` files onto a synth track to load a synth patch
- NCS note numbers are offset +12 from MIDI — account for this during playback

### Transport

Play/Stop, record (step and live), BPM (40–240), swing, tap tempo. Timing must be sample-accurate — not based on `setInterval` or `setTimeout`.

## Verification

After building, use the `claude-in-chrome` MCP tools to verify in Chrome DevTools — fix all issues before reporting done:

1. No console errors on load
2. Load `Circuit Coade Intro.ncs` — BPM, project name, and step data display correctly
3. Load `Empty.ncs` — no errors on an empty project
4. Load `WithDrums.ncs` — press Play, drum steps trigger the correct WAV samples at the right tempo
5. Load `FXBypass.ncs` — FX bypass state is reflected in the UI
6. Load `MixerAndSends.ncs` — mixer levels and send values load correctly
7. Drag a `.syx` file onto Synth 1 — patch name appears, synth parameters update
8. Press a pad — the synth produces audible output
9. Keyboard input triggers pads with correct RGB visual feedback
10. No layout issues at 1280×800 viewport
