"""Saved songs on disk, and the song tools the voice agent exposes.

A song library is a folder of song JSON files (the format ``load_song`` takes).
Names are matched loosely ("nachtfahrt" finds ``Nachtfahrt.json``) so they can
be spoken. ``register_song_tools`` wires the virtual tools ``list_saved_songs``,
``load_saved_song`` and ``save_to_device`` into a bridge: loading validates the
file with ``SongSchema`` before it reaches the sequencer, saving needs the model
to pass ``confirmed=true`` after asking the user, because it overwrites a slot.
"""

from __future__ import annotations

import asyncio
import copy
import difflib
import json
import unicodedata
from pathlib import Path
from typing import Any

from circuit_tracks.song_schema import SongSchema

from .bridge import Bridge
from .generate_tools import default_macros
from .tool_cards import _int_in_range


def _fold(text: str) -> str:
    text = text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if ch.isalnum())


class SongLibrary:
    def __init__(self, folder: str | Path | None):
        self.folder = Path(folder).expanduser() if folder else None

    def files(self) -> dict[str, Path]:
        if self.folder is None or not self.folder.is_dir():
            return {}
        return {p.stem: p for p in sorted(self.folder.glob("*.json"))}

    def names(self) -> list[str]:
        return list(self.files())

    def find(self, spoken: str) -> Path | None:
        files = self.files()
        by_fold = {_fold(n): p for n, p in files.items()}
        key = _fold(spoken)
        if key in by_fold:
            return by_fold[key]
        match = difflib.get_close_matches(key, list(by_fold), n=1, cutoff=0.6)
        return by_fold[match[0]] if match else None

    def closest(self, spoken: str, n: int = 3) -> list[str]:
        names = self.names()
        folded = {_fold(x): x for x in names}
        hits = difflib.get_close_matches(_fold(spoken), list(folded), n=n, cutoff=0.0)
        return [folded[h] for h in hits] or names[:n]

    @staticmethod
    def load(path: Path) -> dict[str, Any]:
        """Read and validate a song file; returns the plain dict for ``load_song``."""
        data = json.loads(Path(path).read_text())
        SongSchema.model_validate(copy.deepcopy(data))  # validation mutates its input
        return data


SETTLE_S = 0.3  # pause between stopping playback or sweeps and writing a project
DRUM_FIELDS = ("level", "pitch", "decay", "distortion", "eq", "pan")


async def capture_live_sounds(bridge: Bridge) -> list[str]:
    """Fold the synths' current patches and live drum/mixer values into the song JSON."""
    ledger, backend = bridge.ledger, bridge.backend
    song = ledger.song_json
    if song is None:
        return []
    notes: list[str] = []
    sounds = song.setdefault("sounds", {}) or {}
    song["sounds"] = sounds
    for synth in (1, 2):
        dump = await backend.call("get_synth_patch", {"synth": synth})
        if dump.is_error or not isinstance(dump.data, dict) or not dump.data.get("params"):
            notes.append(f"synth{synth}: Patch nicht lesbar")
            continue
        # Without macro routing the knobs on the device do nothing (seen on 2026-09-17). The dump carries
        # the routing of whatever is on the synth; an older server sends parameter values only, then it
        # comes from what we know: the designed patch, else the song's own sound.
        design = ledger.designed_patch.get(synth)
        if dump.data.get("macros"):
            routing = {"macros": dump.data["macros"]}  # the mod matrix is part of params
        elif design is not None:
            routing = {k: design[k] for k in ("mod_matrix", "macros") if design.get(k)}
        elif ledger.selected_patch.get(synth):
            routing = {}  # a patch from the device whose routing we could not read
        else:
            previous = sounds.get(f"synth{synth}") or {}
            routing = {k: previous[k] for k in ("preset", "mod_matrix", "macros") if previous.get(k)}
        if not routing.get("macros") and not routing.get("preset"):
            routing["macros"] = default_macros(dump.data["params"])
        sounds[f"synth{synth}"] = {
            **routing,
            "name": str(dump.data.get("name") or f"Synth {synth}")[:16],
            "params": dump.data["params"],
        }
        notes.append(f"synth{synth}: {sounds[f'synth{synth}']['name']}")
    for drum in (1, 2, 3, 4):
        live = ledger.known.get(f"drum{drum}", {})
        if live:
            entry = dict(sounds.get(f"drum{drum}") or {})
            entry.update({k: int(v) for k, v in live.items() if k in DRUM_FIELDS})
            sounds[f"drum{drum}"] = entry
    project = ledger.known.get("project", {})
    if any(k in project for k in ("synth1_level", "synth2_level", "synth1_pan", "synth2_pan")):
        mixer = song.setdefault("mixer", {}) or {}
        song["mixer"] = mixer
        for synth in ("synth1", "synth2"):
            entry = dict(mixer.get(synth) or {})
            if f"{synth}_level" in project:
                entry["level"] = int(project[f"{synth}_level"])
            if f"{synth}_pan" in project:
                entry["pan"] = int(project[f"{synth}_pan"])
            if entry:
                mixer[synth] = entry
    return notes


