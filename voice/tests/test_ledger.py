"""State ledger: what the agent knows, undo, German summaries."""

from __future__ import annotations

from contextlib import asynccontextmanager

from circuit_voice.bridge import Bridge
from circuit_voice.device_tools import register_device_tools
from circuit_voice.ledger import Ledger
from circuit_voice.mcp_client import McpBackend


@asynccontextmanager
async def make(server):
    async with McpBackend("inproc") as backend:
        ledger = Ledger()
        b = Bridge(backend, profile="full", ledger=ledger)
        await b.prepare()
        register_device_tools(b)
        yield b, ledger, server[1]


def test_summary_when_nothing_is_loaded():
    text = Ledger().summary_de()
    assert "kein Song" in text.lower() or "nichts" in text.lower()


def test_observe_device_names_the_project_the_user_picked_on_the_pads():
    lg = Ledger()
    lg.observe_device({"project": 33, "playing": None, "bpm": 120.0})
    assert lg.device_project == 34 and lg.bpm == 120.0 and not lg.running
    assert "Projekt 34 ist gewählt" in lg.summary_de() and "120 BPM" in lg.summary_de()
    lg.observe_device({"project": 33, "playing": True, "bpm": 120.0})
    assert lg.running and lg.source == "device" and "spielt Projekt 34" in lg.summary_de()


def test_observe_device_does_not_touch_the_internal_sequencer_state():
    lg = Ledger()
    lg.song_loaded("Nachtfahrt", 118, ["a"], ["a"])
    lg.observe("start_sequencer", {}, None)
    lg.observe_device({"project": None, "playing": True, "bpm": 118.0, "clock_from_here": True})
    assert lg.source == "internal" and lg.bpm == 118 and lg.device_project is None


def test_song_loaded_and_unsaved_flag():
    lg = Ledger()
    lg.song_loaded("Nachtfahrt", 118, ["intro", "chorus"], ["intro", "chorus"])
    assert lg.unsaved is True
    lg.saved(5)
    assert lg.unsaved is False and lg.slot == 5
    lg.observe("set_synth_params", {"synth": 1, "params": {"filter_frequency": 60}}, "ok")
    assert lg.unsaved is True


def test_summary_internal_merges_status():
    lg = Ledger()
    lg.song_loaded("Nachtfahrt", 118, ["intro", "chorus"], ["intro", "chorus"])
    lg.observe("start_sequencer", {}, "started")
    status = {
        "running": True,
        "current_pattern": "chorus",
        "bpm": 118.0,
        "track_mutes": {"drum2": True, "synth1": False},
    }
    text = lg.summary_de(status)
    assert "Nachtfahrt" in text and "chorus" in text and "118" in text
    assert "Drum 2" in text and "Synth 1" not in text
    assert "nicht gespeichert" in text


def test_summary_names_the_new_value_of_the_last_change_not_the_old_one():
    # "Drum 4: eq wieder auf 100" in get_state made the model believe eq was back at 100 (2026-09-20).
    lg = Ledger()
    lg.song_loaded("Nachtfahrt", 118, ["intro"], ["intro"])
    lg.observe("set_drum_params", {"drum": 4, "params": {"eq": 100}}, "ok")
    lg.observe("set_drum_params", {"drum": 4, "params": {"eq": 0}}, "ok")
    text = lg.summary_de()
    assert "letzte Änderung: Drum 4: eq auf 0 (vorher 100)" in text
    assert "wieder auf" not in text
    assert lg.history[-1].text_de == "Drum 4: eq wieder auf 100"  # what undo says


async def test_undo_reverts_the_last_known_change(server):
    async with make(server) as (b, lg, midi):
        await b.dispatch("set_synth_params", {"synth": 1, "params": {"filter_frequency": 60}})
        await b.dispatch("set_synth_params", {"synth": 1, "params": {"filter_frequency": 90}})
        midi.messages.clear()
        out = await b.dispatch("undo_last", {})
        assert "60" in out.response["result"]
        assert any(m[0] == "cc" and m[3] == 60 for m in midi.messages)
        midi.messages.clear()
        out = await b.dispatch("undo_last", {})
        assert "nicht rückgängig" in out.response["result"]
        assert midi.messages == []


async def test_get_state_uses_live_status_when_internal(server):
    async with make(server) as (b, lg, midi):
        lg.song_loaded("Test", 100, ["a"], ["a"])
        await b.dispatch("set_bpm", {"bpm": 105})
        out = await b.dispatch("get_state", {})
        assert "Test" in out.response["result"]
        assert lg.bpm == 105


async def test_mute_is_tracked(server):
    async with make(server) as (b, lg, midi):
        lg.song_loaded("Test", 100, ["a"], ["a"])
        await b.dispatch("mute_track", {"track": "drum1", "muted": True})
        assert lg.mutes["drum1"] is True
        out = await b.dispatch("undo_last", {})
        assert lg.mutes["drum1"] is False
        assert "Drum 1" in out.response["result"]
