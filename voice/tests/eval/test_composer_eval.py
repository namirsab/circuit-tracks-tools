"""Composer eval: do German briefs become valid songs with the requested properties?

Opt-in (CIRCUIT_VOICE_EVAL=1, GEMINI_API_KEY). Set CIRCUIT_VOICE_COMPOSER_MODELS
to a comma-separated list to compare models; default gemini-3.8-flash.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from circuit_voice.composer import ComposeFailed, Composer, GeminiComposer, validate_song
from circuit_voice.mcp_client import McpBackend
from circuit_voice.songs import SongLibrary

HERE = Path(__file__).parent
CASES = [json.loads(line) for line in (HERE / "briefs_de.jsonl").read_text().splitlines() if line]
SONGS = SongLibrary(HERE.parent / "songs")
GATES = {"first_try": 0.8, "valid": 0.95, "properties": 0.9}


def tracks_with_steps(song):
    return {t for p in song.get("patterns", {}).values() for t, d in (p.get("tracks") or {}).items() if d.get("steps")}


def check_properties(case, song, base=None) -> list[str]:
    problems = []
    tracks = tracks_with_steps(song)
    if "bpm" in case and abs(float(song.get("bpm", 0)) - case["bpm"]) > 2:
        problems.append(f"bpm {song.get('bpm')} != {case['bpm']}")
    if "bpm_max" in case and float(song.get("bpm", 999)) > case["bpm_max"]:
        problems.append(f"bpm {song.get('bpm')} > {case['bpm_max']}")
    for t in case.get("tracks", []):
        if t not in tracks:
            problems.append(f"missing {t}")
    for t in case.get("no_tracks", []):
        if t in tracks:
            problems.append(f"unwanted {t}")
    if len(song.get("patterns", {})) < case.get("min_patterns", 0):
        problems.append("too few patterns")
    if len(song.get("song") or []) < case.get("song_order_min", 0):
        problems.append("song order too short")
    if base is not None:
        for t in case.get("untouched", []):
            for name, pat in base["patterns"].items():
                if name in song.get("patterns", {}) and (pat.get("tracks") or {}).get(t) != (
                    song["patterns"][name].get("tracks") or {}
                ).get(t):
                    problems.append(f"{t} in {name} changed")
    return problems


class CountingModel:
    def __init__(self, inner):
        self.inner, self.name, self.calls = inner, inner.name, 0

    async def generate(self, *a, **kw):
        self.calls += 1
        return await self.inner.generate(*a, **kw)


@pytest.mark.parametrize("model_id", os.environ.get("CIRCUIT_VOICE_COMPOSER_MODELS", "gemini-3.8-flash").split(","))
async def test_briefs(server, model_id):
    async with McpBackend("inproc") as backend:
        model = CountingModel(GeminiComposer(os.environ["GEMINI_API_KEY"], model_id.strip()))
        composer = Composer(model, backend=backend)
        await composer.prepare()
        rows, first_try, valid, props_ok = [], 0, 0, 0
        for case in CASES:
            base = SONGS.load(SONGS.find(case["base"])) if "base" in case else None
            before, t0 = model.calls, time.perf_counter()
            try:
                song = await composer.compose(case.get("brief") or case["revise"], current_song=base)
            except ComposeFailed as e:
                rows.append(("FAIL", case.get("brief") or case["revise"], str(e)[:60], time.perf_counter() - t0))
                continue
            attempts = model.calls - before
            first_try += attempts == 1
            valid += 1
            problems = check_properties(case, song, base)
            props_ok += not problems
            assert validate_song(song)[0] is not None
            rows.append(
                (
                    "ok" if not problems else "PROP",
                    case.get("brief") or case["revise"],
                    "; ".join(problems)[:60],
                    time.perf_counter() - t0,
                )
            )
    n = len(CASES)
    print(f"\n{model_id}: first-try {first_try}/{n}, valid {valid}/{n}, properties {props_ok}/{n}")
    for status, brief, note, secs in rows:
        print(f"{status:4} {secs:5.1f}s {brief[:55]!r:58} {note}")
    assert first_try / n >= GATES["first_try"], "first-try validity below gate"
    assert valid / n >= GATES["valid"], "validity after retries below gate"
    assert props_ok / n >= GATES["properties"], "property checks below gate"
