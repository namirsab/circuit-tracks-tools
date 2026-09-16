"""Composer step: prompt assembly, validation with retries, revision diffs."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from circuit_voice.composer import ComposeFailed, Composer, FakeComposer, check_house_rules, track_edits
from circuit_voice.mcp_client import McpBackend
from circuit_voice.prompt_composer import HOUSE_RULES

SONG = json.loads((Path(__file__).parent / "songs" / "Ethereal Drift.json").read_text())


def broken(**changes):
    song = copy.deepcopy(SONG)
    song.update(changes)
    return song


async def test_retry_then_success_and_failure_after_retries():
    invalid = {"patterns": {}}
    fake = FakeComposer([invalid, SONG])
    composer = Composer(fake, max_retries=2)
    song = await composer.compose("Ein ruhiger Ambient-Track")
    assert song["bpm"] == 65
    assert len(fake.calls) == 2
    assert "rejected" in fake.calls[1][-1][1]
    fake = FakeComposer([invalid, invalid, invalid])
    with pytest.raises(ComposeFailed):
        await Composer(fake, max_retries=2).compose("x")
    assert len(fake.calls) == 3


def test_house_rules_catch_what_the_schema_accepts():
    mixed = copy.deepcopy(SONG)
    mixed["patterns"]["drift"]["length"] = 16
    assert any("same length" in p for p in check_house_rules(mixed))
    no_sounds = broken(sounds={})
    assert any("synth1" in p for p in check_house_rules(no_sounds))
    bad_order = broken(song=["nope"])
    assert any("unknown pattern" in p for p in check_house_rules(bad_order))
    assert check_house_rules(SONG) == []


async def test_prompt_contains_rules_reference_and_current_song(server):
    async with McpBackend("inproc") as backend:
        composer = Composer(FakeComposer([SONG]), backend=backend)
        await composer.prepare()
        assert "same length" in composer.system and HOUSE_RULES in composer.system
        assert "Reference: song_format" in composer.system and "Reference: best_practices" in composer.system
        fake = composer.model
        await composer.compose("Die Hi-Hats leiser", current_song=SONG)
        user_text = fake.calls[0][0][1]
        assert "Revision request" in user_text and '"emergence"' in user_text


def test_track_edits_only_for_step_changes():
    new = copy.deepcopy(SONG)
    first_pattern = next(iter(new["patterns"]))
    tracks = new["patterns"][first_pattern]["tracks"]
    track = next(t for t in tracks if set(tracks[t]) == {"steps"})
    tracks[track]["steps"] = {"0": {"note": 60, "velocity": 100}}
    edits = track_edits(SONG, new)
    assert edits == [(first_pattern, track, tracks[track])]
    new["bpm"] = 70
    assert track_edits(SONG, new) is None
    renamed = copy.deepcopy(SONG)
    renamed["patterns"]["extra"] = copy.deepcopy(SONG["patterns"]["drift"])
    assert track_edits(SONG, renamed) is None
    assert track_edits(SONG, copy.deepcopy(SONG)) == []
