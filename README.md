# circuit-tracks-tools

A Python library and MCP server for controlling a [Novation Circuit Tracks](https://novationmusic.com/products/circuit-tracks) synthesizer via MIDI. Control your Circuit Tracks from Python scripts or let an AI agent create music through natural language — just like pair-programming, but for music.

## Features

- **Synth & drum programming** — select patches, play notes/chords, tweak parameters via CC/NRPN
- **Pattern sequencing** — create and edit step-sequencer patterns across synth, drum, and MIDI tracks
- **Patch editing** — read, modify, and save synth patches; load `.syx` patch files
- **Project management** — transfer `.ncs` project files to the device
- **Macro control** — configure and sweep macro knobs for expressive parameter control
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

## Windows Setup

The library and MCP server work on Windows. Install Python 3.11+ from [python.org](https://www.python.org/downloads/) and then:

```bash
pip install circuit-tracks-tools[mcp]
```

The Circuit Tracks appears as a USB MIDI device automatically — no additional driver installation is needed. You can verify it shows up in Device Manager under "Sound, video and game controllers".

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
  morph.py            # Parameter morphing engine
src/circuit_mcp/      # MCP server (thin wrapper over the library)
  server.py           # All MCP tool definitions
docs/
  ncs-format.md       # Reverse-engineered .ncs file format spec
```

## License

[MIT](LICENSE)
