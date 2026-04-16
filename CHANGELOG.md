# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-04-16

First public release.

### Added

- **Standalone library** (`circuit_tracks`) — use the Circuit Tracks from Python scripts without the MCP server
- **Song format** — create full songs (patterns, patches, macros, FX) in a single call with `parse_song()` and export to the device
- **Pydantic schema validation** — song input is validated with JSON Schema; call `get_song_json_schema()` for the full spec
- **Synth patch builder** — construct patches from scratch with oscillators, filters, envelopes, mod matrix, and macros via `PatchBuilder`
- **NCS project parser** — read and write `.ncs` project files with `parse_ncs()` / `serialize_ncs()`
- **NCS project transfer** — send projects and patches to the device over SysEx
- **Read projects from device** — pull the current project back from the Circuit Tracks via SysEx
- **Parameter morphing** — smoothly interpolate synth, drum, and project parameters over time
- **Macro knob system** — configure macro destinations and sweep parameters for live performance
- **MIDI track support** — sequence external gear on MIDI tracks 3 and 4
- **4-track sidechain** — automate volume ducking across all tracks with preset sidechain curves
- **Standalone clock/transport** — start/stop the sequencer, set BPM, and send MIDI clock independently
- **Step ties** — tie consecutive steps for legato note sequences
- **Scale quantization** — quantize notes to any of the Circuit Tracks' built-in scales
- **FX preset tables** — select delay and reverb presets by index
- **CI/CD** — GitHub Actions for tests (Python 3.11–3.13) and PyPI publishing via OIDC trusted publishing
- **`py.typed` marker** — PEP 561 compliant for type checkers

### Fixed

- Mod matrix byte order: source2 and destination were swapped on read
- Scene chain entry byte layout: byte[3] is the start position, not byte[1]
- Scale transposition mismatch between MIDI preview and NCS playback
- Drum NCS byte mapping and per-step sample selection
- FX preset selection for NCS export
- Patch save using reverse-engineered Components protocol (Replace Patch SysEx doesn't work)
- Gate encoding now correctly caps at 16 (one full step)

## [0.1.1] — 2026-04-16

### Added

- Linting and formatting with [ruff](https://docs.astral.sh/ruff/) — configured in `pyproject.toml`
- Pre-commit hooks for automatic lint and format checks on every commit
- `dev` optional dependency group (`pip install -e ".[dev]"`) with pytest, ruff, and pre-commit

### Fixed

- Removed unused imports and variables across the codebase
- Fixed undefined `NCSFile` name in `test_song.py`
- Moved module-level logger in `song.py` after imports to satisfy E402

[0.1.1]: https://github.com/namirsab/circuit-tracks-tools/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/namirsab/circuit-tracks-tools/releases/tag/v0.1.0
