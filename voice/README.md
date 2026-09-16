# Circuit Voice

German voice control for the Novation Circuit Tracks. A Gemini Live audio
session drives a curated set of the `circuit-tracks-mcp` tools so that people
who cannot use the pads and knobs, or cannot see the device, can build music in
the MCP server's internal sequencer, perform it, and save it to the device.

Spec: [`docs/specs/voice-agent.md`](../docs/specs/voice-agent.md).

## Install

```sh
# from the repo root, inside venv/
pip install -e ".[mcp]" && pip install -e "./voice[dev]"
export GEMINI_API_KEY=...
circuit-voice --list-devices
```

## Test

```sh
pytest voice/tests
ruff check voice
```
