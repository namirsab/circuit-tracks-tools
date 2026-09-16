"""Voice tools → bridge → in-process MCP server → mock MIDI, without the Live model."""

from __future__ import annotations

import asyncio
import statistics
import time
from contextlib import asynccontextmanager
from pathlib import Path

from circuit_voice.bridge import Bridge
from circuit_voice.ledger import Ledger
from circuit_voice.mcp_client import McpBackend
from circuit_voice.songs import SongLibrary, register_song_tools

SONGS = Path(__file__).parent / "songs"


@asynccontextmanager
async def agent(server):
    async with McpBackend("inproc") as backend:
        ledger = Ledger()
        bridge = Bridge(backend, profile="full", ledger=ledger)
        await bridge.prepare()
        register_song_tools(bridge, SongLibrary(SONGS))
        yield bridge, ledger, server[1]


async def test_load_song_by_voice_and_play_at_its_tempo(server):
    async with agent(server) as (bridge, ledger, midi):
        out = await bridge.dispatch("load_saved_song", {"name": "ethereal drift"})
        assert "Ethereal Drift" in out.response["result"] or "Ethereal" in out.response["result"]
        assert ledger.song_name and ledger.bpm == 65 and ledger.patterns[0] == "emergence"
        assert ledger.unsaved is True

        midi.messages.clear()
        midi.clock_times.clear()
        out = await bridge.dispatch("start_sequencer", {})
        assert "emergence" in out.response["result"] and "65" in out.response["result"]
        await asyncio.sleep(0.6)
        await bridge.dispatch("stop_sequencer", {})

        assert any(m[0] == "note_on" for m in midi.messages), "pattern did not play"
        assert ("all_notes_off", 0) in midi.messages
        gaps = [b - a for a, b in zip(midi.clock_times, midi.clock_times[1:], strict=False)]
        assert len(gaps) > 5
        expected = 60 / 65 / 24
        assert abs(statistics.median(gaps) - expected) < expected * 0.1
        assert ledger.running is False


async def test_start_without_song_explains(server):
    async with agent(server) as (bridge, ledger, midi):
        out = await bridge.dispatch("start_sequencer", {})
        assert "kein Song" in out.response["error"]
        assert midi.messages == []


async def test_unknown_song_suggests_names(server):
    async with agent(server) as (bridge, ledger, midi):
        out = await bridge.dispatch("load_saved_song", {"name": "Symphonie"})
        assert "Ethereal Drift" in out.response["result"]
        out = await bridge.dispatch("list_saved_songs", {})
        assert "Minimal" in out.response["result"]


async def test_save_needs_confirmation_then_exports(server, monkeypatch):
    exported = []

    def fake_export(song, midi, slot=0, name="", template_path=None):
        exported.append((slot, name))
        midi.send_sysex(b"\xf0export\xf7")
        return {"status": "ok", "bytes": 1}

    monkeypatch.setattr("circuit_tracks.song.export_song_to_device", fake_export)
    async with agent(server) as (bridge, ledger, midi):
        out = await bridge.dispatch("save_to_device", {"slot": 5})
        assert "kein Song" in out.response["result"]

        await bridge.dispatch("load_saved_song", {"name": "Ethereal Drift"})
        out = await bridge.dispatch("save_to_device", {"slot": 5, "name": "Nachtfahrt Lang Name Test"})
        q = out.response["result"]["needs_confirmation"]
        assert "Slot 5" in q and "Nachtfahrt Lang " in q
        assert exported == [] and ledger.unsaved is True

        out = await bridge.dispatch(
            "save_to_device", {"slot": 5, "name": "Nachtfahrt Lang Name Test", "confirmed": True}
        )
        assert exported == [(4, "Nachtfahrt Lang ")]
        assert ("sysex", b"\xf0export\xf7") in midi.messages
        assert "Slot 5" in out.response["result"]
        assert ledger.unsaved is False and ledger.slot == 5

        out = await bridge.dispatch("save_to_device", {"slot": 65, "confirmed": True})
        assert "zwischen 1 und 64" in out.response["error"]
        assert len(exported) == 1


async def test_clock_times_helper_exists(server):
    _, midi = server
    assert hasattr(midi, "clock_times")
    t = time.perf_counter()
    midi.send_clock()
    assert midi.clock_times[-1] >= t
