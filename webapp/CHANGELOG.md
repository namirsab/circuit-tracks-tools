# Web Tracks Changelog

All notable changes to the Web Tracks webapp will be documented in this
file. The webapp is versioned independently from the `circuit-tracks-tools`
Python library that lives in the same repository.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [1.2.2] — 2026-06-30

### Added

- **Social share previews** — shared links now render a rich card (Open Graph
  and Twitter/X tags) with a branded 1200×630 preview image, title, and
  description instead of a bare URL. The image lives at `og-image.png`.

## [1.2.1] — 2026-06-16

### Fixed

- **Audio engine no longer collapses under heavy playback** — with several
  tracks, sounds, and effects running, all sound could cut out (and the whole
  UI slow down) until you stopped and waited. Voice stealing was a no-op for
  notes already in their release tail, so the polyphony cap was never enforced
  and voices piled up (100+ on one track) until the audio thread choked and the
  context clock stalled. Stolen voices are now reclaimed immediately. Synth
  polyphony is also capped at 6 voices per synth to match the hardware, and
  out-of-range parameter values can no longer poison the audio graph.

## [1.2.0] — 2026-06-14

### Added

- **Session persistence** — the workspace is now saved to the browser
  (IndexedDB) so an accidental reload no longer throws away your work. The
  live project autosaves continuously (debounced after edits), and the full
  pack — saved bank slots, drum samples, and patches — is snapshotted when it
  changes and on tab close. On the next visit, if a saved session exists, a
  prompt offers to **Restore** it or **Start fresh** from the bundled pack.
  Degrades gracefully when storage is unavailable (e.g. private browsing).

## [1.1.0] — 2026-06-13

### Added

- **Duplicate on steps** — holding **Duplicate** in the step sequencer
  (Note, Velocity, Gate, Probability, Micro Step views) now copies a step:
  the first pad press picks the source, each later press pastes the whole
  step — notes/hit, velocity, gate, probability, and that step's param
  locks — onto the target. Mirrors the existing Patterns-view copy/paste,
  including the synth↔drum guard.

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
