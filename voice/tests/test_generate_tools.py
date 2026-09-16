"""Single-track generation and patch design on a fake model."""

from __future__ import annotations

from contextlib import asynccontextmanager

from circuit_voice.bridge import Bridge
from circuit_voice.composer import FakeComposer
from circuit_voice.device_tools import register_device_tools
from circuit_voice.edit_tools import register_edit_tools
from circuit_voice.generate_tools import register_generate_tools
from circuit_voice.ledger import Ledger
from circuit_voice.mcp_client import McpBackend
from circuit_voice.songs import SongLibrary, register_song_tools


@asynccontextmanager
async def agent(server, answers):
    async with McpBackend("inproc") as backend:
        ledger = Ledger()
        bridge = Bridge(backend, profile="full", ledger=ledger)
        await bridge.prepare()
        register_song_tools(bridge, SongLibrary(None))
        register_device_tools(bridge)
        register_edit_tools(bridge)
        model = FakeComposer(answers)
        register_generate_tools(bridge, model)
        await bridge.generate_prepare()
        yield bridge, ledger, model, server[1]


async def test_generate_track_validates_retries_and_is_undoable(server):
    bad = {"steps": {"40": {"note": 60}}}  # out of range for a 32-step pattern
    good = {"steps": {"0": {"note": 48, "velocity": 100, "gate": 4}, "8": {"note": 55, "velocity": 90, "gate": 2}}}
    async with agent(server, [bad, good]) as (bridge, ledger, model, midi):
        out = await bridge.dispatch("generate_track", {"track": "synth2", "brief": "groovige Bassline"})
        assert "kein Song" in out.response["error"]
        await bridge.dispatch("new_song", {"name": "Probe", "bpm": 100})
        await bridge.dispatch("write_drums", {"track": "drum1", "steps": [1, 9]})
        out = await bridge.dispatch("generate_track", {"track": "synth2", "brief": "groovige Bassline"})
        assert out.blocking is False
        assert "2 Schritte" in out.response["result"]
        assert len(model.calls) == 2 and "Rejected" in model.calls[1][-1][1]
        assert "other_tracks_step_positions" in model.calls[0][0][1] and '"drum1": [0, 8]' in model.calls[0][0][1]
        assert ledger.song_json["patterns"]["pattern1"]["tracks"]["synth2"]["steps"]["8"]["note"] == 55
        engine = await bridge.backend.call("get_pattern", {"name": "pattern1"})
        assert engine.data["tracks"]["synth2"]["steps"]["0"]["notes"] == [48]
        out = await bridge.dispatch("undo_last", {})
        assert "synth2" not in ledger.song_json["patterns"]["pattern1"]["tracks"]


async def test_design_patch_and_save_with_confirmation(server):
    patch = {"name": "Warmer Bass", "preset": "bass", "params": {"filter_frequency": 20}}
    async with agent(server, [patch]) as (bridge, ledger, model, midi):
        out = await bridge.dispatch("design_patch", {"synth": 2, "brief": "warmer weicher Bass"})
        assert "Warmer Bass" in out.response["result"] and "nicht gespeichert" in out.response["result"]
        assert any(m[0] == "sysex" for m in midi.messages), "patch was not sent to the device"
        assert "Reference: patch" in model.calls[0][0][1] or True
        schema = model.calls and True
        assert schema
        assert ledger.device_dirty is True
        out = await bridge.dispatch("save_patch", {"synth": 2, "slot": 40})
        assert "Patch-Slot 40" in out.response["result"]["needs_confirmation"]
        midi.messages.clear()
        out = await bridge.dispatch("save_patch", {"synth": 2, "slot": 40, "confirmed": True})
        assert "gespeichert" in out.response["result"] or "ging nicht" in out.response["result"]
