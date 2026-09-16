"""Workflow B: device transport, tempo, project select and import."""

from __future__ import annotations

from contextlib import asynccontextmanager

from circuit_tracks.constants import PROJECT_CHANNEL
from circuit_voice.bridge import Bridge
from circuit_voice.device_tools import register_device_tools
from circuit_voice.ledger import Ledger
from circuit_voice.mcp_client import McpBackend


@asynccontextmanager
async def agent(server):
    async with McpBackend("inproc") as backend:
        ledger = Ledger()
        bridge = Bridge(backend, profile="full", ledger=ledger)
        await bridge.prepare()
        register_device_tools(bridge)
        yield bridge, ledger, server[1]


async def test_select_project_queued_while_running_instant_when_stopped(server, monkeypatch):
    monkeypatch.setattr("circuit_mcp.server._read_device_bpm", lambda midi: 100.0)
    async with agent(server) as (bridge, ledger, midi):
        out = await bridge.dispatch("select_project", {"number": 3})
        assert ("pc", PROJECT_CHANNEL, 2) in midi.messages and "gewählt" in out.response["result"]
        out = await bridge.dispatch("transport", {"action": "start"})
        assert ledger.source == "device" and ledger.running and ledger.bpm == 100.0
        assert "100" in out.response["result"]
        midi.messages.clear()
        await bridge.dispatch("select_project", {"number": 3})
        assert ("pc", PROJECT_CHANNEL, 66) in midi.messages  # queued
        midi.messages.clear()
        await bridge.dispatch("select_project", {"number": 3, "immediate": True})
        assert ("pc", PROJECT_CHANNEL, 2) in midi.messages
        await bridge.dispatch("transport", {"action": "stop"})
        assert ledger.running is False


async def test_confirmation_only_after_sound_changes(server):
    async with agent(server) as (bridge, ledger, midi):
        await bridge.dispatch("select_project", {"number": 1})
        await bridge.dispatch("set_synth_params", {"synth": 1, "params": {"filter_frequency": 90}})
        out = await bridge.dispatch("select_project", {"number": 2})
        assert "needs_confirmation" in out.response["result"]
        assert ("pc", PROJECT_CHANNEL, 1) not in midi.messages
        out = await bridge.dispatch("select_project", {"number": 2, "confirmed": True})
        assert ("pc", PROJECT_CHANNEL, 1) in midi.messages and ledger.device_dirty is False
        out = await bridge.dispatch("select_project", {"number": 3})
        assert "gewählt" in out.response["result"]


async def test_transport_reads_device_bpm_once_and_caches(server, monkeypatch):
    reads = []

    def fake_read(midi):
        reads.append(1)
        return 96.0

    monkeypatch.setattr("circuit_mcp.server._read_device_bpm", fake_read)
    async with agent(server) as (bridge, ledger, midi):
        await bridge.dispatch("select_project", {"number": 5})
        await bridge.dispatch("transport", {"action": "start"})
        await bridge.dispatch("transport", {"action": "stop"})
        await bridge.dispatch("transport", {"action": "start"})
        assert len(reads) == 1 and ledger.device_bpm[5] == 96.0
        await bridge.dispatch("transport", {"action": "stop"})


async def test_set_tempo_uses_the_clock_tool(server, monkeypatch):
    monkeypatch.setattr("circuit_mcp.server._read_device_bpm", lambda midi: 120.0)
    async with agent(server) as (bridge, ledger, midi):
        out = await bridge.dispatch("set_tempo", {"bpm": 90})
        assert "vorgemerkt" in out.response["result"]
        await bridge.dispatch("transport", {"action": "start"})
        out = await bridge.dispatch("set_tempo", {"bpm": 100})
        assert out.response["result"] == "Tempo ist jetzt 100."
        assert ledger.bpm == 100.0
        await bridge.dispatch("transport", {"action": "stop"})


async def test_transport_start_stops_internal_sequencer_first(server, monkeypatch):
    monkeypatch.setattr("circuit_mcp.server._read_device_bpm", lambda midi: 120.0)
    async with agent(server) as (bridge, ledger, midi):
        ledger.song_loaded("x", 100, ["a"], ["a"])
        await bridge.backend.call("set_pattern", {"name": "a", "tracks": {"drum1": {"steps": {"0": {}}}}})
        await bridge.dispatch("start_sequencer", {})
        assert ledger.source == "internal" and ledger.running
        out = await bridge.dispatch("transport", {"action": "start"})
        assert "internen Sequenzer gestoppt" in out.response["result"]
        assert ledger.source == "device"
        await bridge.dispatch("transport", {"action": "stop"})


