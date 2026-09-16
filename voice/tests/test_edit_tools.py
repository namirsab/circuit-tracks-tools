"""Precise editing: note names, new song, write_notes, write_drums, clear_track, and saving the edits."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from circuit_voice.bridge import Bridge
from circuit_voice.device_tools import register_device_tools
from circuit_voice.edit_tools import empty_song, note_number, parse_notes, register_edit_tools
from circuit_voice.ledger import Ledger
from circuit_voice.mcp_client import McpBackend
from circuit_voice.songs import SongLibrary, register_song_tools
from circuit_voice.tool_cards import ArgumentError


@asynccontextmanager
async def agent(server):
    async with McpBackend("inproc") as backend:
        ledger = Ledger()
        bridge = Bridge(backend, profile="full", ledger=ledger)
        await bridge.prepare()
        register_song_tools(bridge, SongLibrary(None))
        register_device_tools(bridge)
        register_edit_tools(bridge)
        yield bridge, ledger, server[1]


def test_note_names_follow_the_device():
    assert note_number("C3") == 60
    assert note_number("c3") == 60
    assert note_number("F#4") == 78
    assert note_number("Bb2") == 58
    assert note_number("H3") == 71
    assert note_number("Cis3") == 61
    assert note_number("C") == 60
    assert note_number("60") == 60 and note_number(72) == 72
    assert parse_notes("C3, E3 G3") == [60, 64, 67]
    assert parse_notes(["C3", 64]) == [60, 64]
    for bad in ("X3", "C99", "", "Katze"):
        with pytest.raises(ArgumentError):
            parse_notes(bad)


def test_empty_song_validates():
    from circuit_tracks.song_schema import SongSchema

    SongSchema.model_validate(empty_song("Test", 100))


async def test_new_song_then_notes_and_drums(server):
    async with agent(server) as (bridge, ledger, midi):
        out = await bridge.dispatch("write_notes", {"track": "synth1", "notes": "C3"})
        assert out.response["error"].startswith("Es ist kein Song geladen")
        out = await bridge.dispatch("new_song", {"name": "Probe", "bpm": 100})
        assert "Probe" in out.response["result"] and ledger.bpm == 100 and ledger.patterns == ["pattern1"]

        out = await bridge.dispatch(
            "write_notes", {"track": "synth1", "notes": "C3 E3 G3", "start_step": 1, "spacing": 4}
        )
        assert out.response["result"].startswith("3 Noten auf synth1 in pattern1 ab Schritt 1")
        steps = ledger.song_json["patterns"]["pattern1"]["tracks"]["synth1"]["steps"]
        assert {k: v["note"] for k, v in steps.items()} == {"0": 60, "4": 64, "8": 67}
        engine = await bridge.backend.call("get_pattern", {"name": "pattern1"})
        assert engine.data["tracks"]["synth1"]["steps"]["4"]["notes"] == [64]

        out = await bridge.dispatch("write_notes", {"track": "synth1", "notes": "C4"})
        assert "ab Schritt 13" in out.response["result"]
        assert ledger.song_json["patterns"]["pattern1"]["tracks"]["synth1"]["steps"]["12"]["note"] == 72

        out = await bridge.dispatch("write_notes", {"track": "synth1", "notes": "D3", "replace": True})
        assert list(ledger.song_json["patterns"]["pattern1"]["tracks"]["synth1"]["steps"]) == ["0"]

        out = await bridge.dispatch("write_drums", {"track": "drum1", "steps": [1, 5, 9, 13]})
        assert out.response["result"] == "drum1 in pattern1 schlägt auf 1, 5, 9, 13."
        assert set(ledger.song_json["patterns"]["pattern1"]["tracks"]["drum1"]["steps"]) == {"0", "4", "8", "12"}
        out = await bridge.dispatch("write_drums", {"track": "drum1", "steps": [33]})
        assert "zwischen 1 und 32" in out.response["error"]

        out = await bridge.dispatch("clear_track", {"track": "drum1"})
        assert "leer" in out.response["result"]
        assert "drum1" not in ledger.song_json["patterns"]["pattern1"]["tracks"]

        out = await bridge.dispatch("write_notes", {"pattern": "Pattern 2", "track": "synth2", "notes": "C2"})
        assert "pattern2" in out.response["result"] and ledger.song_order == ["pattern1", "pattern2"]
        out = await bridge.dispatch("write_notes", {"pattern": "chorus", "track": "synth2", "notes": "C2"})
        assert "gibt es nicht" in out.response["error"]


async def test_edits_reach_the_device_on_save(server, monkeypatch):
    exported = []

    def fake_export(song, midi, slot=0, name="", template_path=None):
        exported.append(song)
        return {"status": "ok"}

    monkeypatch.setattr("circuit_tracks.song.export_song_to_device", fake_export)
    async with agent(server) as (bridge, ledger, midi):
        await bridge.dispatch("new_song", {"name": "Probe", "bpm": 90})
        await bridge.dispatch("write_notes", {"track": "synth1", "notes": "C3 G3", "spacing": 8})
        await bridge.dispatch("write_drums", {"track": "drum1", "steps": [1, 9]})
        await bridge.dispatch("start_sequencer", {})
        out = await bridge.dispatch("save_to_device", {"slot": 10, "confirmed": True})
        assert "Slot 10" in out.response["result"]
        song = exported[0]
        pat = song.patterns["pattern1"]
        assert pat.tracks["synth1"]["steps"]["8"]["note"] == 67
        assert "0" in pat.tracks["drum1"]["steps"]
        assert ledger.running is False


async def test_step_edits_are_undoable(server):
    async with agent(server) as (bridge, ledger, midi):
        await bridge.dispatch("new_song", {"name": "Probe", "bpm": 100})
        await bridge.dispatch("write_drums", {"track": "drum1", "steps": [1, 5]})
        await bridge.dispatch("write_drums", {"track": "drum1", "steps": [9, 13]})
        out = await bridge.dispatch("undo_last", {})
        assert "drum1 in pattern1 ist wieder wie vorher" in out.response["result"]
        assert set(ledger.song_json["patterns"]["pattern1"]["tracks"]["drum1"]["steps"]) == {"0", "4"}
        engine = await bridge.backend.call("get_pattern", {"name": "pattern1"})
        assert set(engine.data["tracks"]["drum1"]["steps"]) == {"0", "4"}
        await bridge.dispatch("undo_last", {})
        assert "drum1" not in ledger.song_json["patterns"]["pattern1"]["tracks"]
        await bridge.dispatch("write_notes", {"track": "synth1", "notes": "C3 D3"})
        await bridge.dispatch("clear_track", {"track": "synth1"})
        await bridge.dispatch("undo_last", {})
        assert list(ledger.song_json["patterns"]["pattern1"]["tracks"]["synth1"]["steps"]) == ["0", "4"]
