"""Saved songs on disk, and the song tools the voice agent exposes.

A song library is a folder of song JSON files (the format ``load_song`` takes).
Names are matched loosely ("nachtfahrt" finds ``Nachtfahrt.json``) so they can
be spoken. ``register_song_tools`` wires the virtual tools ``list_saved_songs``,
``load_saved_song`` and ``save_to_device`` into a bridge: loading validates the
file with ``SongSchema`` before it reaches the sequencer, saving needs the model
to pass ``confirmed=true`` after asking the user, because it overwrites a slot.
"""

from __future__ import annotations

import copy
import difflib
import json
import unicodedata
from pathlib import Path
from typing import Any

from circuit_tracks.song_schema import SongSchema

from .bridge import Bridge
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


def register_song_tools(bridge: Bridge, library: SongLibrary) -> None:
    ledger, backend = bridge.ledger, bridge.backend

    async def list_saved_songs(args: dict[str, Any]) -> str:
        names = library.names()
        if not names:
            return "Es gibt keine gespeicherten Songs im Song-Ordner."
        return "Gespeicherte Songs: " + ", ".join(names) + "."

    async def load_song_dict(data: dict[str, Any], label: str) -> str:
        result = await backend.call("load_song", {"song": copy.deepcopy(data)})  # the server mutates its input
        if result.is_error:
            return f"Der Song {label} konnte nicht geladen werden: {result.text}"
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
        result = await backend.call("export_song_to_project", {"slot": slot - 1, "name": name})
        if result.is_error:
            return f"Speichern fehlgeschlagen: {result.text}"
        ledger.saved(slot)
        tail = " Der Sequenzer ist dafür gestoppt; sag Start, oder ich spiele es vom Gerät ab." if stopped else ""
        return f"Gespeichert in Slot {slot} als '{name}'.{tail}"

    bridge.register_virtual("list_saved_songs", list_saved_songs)
    bridge.register_virtual("load_saved_song", load_saved_song)
    bridge.register_virtual("save_to_device", save_to_device)
    bridge.load_song_dict = load_song_dict  # type: ignore[attr-defined]  # used by --song at startup
