"""McpBackend against the in-process hardware server."""

from __future__ import annotations

import pytest

from circuit_voice.mcp_client import McpBackend, MidiNotFound


async def test_inproc_lists_tools_and_calls(server):
    async with McpBackend("inproc") as backend:
        names = await backend.tool_names()
        assert len(names) >= 46
        assert "set_clock_bpm" in names
        result = await backend.call("get_sequencer_status", {})
        assert result.is_error is False
        assert result.data["running"] is False


async def test_unknown_tool_and_tool_errors_do_not_raise(server):
    async with McpBackend("inproc") as backend:
        result = await backend.call("no_such_tool", {})
        assert result.is_error is True
        assert "no_such_tool" in result.text
        result = await backend.call("select_patch", {"synth": 3, "patch_number": 1})
        assert result.is_error is True
        assert "Invalid synth" in result.text


async def test_input_schema_available(server):
    async with McpBackend("inproc") as backend:
        schema = await backend.input_schema("set_bpm")
        assert schema["properties"]["bpm"]["type"] == "number"


async def test_midi_autoconnect_picks_circuit_port(server):
    srv, midi = server
    async with McpBackend("inproc") as backend:
        port = await backend.connect_midi()
        assert port == "Circuit Tracks MIDI"
        assert midi.port_name == "Circuit Tracks MIDI"


async def test_midi_explicit_port(server):
    srv, midi = server
    async with McpBackend("inproc") as backend:
        port = await backend.connect_midi("IAC Driver Bus 1")
        assert port == "IAC Driver Bus 1"


async def test_midi_not_found_lists_ports(server, monkeypatch):
    monkeypatch.setattr("circuit_tracks.midi.MidiConnection.list_output_ports", staticmethod(lambda: ["Other"]))
    async with McpBackend("inproc") as backend:
        with pytest.raises(MidiNotFound) as exc:
            await backend.connect_midi()
        assert "Other" in str(exc.value)
        assert "Circuit Tracks" in str(exc.value)


def test_target_parsing():
    assert McpBackend("inproc").kind == "inproc"
    assert McpBackend("stdio:circuit-tracks-mcp --x").kind == "stdio"
    assert McpBackend("https://link.example/mcp/a/b").kind == "http"
    with pytest.raises(ValueError):
        McpBackend("ftp://nope")
