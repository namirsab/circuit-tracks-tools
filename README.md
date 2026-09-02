# circuit-tracks-tools

[![PyPI](https://img.shields.io/pypi/v/circuit-tracks-tools)](https://pypi.org/project/circuit-tracks-tools/)
[![Python](https://img.shields.io/pypi/pyversions/circuit-tracks-tools)](https://pypi.org/project/circuit-tracks-tools/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Built with Claude Code](https://img.shields.io/badge/Built%20with-Claude%20Code-blue?logo=claude)](https://claude.ai/claude-code)

A Python library and MCP server for controlling a [Novation Circuit Tracks](https://novationmusic.com/products/circuit-tracks) synthesizer via MIDI. Control your Circuit Tracks from Python scripts or let an AI agent create music through natural language — just like pair-programming, but for music.

## Features

- **Song format** — create full songs (patterns, patches, macros, FX) in a single call and export to the device
- **Synth & drum programming** — select patches, play notes/chords, tweak parameters via CC/NRPN
- **Patch builder** — construct synth patches from scratch with oscillators, filters, envelopes, mod matrix, and macros
- **Pattern sequencing** — create and edit step-sequencer patterns across synth, drum, and MIDI tracks with p-lock automation
- **Patch editing** — read, modify, and save synth patches; load `.syx` patch files
- **Project management** — read and write `.ncs` project files; transfer projects to and from the device over SysEx
- **Parameter morphing** — smoothly interpolate synth, drum, and project parameters over time
- **Macro control** — configure and sweep macro knobs for expressive parameter control
- **Sidechain automation** — automate volume ducking across all 4 tracks with preset sidechain curves
- **Scale quantization** — quantize notes to any of the Circuit Tracks' built-in scales
- **Live transport** — start/stop sequencer, set BPM, mute tracks, queue patterns

## Requirements

- Python 3.11+
- A Novation Circuit Tracks connected via USB
- Tested with firmware **1.2.1** (latest as of April 2026) — other versions may work but are untested

## Installation

### As a Python library

```bash
pip install circuit-tracks-tools
```

### With MCP server (for AI agents)

```bash
pip install circuit-tracks-tools[mcp]
```

## Quick Start: Python Library

```python
from circuit_tracks import MidiConnection, PatchBuilder

# Connect to the Circuit Tracks
midi = MidiConnection()
midi.connect("Circuit Tracks MIDI")

# Play a C major chord on Synth 1
midi.note_on(channel=0, note=60, velocity=100)
midi.note_on(channel=0, note=64, velocity=100)
midi.note_on(channel=0, note=67, velocity=100)

# Build a pad patch from scratch
patch = PatchBuilder.preset_pad(cutoff=80, attack=40, release=90)
```

See the [API Reference](src/circuit_tracks/API.md) for full library documentation.

## Quick Start: MCP Server

The MCP server runs as a subprocess launched by your AI agent. The easiest setup uses [uvx](https://docs.astral.sh/uv/guides/tools/) (requires [uv](https://docs.astral.sh/uv/getting-started/installation/)):

### Claude Code

Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "circuit-tracks": {
      "command": "uvx",
      "args": ["--from", "circuit-tracks-tools[mcp]", "circuit-tracks-mcp"]
    }
  }
}
```

Then talk to Claude:

> "Let's create a dark techno loop"

The agent will program patterns on your Circuit Tracks as if a human were doing it — selecting sounds, writing sequences, adjusting parameters — and iterate with you on the result.

### Claude Desktop (macOS)

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "circuit-tracks": {
      "command": "uvx",
      "args": ["--from", "circuit-tracks-tools[mcp]", "circuit-tracks-mcp"]
    }
  }
}
```

### Claude Desktop (Windows)

Edit `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "circuit-tracks": {
      "command": "uvx",
      "args": ["--from", "circuit-tracks-tools[mcp]", "circuit-tracks-mcp"]
    }
  }
}
```

### Without uvx

If you prefer a manual setup, install into a virtual environment and use the full path:

```json
{
  "mcpServers": {
    "circuit-tracks": {
      "command": "/path/to/venv/bin/circuit-tracks-mcp"
    }
  }
}
```

## No hardware? Use Web Tracks in the browser

[Web Tracks](webapp/) is a browser emulation of the Circuit Tracks that ships in
this repository (live at [webtracks.namirsab.dev](https://webtracks.namirsab.dev/)).
It exposes the same MCP tool surface as the hardware server, so the workflow above
works without a device: press **Connect an AI agent…** in its sidebar, add the URL
it shows to Claude Code (`claude mcp add --transport http webtracks <url>`), Claude
Desktop or claude.ai, and watch the agent play on the pads. The URL is served by the
small [Agent Link relay](link/); the tools also register with WebMCP
(`document.modelContext`) and as `window.webtracks` on the page. Details in
[webapp/README.md](webapp/README.md#ai-agents-mcp) and the design notes in
[docs/webmcp-plan.md](docs/webmcp-plan.md).

## Windows Setup

The library and MCP server work on Windows. Install Python 3.11+ from [python.org](https://www.python.org/downloads/) and then:

```bash
pip install circuit-tracks-tools[mcp]
```

The Circuit Tracks appears as a USB MIDI device automatically — no additional driver installation is needed. You can verify it shows up in Device Manager under "Sound, video and game controllers".

## Build from Source

```bash
git clone https://github.com/namirsab/circuit-tracks-tools.git
cd circuit-tracks-tools
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -e ".[mcp,dev]"

# Set up pre-commit hooks
pre-commit install

# Run tests (no hardware required — MIDI is mocked)
pytest

# Build a distributable wheel
pip install build
python -m build
```

## Project Structure

```
src/circuit_tracks/   # Standalone library for Circuit Tracks control
  midi.py             # MIDI connection handling
  sequencer.py        # Pattern/step sequencer engine
  patch.py            # Synth patch read/write
  patch_builder.py    # Patch construction helpers
  constants.py        # CC numbers, NRPN mappings, channel assignments
  macros.py           # Macro knob configuration
  ncs_parser.py       # .ncs project file parser
  ncs_transfer.py     # SysEx project/patch transfer
  song.py             # Song format and device export
  song_schema.py      # Pydantic models and JSON Schema for song format
  morph.py            # Parameter morphing engine
src/circuit_mcp/      # MCP server (thin wrapper over the library)
  server.py           # All MCP tool definitions
webapp/               # Web Tracks: browser emulation (static, no build step)
  js/agent/           # MCP tool registry, headless API, WebMCP + Agent Link adapters
  js/agent/song-compiler.js  # Song JSON -> project (golden-tested against song.py)
  data/               # song.schema.json + parameter-reference.json (generated)
  tests/              # node --test suites and golden .ncs vectors
link/                 # Agent Link relay: MCP endpoint for a Web Tracks tab
scripts/
  generate_agent_data.py     # Regenerates webapp/data and the golden vectors
docs/
  ncs-format.md       # Reverse-engineered .ncs file format spec
  webmcp-plan.md      # Why and how Web Tracks became an MCP target
```

## License

[MIT](LICENSE)

## Disclaimer

This is an unofficial, non-commercial fan project by
[Namir Sayed-Ahmad Baraza](https://namirsab.dev). It is **not affiliated
with, endorsed, or sponsored by Novation or Focusrite plc**. "Circuit
Tracks", "Novation", and "Components" are trademarks of their respective
owners, used here only to describe compatibility. The `.ncs` file format and
SysEx protocol were independently reverse-engineered from files and MIDI
traffic produced by the device; no proprietary code or documentation was
used. The bundled [Web Tracks](webapp/) browser groovebox ships only
code-generated samples, patches, and projects.