def register_song_tools(bridge: Bridge, library: SongLibrary) -> None:
    ledger, backend = bridge.ledger, bridge.backend

    async def list_saved_songs(args: dict[str, Any]) -> str:
        names = library.names()
        if not names:
            return "Es gibt keine gespeicherten Songs im Song-Ordner."
        return "Gespeicherte Songs: " + ", ".join(names) + "."

    async def load_song_dict(data: dict[str, Any], label: str) -> str:
        result = await backend.call("load_song", {"song": copy.deepcopy(data)})  # the server mutates its input
        ledger.last_load_ok = not result.is_error
        if result.is_error:
            return f"Der Song {label} konnte nicht geladen werden: {result.text[:300]}"
        info = result.data if isinstance(result.data, dict) else {}
        name = info.get("name") or data.get("name") or label
        ledger.song_loaded(
            name, float(info.get("bpm") or data.get("bpm") or 120), info.get("patterns", []), info.get("song_order", [])
        )
        ledger.source = "internal"
        ledger.song_json = data
        patterns = ", ".join(ledger.patterns)
        return f"Song {name} geladen, {int(ledger.bpm)} BPM, Patterns: {patterns}."

    async def load_saved_song(args: dict[str, Any]) -> str:
        spoken = str(args.get("name", "")).strip()
        path = library.find(spoken) if spoken else None
        if path is None:
            hints = ", ".join(library.closest(spoken)) if library.names() else "keine"
            return f"Ich habe keinen Song namens {spoken or '(leer)'} gefunden. Ähnliche Namen: {hints}."
        try:
            data = library.load(path)
        except Exception as e:  # noqa: BLE001 - a bad file must not kill the session
            return f"Die Datei {path.name} ist kein gültiger Song: {str(e)[:200]}"
        return await load_song_dict(data, path.stem)

    async def save_to_device(args: dict[str, Any]) -> Any:
        if ledger.song_name is None:
            return "Es ist kein Song geladen, den ich speichern könnte."
        if args.get("slot") is None:
            current = ledger.device_project or ledger.slot
            if current is None:
                return "In welchen Slot von 1 bis 64 soll ich speichern?"
            args = {**args, "slot": current}
        slot = _int_in_range(args, "slot", 1, 64, "Der Slot")
        name = (str(args.get("name") or ledger.song_name)).strip()[:16]
        if not args.get("confirmed"):
            return {
                "needs_confirmation": f"Slot {slot} wird überschrieben, Song '{name}'. Speichern?",
                "hint": "Ask the user; on 'ja' call save_to_device again with confirmed=true.",
            }
        # The device hangs when project SysEx blocks interleave with clock and notes
        # (seen on 2026-09-16): stop the internal sequencer before writing.
        stopped = False
        if ledger.running and ledger.source == "internal":
            await bridge.dispatch("stop_sequencer", {})
            stopped = True
        # A running sweep keeps sending controller messages between the project's SysEx blocks
        # (seen on 2026-09-17: a project saved during a ping-pong morph crashed the device on load).
        morphs = await backend.call("stop_morph", {})
        swept = not morphs.is_error and "Stopped" in str(morphs.data if morphs.data is not None else morphs.text)
        if stopped or swept:
            await asyncio.sleep(SETTLE_S)  # let the last notes and controller messages drain
        # Capture what the user hears: the synths' current patches and the drum/mixer values set live,
        # otherwise the export writes the presets the song started with (seen on 2026-09-16).
        if ledger.song_json is not None:
            await capture_live_sounds(bridge)
        # The export writes the server's last loaded song; reload ours so step edits are included.
        if ledger.song_json is not None:
            if ledger.song_bpm:  # tempo changes made by voice belong in the project (40-240 in the file)
                ledger.song_json["bpm"] = max(40, min(240, int(round(ledger.song_bpm))))
            reload = await backend.call("load_song", {"song": copy.deepcopy(ledger.song_json)})
            if reload.is_error:
                return f"Speichern fehlgeschlagen, der Song ist nicht mehr gültig: {reload.text[:200]}"
        result = await backend.call("export_song_to_project", {"slot": slot - 1, "name": name})
        if result.is_error:
            return f"Speichern fehlgeschlagen: {result.text}"
        ledger.saved(slot)
        ledger.device_dirty = False
        tempo = ledger.song_bpm or ledger.bpm
        if tempo:
            ledger.device_bpm[slot] = ledger.saved_bpm[slot] = float(tempo)
        tail = " Der Sequenzer ist dafür gestoppt; sag Start, oder ich spiele es vom Gerät ab." if stopped else ""
        if swept:
            tail = " Die laufende Bewegung habe ich dafür angehalten." + tail
        return f"Gespeichert in Slot {slot} als '{name}'.{tail}"

    bridge.register_virtual("list_saved_songs", list_saved_songs)
    bridge.register_virtual("load_saved_song", load_saved_song)
    bridge.register_virtual("save_to_device", save_to_device)
    bridge.load_song_dict = load_song_dict  # type: ignore[attr-defined]  # used by --song at startup
