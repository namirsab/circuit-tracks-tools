# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

This file covers the Python library (`circuit-tracks-tools`) only. The
[Web Tracks webapp](webapp/) is versioned independently — see
[webapp/CHANGELOG.md](webapp/CHANGELOG.md).

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

## [0.2.0] — 2026-06-12

NCS parser accuracy release — every fix verified against hardware behaviour,
and round-trips of all tested factory/user projects are now byte-exact.

### Fixed

- **Scenes/chains region alignment**: the region starts at offset `0x38`
  (not `0x39`), and every chain entry — scene track chains, the scene chain,
  and pattern chains — is laid out `[start, end, 0, 0]`. The previous
  one-byte misalignment produced wrong scene ranges (e.g. scenes 6–16 shown
  instead of 1–16) and garbage chain data that could blow up playback.
- Scene "used" flag: byte 0 of each 8-byte scene header marks a stored scene;
  `set_scene` now writes it instead of the old header hack.
- P-lock writer no longer smears integer-step locks across all micro
  positions — one byte per position, keeping round-trips byte-exact.
- Automation lock positions recorded at longer pattern lengths (beyond the
  current step count) are preserved on parse/serialize instead of dropped.

### Added

- **Drum micro-hits**: `DrumStep.micro_hits` exposes the rhythm row's 6-bit
  micro-hit mask (`0x01` plain hit … `0x3F` six-hit roll).
- **Fractional p-lock positions**: automation lanes are parsed at full
  192-position resolution; sub-step locks use fractional step keys
  (e.g. `3.5`) for smooth micro-step automation.

### Changed

- `ChainEntry` fields renamed to match the true byte layout (`start`, `end`);
  the bogus `scene_chain_start` property is gone.

### Documentation

- `docs/ncs-format.md`: scenes & chains section rewritten for the `0x38`
  base, chain entry format corrected, rhythm-row micro-hit mask documented,
  and the pattern sync-rate table added (stored byte is reversed relative to
  the hardware pad order: `7` = 1/4, `0` = 1/32T).

## [0.2.1] — 2026-08-11

### Fixed

- Pinned the `mcp` optional dependency to `>=1.9.0,<2` — the MCP Python SDK
  2.0.0 release removed `mcp.server.fastmcp` (FastMCP was replaced by a new
  `MCPServer` API), which broke the server import and CI. The server stays on
  the 1.x line until it is migrated to the 2.0 API.

[0.2.1]: https://github.com/namirsab/circuit-tracks-tools/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/namirsab/circuit-tracks-tools/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/namirsab/circuit-tracks-tools/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/namirsab/circuit-tracks-tools/releases/tag/v0.1.0
