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
    assert parse_notes("C3, E3 G3") == [[60], [64], [67]]
    assert parse_notes([["C3", "E3"], 67]) == [[60, 64], [67]]
    assert parse_notes(["C3", 64]) == [[60], [64]]
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


async def test_velocities_set_step_and_drum_sample(server):
    async with agent(server) as (bridge, ledger, midi):
        await bridge.dispatch("new_song", {"name": "Probe", "bpm": 100})
        await bridge.dispatch("write_drums", {"track": "drum3", "steps": [1, 2, 3, 4], "velocities": [110, 70]})
        steps = ledger.song_json["patterns"]["pattern1"]["tracks"]["drum3"]["steps"]
        assert [steps[k]["velocity"] for k in ("0", "1", "2", "3")] == [110, 70, 110, 70]
        await bridge.dispatch("write_notes", {"track": "synth1", "notes": "C3 D3 E3", "velocities": "120 60"})
        notes = ledger.song_json["patterns"]["pattern1"]["tracks"]["synth1"]["steps"]
        assert [notes[k]["velocity"] for k in ("0", "4", "8")] == [120, 60, 120]

        out = await bridge.dispatch("set_step", {"track": "drum3", "step": 2, "velocity": 40})
        assert "Schritt 2" in out.response["result"] and steps is not None
        assert ledger.song_json["patterns"]["pattern1"]["tracks"]["drum3"]["steps"]["1"]["velocity"] == 40
        out = await bridge.dispatch("set_step", {"track": "synth1", "step": 5, "note": "G3", "gate": 2})
        assert ledger.song_json["patterns"]["pattern1"]["tracks"]["synth1"]["steps"]["4"]["note"] == 67
        out = await bridge.dispatch("set_step", {"track": "synth1", "step": 7, "velocity": 50})
        assert "liegt nichts" in out.response["result"]
        await bridge.dispatch("undo_last", {})
        assert ledger.song_json["patterns"]["pattern1"]["tracks"]["synth1"]["steps"]["4"]["note"] == 62

        out = await bridge.dispatch("set_drum_sample", {"drum": 2, "name": "snare"})
        assert "Snare" in out.response["result"] and "nach dem Speichern" in out.response["result"]
        assert ledger.song_json["sounds"]["drum2"]["sample"] == 2
        out = await bridge.dispatch("set_drum_sample", {"drum": 1, "number": 64})
        assert ledger.song_json["sounds"]["drum1"]["sample"] == 63


async def test_save_captures_live_patches_and_drum_values(server, monkeypatch):
    from circuit_voice.mcp_client import ToolResult

    exported = []

    def fake_export(song, midi, slot=0, name="", template_path=None):
        exported.append(song)
        return {"status": "ok"}

    monkeypatch.setattr("circuit_tracks.song.export_song_to_device", fake_export)
    async with agent(server) as (bridge, ledger, midi):
        real_call = bridge.backend.call

        async def fake_call(name, args=None):
            if name == "get_synth_patch":
                return ToolResult(
                    text="", data={"name": f"Live {args['synth']}", "params": {"filter_frequency": 33}}, is_error=False
                )
            return await real_call(name, args)

        bridge.backend.call = fake_call  # type: ignore[method-assign]
        await bridge.dispatch("new_song", {"name": "Probe", "bpm": 90})
        await bridge.dispatch("write_drums", {"track": "drum1", "steps": [1]})
        await bridge.dispatch("set_drum_params", {"drum": 1, "params": {"decay": 44, "level": 80}})
        await bridge.dispatch("set_project_params", {"params": {"synth1_level": 70}})
        await bridge.dispatch("save_to_device", {"slot": 12, "confirmed": True})
        song = exported[0]
        assert song.sounds["synth1"].name == "Live 1" and song.sounds["synth1"].params["filter_frequency"] == 33
        assert song.sounds["drum1"].decay == 44 and song.sounds["drum1"].level == 80
        assert ledger.song_json["mixer"]["synth1"]["level"] == 70
        assert ledger.device_dirty is False and ledger.device_bpm[12] == 90.0


async def test_chords_are_several_notes_on_one_step(server):
    """Hardware 2026-09-17: 'E3 G3 B3' became three single notes; the agent said it cannot play chords."""
    async with agent(server) as (bridge, ledger, midi):
        await bridge.dispatch("new_song", {})
        out = await bridge.dispatch(
            "write_notes",
            {"track": "synth1", "notes": [["E3", "G3", "B3"], "E3", ["A2", "C3", "E3"]], "start_step": 3, "spacing": 4},
        )
        assert "Akkord" in out.response["result"]
        steps = ledger.song_json["patterns"]["pattern1"]["tracks"]["synth1"]["steps"]
        assert steps["2"]["notes"] == [64, 67, 71] and "note" not in steps["2"]
        assert steps["6"]["note"] == 64
        assert steps["10"]["notes"] == [57, 60, 64]
        engine = (await bridge.backend.call("get_pattern", {"name": "pattern1"})).data
        assert engine["tracks"]["synth1"]["steps"]["2"]["notes"] == [64, 67, 71]


async def test_chord_as_text_and_on_a_single_step(server):
    async with agent(server) as (bridge, ledger, midi):
        await bridge.dispatch("new_song", {})
        await bridge.dispatch("write_notes", {"track": "synth1", "notes": "C3+E3+G3 D3", "start_step": 1})
        steps = ledger.song_json["patterns"]["pattern1"]["tracks"]["synth1"]["steps"]
        assert steps["0"]["notes"] == [60, 64, 67] and steps["4"]["note"] == 62
        await bridge.dispatch("set_step", {"track": "synth1", "step": 5, "notes": ["D3", "F3", "A3"]})
        steps = ledger.song_json["patterns"]["pattern1"]["tracks"]["synth1"]["steps"]
        assert steps["4"]["notes"] == [62, 65, 69] and "note" not in steps["4"]
