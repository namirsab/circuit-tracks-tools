# Circuit Tracks MCP Server

An MCP (Model Context Protocol) server that lets AI agents control a [Novation Circuit Tracks](https://novationmusic.com/products/circuit-tracks) synthesizer via MIDI. Connect your Circuit Tracks to your laptop and create music by prompting an AI — just like writing software with Claude Code.

## What it does

- **Synth & drum programming** — select patches, play notes/chords, tweak parameters via CC/NRPN
- **Pattern sequencing** — create and edit step-sequencer patterns across synth and drum tracks
- **Patch editing** — read, modify, and save synth patches; load `.syx` patch files
- **Project management** — transfer `.ncs` project files to the device
- **Macro control** — configure and sweep macro knobs for expressive parameter control
- **Live transport** — start/stop sequencer, set BPM, mute tracks, queue patterns

## Requirements

- Python 3.11+
- A Novation Circuit Tracks connected via USB

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install .
```

## Usage with Claude Code

Add the MCP server to your Claude Code configuration:

```json
{
  "mcpServers": {
    "circuit-tracks": {
      "command": "/path/to/.venv/bin/circuit-mcp"
    }
  }
}
```

Then talk to Claude:

> "Let's create a dark techno loop"

The agent will program patterns on your Circuit Tracks as if a human were doing it — selecting sounds, writing sequences, adjusting parameters — and iterate with you on the result.

## Project structure

```
src/circuit_mcp/
  server.py         # MCP server — all tool definitions
  midi.py           # MIDI connection handling
  sequencer.py      # Pattern/step sequencer engine
  patch.py          # Synth patch read/write
  patch_builder.py  # Patch construction helpers
  constants.py      # CC numbers, NRPN mappings, channel assignments
  macros.py         # Macro knob configuration
  ncs_parser.py     # .ncs project file parser
  ncs_transfer.py   # SysEx project/patch transfer
  song.py           # Song structure
docs/
  ncs-format.md     # Reverse-engineered .ncs file format spec
```
