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
RESTS = {"", "-", "_", ".", "leer", "pause", "rest", "none", "null", "x"}
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


def parse_notes(spoken: Any) -> list[list[int]]:
    """One entry per step; each entry is the notes sounding together (one note, or a chord).

    Accepts a list whose items are a note ("E3", 64) or a list of notes (a chord), or
    text where steps are separated by spaces and chord notes joined with "+": "C3+E3+G3 D3".
    A rest ("", "-", "leer", "pause", null, or an empty list) is an empty entry: it keeps its place.
    """
    if isinstance(spoken, list):
        items: list[Any] = spoken
    else:
        items = [t for t in re.split(r"[\s,;]+", str(spoken or "")) if t]
    steps: list[list[int]] = []
    for item in items:
        if isinstance(item, (list, tuple)):
            parts: list[Any] = list(item)
        else:
            parts = [t for t in str(item).split("+") if t]
        if item is None or (len(parts) <= 1 and str(parts[0] if parts else "").strip().lower() in RESTS):
            steps.append([])
            continue
        chord = [note_number(str(t)) for t in parts]
        if len(chord) > 6:
            raise ArgumentError("Ein Akkord hat höchstens sechs Noten.")
        steps.append(chord)
    if not any(steps):
        raise ArgumentError("Ich brauche mindestens eine Note.")
    return steps


def step_notes(chord: list[int]) -> dict[str, Any]:
    return {"note": chord[0]} if len(chord) == 1 else {"notes": list(chord)}


