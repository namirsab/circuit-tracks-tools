"""Song library: listing, loose name matching, validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from circuit_voice.songs import SongLibrary

SONGS = Path(__file__).parent / "songs"


def test_names_and_find():
    lib = SongLibrary(SONGS)
    assert lib.names() == ["Ethereal Drift", "Minimal"]
    assert lib.find("ethereal drift").name == "Ethereal Drift.json"
    assert lib.find("Etherial Drifft").name == "Ethereal Drift.json"
    assert lib.find("minimal").name == "Minimal.json"
    assert lib.find("Symphonie") is None


def test_umlauts_fold(tmp_path):
    (tmp_path / "Nachtfahrt.json").write_text(json.dumps({"patterns": {"a": {"tracks": {}}}}))
    (tmp_path / "Grüße.json").write_text("{}")
    lib = SongLibrary(tmp_path)
    assert lib.find("nachtfahrt").name == "Nachtfahrt.json"
    assert lib.find("Gruesse").name == "Grüße.json"
    assert "Nachtfahrt" in lib.closest("Nachtfart")


def test_missing_folder_is_empty():
    lib = SongLibrary("/nonexistent/folder")
    assert lib.names() == []
    assert lib.find("x") is None
    assert SongLibrary(None).names() == []


def test_load_validates():
    lib = SongLibrary(SONGS)
    data = lib.load(lib.find("Ethereal Drift"))
    assert data["bpm"] == 65
    with pytest.raises((ValidationError, ValueError)):
        SongLibrary.load(SONGS.parent / "fixtures" / "declarations.json")
