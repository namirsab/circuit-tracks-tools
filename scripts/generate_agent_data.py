#!/usr/bin/env python3
"""Generate the data files Web Tracks serves to MCP clients.

The Python library is the single source of truth for the song format and the
parameter reference; the webapp ships generated copies so its agent tools
answer exactly like the hardware MCP server. Re-run after changing either.

Outputs:
  webapp/data/song.schema.json            get_song_json_schema()
  webapp/data/parameter-reference.json    every get_parameter_reference section
  webapp/tests/vectors/patches/<case>.json {config, bytes} golden vectors for the
                                          JS patch builder (PATCH_CASES below)
  webapp/tests/vectors/<name>.ncs         song_to_ncs() of each <name>.song.json
  webapp/tests/vectors/<name>.readback.json  ncs_to_song() of that file, as JSON
                                          (golden vectors for the JS song compiler)
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from circuit_mcp.server import _SECTION_DESCRIPTIONS, get_parameter_reference  # noqa: E402
from circuit_tracks.ncs_parser import parse_ncs_from_bytes  # noqa: E402
from circuit_tracks.song import (  # noqa: E402
    _build_patch_bytes,
    _song_data_to_dict,
    ncs_to_song,
    parse_song,
    song_to_ncs,
)
from circuit_tracks.song_schema import get_song_json_schema  # noqa: E402

SECTIONS = list(_SECTION_DESCRIPTIONS)


def build_patch_bytes(config: dict) -> bytes:
    """340 patch bytes for a SynthSoundConfig, through the same path the
    song export takes (parse_song -> _build_patch_bytes), so the vectors pin
    what hardware exports actually get."""
    song = parse_song({"sounds": {"synth1": config}, "patterns": {"p": {"tracks": {"drum1": {"steps": {"0": {}}}}}}})
    return _build_patch_bytes(song.sounds["synth1"])


# Golden patch configs: each becomes webapp/tests/vectors/patches/<case>.json.
PATCH_CASES = {
    "init-plain": {"name": "Init Test"},
    "preset-pad": {"preset": "pad", "name": "Warm Pad"},
    "preset-bass-params": {
        "preset": "bass",
        "params": {"filter_frequency": 30, "osc1_wave": 14, "env1_attack": 3, "lfo1_flags": 7, "mod3_depth": 90},
    },
    "preset-lead-mods": {
        "preset": "lead",
        "name": "Lead Sixteen Ch",
        "mod_matrix": [
            {"source1": "LFO 1+/-", "dest": "osc 1 & 2 pitch", "depth": -20},
            {"source1": 5, "source2": 4, "dest": 12, "depth": 63},
            {"source": "env 3", "destination": "filter resonance", "depth": -64},
            {"source1": "velocity", "dest": "amp envelope decay"},
        ],
    },
    "preset-pluck-macros": {
        "preset": "pluck",
        "macros": {
            "5": {"targets": [{"dest": "filter_frequency", "start": 10, "end": 120, "depth": 100}], "position": 40},
            "8": {
                "targets": [
                    {"dest": 46, "start": 0, "end": 80},
                    {"dest": "distortion_level"},
                    {"dest": "chorus_rate", "depth": 64},
                    {"dest": "mod_matrix_1_depth", "start": 20},
                ]
            },
            "1": {"targets": []},
        },
    },
    "full-custom": {
        "name": "SixteenCharName!",
        "params": {
            "polyphony_mode": 1,
            "osc1_wave": 20,
            "osc2_wave": 3,
            "osc2_semitones": 71,
            "osc1_level": 127,
            "osc2_level": 200,
            "noise_level": -5,
            "filter_type": 4,
            "filter_frequency": 0,
            "filter_resonance": 100,
            "env1_attack": 0,
            "env1_release": 127,
            "lfo2_waveform": 6,
            "lfo2_rate_sync": 4,
            "chorus_level": 60,
            "distortion_level": 80,
            "distortion_type": 3,
            "eq_bass_level": 90,
            "mod1_depth": 90,
        },
        "mod_matrix": [
            {"source1": "keyboard", "dest": "filter frequency", "depth": 40},
            {"source1": "env filter", "dest": "osc 2 v-sync", "depth": 12},
            {"source1": "LFO 2+", "source2": "velocity", "dest": "noise level", "depth": -30},
        ],
        "macros": {
            str(k): {"targets": [{"dest": 20 + k, "start": k, "end": 127 - k, "depth": 64 + k}]} for k in range(1, 9)
        },
    },
    "unicode-name": {"name": "Café Pad ñ"},
}


def dump(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")


def main() -> None:
    data_dir = ROOT / "webapp" / "data"
    dump(data_dir / "song.schema.json", get_song_json_schema())

    reference = {"": get_parameter_reference("")}
    for section in SECTIONS:
        reference[section] = get_parameter_reference(section)
    dump(data_dir / "parameter-reference.json", reference)

    vectors = ROOT / "webapp" / "tests" / "vectors"
    patches = vectors / "patches"
    patches.mkdir(parents=True, exist_ok=True)
    for case, config in PATCH_CASES.items():
        dump(patches / f"{case}.json", {"config": config, "bytes": list(build_patch_bytes(config))})

    count = 0
    for song_path in sorted(vectors.glob("*.song.json")):
        song = parse_song(json.loads(song_path.read_text()))
        stem = song_path.name[: -len(".song.json")]
        ncs_bytes = song_to_ncs(song)
        (vectors / f"{stem}.ncs").write_bytes(ncs_bytes)
        readback = _song_data_to_dict(ncs_to_song(parse_ncs_from_bytes(ncs_bytes)))
        dump(vectors / f"{stem}.readback.json", readback)
        count += 1
    print(f"wrote {count} song vector(s) to {vectors.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