def _velocities(raw: Any, default: int) -> list[int]:
    if not raw:
        return [default]
    if isinstance(raw, str):
        raw = [t for t in re.split(r"[\s,;]+", raw) if t]
    out = [_int_in_range({"v": v}, "v", 1, 127, "Die Anschlagstärke") for v in raw]
    return out or [default]


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
        name_given = bool(name)
        if not name:
            name = ledger.edit_pattern() or next(iter(patterns), "pattern1")
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
        if name_given:
            ledger.work_pattern = key
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
        if not ledger.last_load_ok:
            return text
        ledger.song_json = data
        return f"Neuer Song {name}, {int(bpm)} BPM, ein leeres Pattern mit 32 Schritten. Sag mir, was rein soll."

    async def add_pattern(args: dict[str, Any]) -> str:
        data = song()
        patterns = data.setdefault("patterns", {})
        key = re.sub(r"[^a-z0-9_]", "", str(args.get("name") or "").strip().lower())
        if not key:
            n = len(patterns) + 1
            while f"pattern{n}" in patterns:
                n += 1
            key = f"pattern{n}"
        if key in patterns:
            raise ArgumentError(f"Das Pattern {key} gibt es schon. Vorhanden: {', '.join(patterns)}.")
        length = next((p.get("length", DEFAULT_LENGTH) for p in patterns.values()), DEFAULT_LENGTH)
        tracks: dict[str, Any] = {}
        source = str(args.get("copy_from") or "").strip().lower().replace(" ", "")
        if source:
            if source not in patterns:
                raise ArgumentError(f"Das Pattern {source} gibt es nicht. Vorhanden: {', '.join(patterns)}.")
            tracks = copy.deepcopy(patterns[source].get("tracks") or {})
        result = await backend.call("set_pattern", {"name": key, "tracks": copy.deepcopy(tracks), "length": length})
        if result.is_error:
            return f"Das Pattern konnte ich nicht anlegen: {result.text[:200]}"
        patterns[key] = {"length": length, "tracks": tracks}
        data.setdefault("song", []).append(key)
        ledger.patterns, ledger.song_order = list(patterns), list(data["song"])
        ledger.work_pattern = key
        ledger.unsaved = True
        tail = ""
        if ledger.running and ledger.source == "internal":
            queued = await backend.call("queue_patterns", {"patterns": [key]})
            tail = "" if queued.is_error else " Es läuft, sobald das aktuelle Pattern zu Ende ist."
        what = f"als Kopie von {source}" if source else "leer"
        return f"Pattern {key} ist angelegt, {what}, {length} Schritte. Änderungen gehen jetzt dorthin.{tail}"

    async def write_notes(args: dict[str, Any]) -> str:
        track = str(args.get("track", "synth1")).lower()
        if track not in SYNTH_TRACKS:
            raise ArgumentError("Noten gehen auf synth1, synth2, midi1 oder midi2. Für Drums nimm write_drums.")
        notes = parse_notes(args.get("notes"))
        spacing = _int_in_range({"s": args.get("spacing", 4)}, "s", 1, 32, "Der Abstand")
        gate = float(args.get("gate") or spacing)
        gate = max(0.25, min(16.0, gate))
        velocity = _int_in_range({"v": args.get("velocity", 100)}, "v", 1, 127, "Die Anschlagstärke")
        velocities = _velocities(args.get("velocities"), velocity)
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
        placed = chords = rests = 0
        for i, note in enumerate(notes):
            idx = start + i * spacing
            if idx >= length:
                break
            if not note:  # a rest keeps its place and clears what was there
                steps.pop(str(idx), None)
                rests += 1
                continue
            steps[str(idx)] = {**step_notes(note), "velocity": velocities[i % len(velocities)], "gate": gate}
            placed += 1
            chords += len(note) > 1
        tracks[track] = {"steps": steps}
        error = await push(name, track, steps, previous=before)
        if error:
            return error
        left = len(notes) - placed - rests
        skipped = f" {left} Einträge passten nicht mehr rein." if left > 0 else ""
        first = start + 1
        what = f"{placed} Noten"
        if chords:
            what = f"{chords} Akkorde" if chords == placed else f"{placed} Schritte, davon {chords} Akkorde,"
        return f"{what} auf {track} in {name} ab Schritt {first}, alle {spacing} Schritte.{skipped}"

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
        velocities = _velocities(args.get("velocities"), velocity)
        name, pat = pattern(args.get("pattern"))
        length = int(pat.get("length", DEFAULT_LENGTH))
        tracks = pat.setdefault("tracks", {})
        before = copy.deepcopy((tracks.get(track) or {}).get("steps") or {})
        steps: dict[str, Any] = {} if args.get("replace") else dict(before)
        for i, item in enumerate(raw):
            n = _int_in_range({"n": item}, "n", 1, length, "Der Schritt")
            steps[str(n - 1)] = {"velocity": velocities[i % len(velocities)]}
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

    async def set_step(args: dict[str, Any]) -> str:
        track = str(args.get("track", "")).lower()
        if track not in SYNTH_TRACKS + DRUM_TRACKS:
            raise ArgumentError("Welche Spur? synth1, synth2, drum1 bis drum4, midi1 oder midi2.")
        name, pat = pattern(args.get("pattern"))
        length = int(pat.get("length", DEFAULT_LENGTH))
        n = _int_in_range(args, "step", 1, length, "Der Schritt")
        tracks = pat.setdefault("tracks", {})
        before = copy.deepcopy((tracks.get(track) or {}).get("steps") or {})
        steps = dict(before)
        step = dict(steps.get(str(n - 1)) or {})
        if not step:
            return f"Auf Schritt {n} von {track} liegt nichts. Sag mir erst, was da hin soll."
        changes = []
        if args.get("velocity") is not None:
            step["velocity"] = _int_in_range(args, "velocity", 1, 127, "Die Anschlagstärke")
            changes.append(f"Anschlag {step['velocity']}")
        wanted = args.get("notes") if args.get("notes") is not None else args.get("note")
        if wanted is not None and track in SYNTH_TRACKS:
            chord = parse_notes([wanted] if isinstance(wanted, list) else str(wanted).replace(" ", "+"))[0]
            step.pop("note", None)
            step.pop("notes", None)
            step.update(step_notes(chord))
            changes.append("Akkord" if len(chord) > 1 else f"Note {wanted}")
        if args.get("gate") is not None and track in SYNTH_TRACKS:
            step["gate"] = max(0.25, min(16.0, float(args["gate"])))
            changes.append(f"Länge {step['gate']}")
        if not changes:
            raise ArgumentError("Was soll sich an dem Schritt ändern: Anschlag, Note oder Länge?")
        steps[str(n - 1)] = step
        tracks[track] = {"steps": steps}
        error = await push(name, track, steps, previous=before)
        return error or f"Schritt {n} auf {track}: {', '.join(changes)}."

    async def set_drum_sample(args: dict[str, Any]) -> str:
        drum = _int_in_range(args, "drum", 1, 4, "Die Drum-Nummer")
        data = song()
        number = args.get("number")
        label = ""
        if number is None:
            query = str(args.get("name") or "").strip().lower()
            if not query:
                raise ArgumentError("Sag mir die Sample-Nummer (1 bis 64) oder ein Wort aus dem Namen.")
            listing = await backend.call("list_drum_samples", {})
            samples = (listing.data or {}).get("samples", {}) if isinstance(listing.data, dict) else {}
            hits = [(int(k), v) for k, v in samples.items() if query in str(v).lower()]
            if not hits:
                return f"Kein Sample-Name enthält '{query}'. Frag mit list_drum_samples nach den Namen."
            number, label = hits[0][0] + 1, str(hits[0][1])
        number = _int_in_range({"n": number}, "n", 1, 64, "Die Sample-Nummer")
        sounds = data.setdefault("sounds", {})
        entry = dict(sounds.get(f"drum{drum}") or {})
        entry["sample"] = number - 1
        sounds[f"drum{drum}"] = entry
        ledger.unsaved = True
        # The CC route (patch_select) does not switch samples on this firmware; the project export does.
        shown = f"{number} ({label})" if label else str(number)
        return (
            f"Drum {drum} bekommt Sample {shown}. Das Gerät wechselt Samples nicht live über MIDI; "
            "hörbar wird es nach dem Speichern und Abspielen vom Gerät."
        )

    bridge.register_virtual("set_step", set_step)
    bridge.register_virtual("set_drum_sample", set_drum_sample)
    bridge.register_internal("restore_track", restore_track)
    bridge.register_virtual("new_song", new_song)
    bridge.register_virtual("add_pattern", add_pattern)
    bridge.register_virtual("write_notes", write_notes)
    bridge.register_virtual("write_drums", write_drums)
    bridge.register_virtual("clear_track", clear_track)