async def test_import_project(server):
    from circuit_voice.mcp_client import ToolResult

    song = {"name": "Alt", "bpm": 111, "patterns": {"p1": {"tracks": {}}}, "song": ["p1"]}
    async with agent(server) as (bridge, ledger, midi):
        real_call = bridge.backend.call

        async def fake_call(name, args=None):
            if name == "read_project":
                assert args == {"slot": 6}
                return ToolResult(text="", data={**song, "_meta": {"slot": 6}}, is_error=False)
            return await real_call(name, args)

        bridge.backend.call = fake_call  # type: ignore[method-assign]
        out = await bridge.dispatch("import_project", {"number": 7})
        assert out.blocking is False
        assert "Alt" in out.response["result"] and ledger.song_json["bpm"] == 111
        assert ledger.slot == 7 and ledger.unsaved is False


async def test_device_mode_mute_uses_levels_and_restores(server, monkeypatch):
    from circuit_tracks.constants import DRUM_CC

    monkeypatch.setattr("circuit_mcp.server._read_device_bpm", lambda midi: 120.0)
    async with agent(server) as (bridge, ledger, midi):
        await bridge.dispatch("select_project", {"number": 1})
        await bridge.dispatch("transport", {"action": "start"})
        midi.messages.clear()
        out = await bridge.dispatch("mute_track", {"track": "drum2", "muted": True})
        level_cc = DRUM_CC[2]["level"]
        assert ("cc", 9, level_cc, 0) in midi.messages and "stumm" in out.response["result"]
        assert ledger.mutes["drum2"] is True
        midi.messages.clear()
        out = await bridge.dispatch("mute_track", {"track": "drum2", "muted": False})
        assert ("cc", 9, level_cc, 100) in midi.messages
        await bridge.dispatch("mute_track", {"track": "synth1", "muted": True})
        assert any(m[0] == "cc" and m[3] == 0 for m in midi.messages[-1:])
        out = await bridge.dispatch("undo_last", {})
        assert "Synth 1" in out.response["result"] and ledger.mutes["synth1"] is False
        await bridge.dispatch("transport", {"action": "stop"})


async def test_internal_mode_mute_uses_the_engine(server):
    async with agent(server) as (bridge, ledger, midi):
        ledger.song_loaded("x", 100, ["a"], ["a"])
        await bridge.backend.call("set_pattern", {"name": "a", "tracks": {"drum1": {"steps": {"0": {}}}}})
        await bridge.dispatch("start_sequencer", {})
        midi.messages.clear()
        out = await bridge.dispatch("mute_track", {"track": "drum1", "muted": True})
        assert not any(m[0] == "cc" for m in midi.messages)
        assert ledger.mutes["drum1"] is True and "stumm" in out.response["result"]
        await bridge.dispatch("stop_sequencer", {})


async def test_start_sequencer_stops_the_device_first(server, monkeypatch):
    monkeypatch.setattr("circuit_mcp.server._read_device_bpm", lambda midi: 120.0)
    async with agent(server) as (bridge, ledger, midi):
        await bridge.dispatch("select_project", {"number": 2})
        await bridge.dispatch("transport", {"action": "start"})
        ledger.song_loaded("x", 100, ["a"], ["a"])
        await bridge.backend.call("set_pattern", {"name": "a", "tracks": {"drum1": {"steps": {"0": {}}}}})
        midi.messages.clear()
        out = await bridge.dispatch("start_sequencer", {})
        kinds = [m[:2] for m in midi.messages]
        assert ("realtime", "stop") in kinds
        assert (
            kinds.index(("realtime", "stop")) < kinds.index(("realtime", "start"))
            if ("realtime", "start") in kinds
            else True
        )
        assert "Gerät gestoppt" in out.response["result"]
        assert ledger.source == "internal" and ledger.running
        out = await bridge.dispatch("get_state", {})
        assert "läuft" in out.response["result"]
        await bridge.dispatch("stop_sequencer", {})
        await bridge.dispatch("select_project", {"number": 2})
        out = await bridge.dispatch("get_state", {})
        assert "Projekt 2" in out.response["result"]


async def test_set_macro_moves_the_hardware_knob(server):
    from circuit_tracks.constants import SYNTH_CC

    async with agent(server) as (bridge, ledger, midi):
        out = await bridge.dispatch("set_macro", {"synth": 1, "macro": 5, "value": 0})
        assert ("cc", 0, SYNTH_CC["macro_knob5"], 0) in midi.messages
        assert "Makro 5" in out.response["result"]
        await bridge.dispatch("set_macro", {"synth": 1, "macro": 5, "value": 90})
        midi.messages.clear()
        out = await bridge.dispatch("undo_last", {})
        assert ("cc", 0, SYNTH_CC["macro_knob5"], 0) in midi.messages
        assert "Rückgängig" in out.response["result"]
