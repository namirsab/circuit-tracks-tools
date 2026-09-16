"""Precise, step-by-step song building by voice: no model in between.

``new_song`` starts from an empty 32-step pattern; ``write_notes`` and
``write_drums`` put exactly the spoken notes and hits on a track;
``clear_track`` empties one. Every edit updates the ledger's song JSON (the
source of truth that ``save_to_device`` exports) and calls ``set_track`` so
the running sequencer plays the change at once. Note names follow the
device's labelling: C3 is MIDI 60.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from .bridge import Bridge
from .ledger import Change
from .tool_cards import ArgumentError, _int_in_range

NOTE_OFFSETS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11, "H": 11}
_NOTE_RE = re.compile(r"^([A-Ha-h])(#|b|is|es|s)?(-?\d)?$")
DEFAULT_LENGTH = 32
SYNTH_TRACKS = ("synth1", "synth2", "midi1", "midi2")
DRUM_TRACKS = ("drum1", "drum2", "drum3", "drum4")


def note_number(token: str | int) -> int:
    """'C3' -> 60, 'F#4' -> 78, 'Bb2' -> 58, 'H3' (German) -> 71, '60' -> 60."""
    if isinstance(token, int) or str(token).strip().lstrip("-").isdigit():
        value = int(token)
        if not 0 <= value <= 127:
            raise ArgumentError(f"Die Note {value} liegt außerhalb von 0 bis 127.")
        return value
    m = _NOTE_RE.match(str(token).strip())
    if not m:
        raise ArgumentError(f"'{token}' ist keine Note. Beispiele: C3, F#4, Bb2 oder eine Zahl 0-127.")
    letter, accidental, octave = m.group(1).upper(), m.group(2), m.group(3)
    value = NOTE_OFFSETS[letter]
    if accidental in ("#", "is"):
        value += 1
    elif accidental in ("b", "es", "s"):
        value -= 1
    octave_n = int(octave) if octave is not None else 3
    value += (octave_n + 2) * 12  # device labelling: C3 = 60
    if not 0 <= value <= 127:
        raise ArgumentError(f"Die Note {token} liegt außerhalb des MIDI-Bereichs.")
    return value


def parse_notes(spoken: Any) -> list[int]:
    if isinstance(spoken, list):
        tokens = [str(t) for t in spoken]
    else:
        tokens = [t for t in re.split(r"[\s,;]+", str(spoken)) if t]
    if not tokens:
        raise ArgumentError("Ich brauche mindestens eine Note.")
    return [note_number(t) for t in tokens]


def empty_song(name: str, bpm: float) -> dict[str, Any]:
    return {
        "name": name[:16],
        "bpm": float(bpm),
        "scale": {"root": "C", "type": "chromatic"},
        "sounds": {"synth1": {"preset": "lead"}, "synth2": {"preset": "bass"}},
        "patterns": {"pattern1": {"length": DEFAULT_LENGTH, "tracks": {}}},
        "song": ["pattern1"],
    }


def register_edit_tools(bridge: Bridge) -> None:
    ledger, backend = bridge.ledger, bridge.backend

    def song() -> dict[str, Any]:
        if ledger.song_json is None:
            raise ArgumentError("Es ist kein Song geladen. Sag 'neuer Song' oder lade einen.")
        return ledger.song_json

    def pattern(name: str | None) -> tuple[str, dict[str, Any]]:
        data = song()
        patterns = data.setdefault("patterns", {})
        if not name:
            name = ledger.first_pattern() or next(iter(patterns), "pattern1")
        key = str(name).strip().lower().replace(" ", "")
        if key not in patterns:
            # "Pattern 2" -> pattern2; create it with the song's length so all patterns match
            if re.fullmatch(r"pattern\d+", key) or len(patterns) == 0:
                length = next((p.get("length", DEFAULT_LENGTH) for p in patterns.values()), DEFAULT_LENGTH)
                patterns[key] = {"length": length, "tracks": {}}
                if key not in data.setdefault("song", []):
                    data["song"].append(key)
                ledger.patterns = list(patterns)
                ledger.song_order = list(data["song"])
            else:
                raise ArgumentError(f"Das Pattern {name} gibt es nicht. Vorhanden: {', '.join(patterns)}.")
        return key, patterns[key]

    async def push(
        pattern_name: str, track: str, steps: dict[str, Any], previous: dict[str, Any] | None = None
    ) -> str | None:
        result = await backend.call(
            "set_track", {"pattern_name": pattern_name, "track": track, "steps": steps, "clear_existing": True}
        )
        if result.is_error:
            return f"Das Schreiben ging nicht: {result.text}"
        ledger.unsaved = True
        if previous is not None:
            ledger.history.append(
                Change(
                    "restore_track",
                    {"pattern": pattern_name, "track": track},
                    {"pattern": pattern_name, "track": track, "steps": previous},
                    f"{track} in {pattern_name} wie vorher",
                )
            )
        return None

    async def restore_track(args: dict[str, Any]) -> str:
        name, pat = pattern(args["pattern"])
        track, steps = args["track"], dict(args.get("steps") or {})
        tracks = pat.setdefault("tracks", {})
        if steps:
            tracks[track] = {"steps": steps}
        else:
            tracks.pop(track, None)
        error = await push(name, track, steps)
        return error or f"{track} in {name} ist wieder wie vorher."

    async def new_song(args: dict[str, Any]) -> str:
        name = str(args.get("name") or "Neuer Song").strip()
        bpm = float(args.get("bpm") or 120)
        if not 40 <= bpm <= 240:
            raise ArgumentError("Das Tempo muss zwischen 40 und 240 liegen.")
        if ledger.running and ledger.source == "internal":
            await bridge.dispatch("stop_sequencer", {})
        data = empty_song(name, bpm)
        text = await bridge.load_song_dict(copy.deepcopy(data), name)  # type: ignore[attr-defined]
        if ledger.song_json is None:
            return text
        ledger.song_json = data
        return f"Neuer Song {name}, {int(bpm)} BPM, ein leeres Pattern mit 32 Schritten. Sag mir, was rein soll."

    async def write_notes(args: dict[str, Any]) -> str:
        track = str(args.get("track", "synth1")).lower()
        if track not in SYNTH_TRACKS:
            raise ArgumentError("Noten gehen auf synth1, synth2, midi1 oder midi2. Für Drums nimm write_drums.")
        notes = parse_notes(args.get("notes"))
        spacing = _int_in_range({"s": args.get("spacing", 4)}, "s", 1, 32, "Der Abstand")
        gate = float(args.get("gate") or spacing)
        gate = max(0.25, min(16.0, gate))
        velocity = _int_in_range({"v": args.get("velocity", 100)}, "v", 1, 127, "Die Anschlagstärke")
        name, pat = pattern(args.get("pattern"))
        length = int(pat.get("length", DEFAULT_LENGTH))
        tracks = pat.setdefault("tracks", {})
        before = copy.deepcopy((tracks.get(track) or {}).get("steps") or {})
        steps: dict[str, Any] = dict(before)
        if args.get("replace"):
            steps = {}
        if args.get("start_step") is not None:
            start = _int_in_range(args, "start_step", 1, length, "Der Startschritt") - 1
        else:
            used = [int(k) for k in steps] if steps else []
            start = 0 if not used else max(used) + spacing
        placed = 0
        for i, note in enumerate(notes):
            idx = start + i * spacing
            if idx >= length:
                break
            steps[str(idx)] = {"note": note, "velocity": velocity, "gate": gate}
            placed += 1
        tracks[track] = {"steps": steps}
        error = await push(name, track, steps, previous=before)
        if error:
            return error
        skipped = f" {len(notes) - placed} Noten passten nicht mehr rein." if placed < len(notes) else ""
        first = start + 1
        return f"{placed} Noten auf {track} in {name} ab Schritt {first}, alle {spacing} Schritte.{skipped}"

    async def write_drums(args: dict[str, Any]) -> str:
        track = str(args.get("track", "drum1")).lower()
        if track not in DRUM_TRACKS:
            raise ArgumentError("Drum-Schläge gehen auf drum1 bis drum4.")
        raw = args.get("steps")
        if isinstance(raw, str):
            raw = [t for t in re.split(r"[\s,;]+", raw) if t]
        if not raw:
            raise ArgumentError("Ich brauche mindestens einen Schritt, zum Beispiel 1, 5, 9, 13.")
        velocity = _int_in_range({"v": args.get("velocity", 100)}, "v", 1, 127, "Die Anschlagstärke")
        name, pat = pattern(args.get("pattern"))
        length = int(pat.get("length", DEFAULT_LENGTH))
        tracks = pat.setdefault("tracks", {})
        before = copy.deepcopy((tracks.get(track) or {}).get("steps") or {})
        steps: dict[str, Any] = {} if args.get("replace") else dict(before)
        for item in raw:
            n = _int_in_range({"n": item}, "n", 1, length, "Der Schritt")
            steps[str(n - 1)] = {"velocity": velocity}
        tracks[track] = {"steps": steps}
        error = await push(name, track, steps, previous=before)
        if error:
            return error
        spoken = ", ".join(str(int(k) + 1) for k in sorted(steps, key=int))
        return f"{track} in {name} schlägt auf {spoken}."

    async def clear_track(args: dict[str, Any]) -> str:
        track = str(args.get("track", "")).lower()
        if track not in SYNTH_TRACKS + DRUM_TRACKS:
            raise ArgumentError("Welche Spur? synth1, synth2, drum1 bis drum4, midi1 oder midi2.")
        name, pat = pattern(args.get("pattern"))
        before = copy.deepcopy((pat.get("tracks") or {}).get(track, {}).get("steps") or {})
        pat.setdefault("tracks", {}).pop(track, None)
        error = await push(name, track, {}, previous=before)
        if error:
            return error
        return f"{track} in {name} ist leer."

    bridge.register_internal("restore_track", restore_track)
    bridge.register_virtual("new_song", new_song)
    bridge.register_virtual("write_notes", write_notes)
    bridge.register_virtual("write_drums", write_drums)
    bridge.register_virtual("clear_track", clear_track)
