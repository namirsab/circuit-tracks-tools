# Web Tracks Changelog

All notable changes to the Web Tracks webapp will be documented in this
file. The webapp is versioned independently from the `circuit-tracks-tools`
Python library that lives in the same repository.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [1.0.0] — 2026-06-12

First public release.

### Added

- **The groovebox** — 8 tracks (2 synth, 2 MIDI, 4 drum), 4×8 pad grid,
  Web Audio synth engine, and the hardware's view system (Note, Velocity,
  Gate, Probability, Micro Step, Pattern Settings, Patterns, Mixer, FX,
  Side Chain, Scales). 100% static and self-contained — runs from any
  file server, nothing is uploaded.
- **Projects view** — 64 slots across 2 pages; switching is queued to the
  pattern boundary while playing (Shift+pad switches instantly); empty
  slots load an init project; drop a `.ncs` on a pad to fill that slot.
- **Hardware-style Save** — arm, pick a project colour, save to the
  current slot; unexported saves warn before the page closes.
- **Pack import/export** — load `.circuittrackspack` archives (zip) or
  Components pack folders; export projects (`.ncs`), patches (`.syx`),
  and whole packs.
- **Starter pack, generated from code** — 64 drum samples (stdlib DSP,
  no recordings), a full bank of 128 synth patches, and 16 demo projects;
  deterministic generator in `scripts/generate_webapp_pack.py`.
- **First-run welcome overlay** with quick start and disclaimer,
  keyboard-mapping overlay, music-reactive glow around the console,
  SVG favicon.
- **Deployment** — `Dockerfile` (nginx) and Coolify instructions in the
  README.

### Branding & legal

- Original identity: **Web Tracks** wordmark (step-bars logo, lowercase
  monospace lockup) and violet-slate palette. Track and pad colours stay
  hardware-accurate for cross-referencing with a real device.
- "Not affiliated with Novation/Focusrite" disclaimers in the app footer,
  welcome overlay, and READMEs. The file format was independently
  reverse-engineered; all bundled audio content is generated — nothing is
  copied from factory packs.
