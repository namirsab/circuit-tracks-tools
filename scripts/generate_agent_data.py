#!/usr/bin/env python3
"""Generate the data files Web Tracks serves to MCP clients.

The Python library is the single source of truth for the song format and the
parameter reference; the webapp ships generated copies so its agent tools
answer exactly like the hardware MCP server. Re-run after changing either.

Outputs:
  webapp/data/song.schema.json          get_song_json_schema()
  webapp/data/parameter-reference.json  every get_parameter_reference section
  webapp/tests/vectors/<name>.ncs       song_to_ncs() of each <name>.song.json
                                        (golden vectors for the JS song compiler)
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from circuit_mcp.server import get_parameter_reference  # noqa: E402
from circuit_tracks.song import parse_song, song_to_ncs  # noqa: E402
from circuit_tracks.song_schema import get_song_json_schema  # noqa: E402

SECTIONS = [
    "synth",
    "patch",
    "drums",
    "project",
    "lookup_tables",
    "mod_matrix",
    "macros",
    "song_format",
    "best_practices",
]


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
    count = 0
    for song_path in sorted(vectors.glob("*.song.json")):
        song = parse_song(json.loads(song_path.read_text()))
        out = song_path.with_name(song_path.name[: -len(".song.json")] + ".ncs")
        out.write_bytes(song_to_ncs(song))
        count += 1
    print(f"wrote {count} golden vector(s) to {vectors.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
