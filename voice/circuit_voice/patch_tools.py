"""Patch names on the device, and selecting patches by name.

The Circuit Tracks has no "list patches" command: a patch's name lives inside
the patch. ``scan_patches`` therefore selects each of the 64 slots on one
synth, reads the patch dump and keeps the names in a small JSON cache, so
later sessions answer "Welche Patches gibt es?" instantly and "Nimm den
Bassix" resolves to a slot. Scanning changes the selected patch while it
runs, so it only happens while nothing plays.
"""

from __future__ import annotations

import asyncio
import difflib
import json
from pathlib import Path
from typing import Any

from .bridge import Bridge
from .tool_cards import ArgumentError, _int_in_range

DEFAULT_CACHE = Path.home() / ".circuit-voice" / "patches.json"
SETTLE_S = 0.03  # the reference asks for 10 ms between program change and dump request


def _fold(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


class PatchCatalog:
    def __init__(self, path: Path | None = DEFAULT_CACHE):
        self.path = path
        self.names: dict[int, str] = {}  # 1-based slot -> name
        if path is not None and path.exists():
            try:
                data = json.loads(path.read_text())
                self.names = {int(k): str(v) for k, v in data.get("patches", {}).items()}
            except (ValueError, OSError):
                self.names = {}

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"patches": {str(k): v for k, v in sorted(self.names.items())}}, indent=1))

    def find(self, spoken: str) -> tuple[int, str] | None:
        if not self.names:
            return None
        key = _fold(spoken)
        folded = {_fold(v): k for k, v in self.names.items()}
        if key in folded:
            return folded[key], self.names[folded[key]]
        hits = difflib.get_close_matches(key, list(folded), n=1, cutoff=0.6)
        if hits:
            return folded[hits[0]], self.names[folded[hits[0]]]
        contains = [k for f, k in folded.items() if key and key in f]
        if len(contains) == 1:
            return contains[0], self.names[contains[0]]
        return None

    def listing(self) -> str:
        return ", ".join(f"{k} {v}" for k, v in sorted(self.names.items()))


def register_patch_tools(bridge: Bridge, catalog: PatchCatalog) -> None:
    ledger, backend = bridge.ledger, bridge.backend

    async def scan_patches(args: dict[str, Any]) -> str:
        if ledger.running:
            return "Zum Einlesen der Patch-Namen muss alles gestoppt sein. Sag erst Stopp."
        synth = _int_in_range({"s": args.get("synth", 2)}, "s", 1, 2, "Der Synth")
        found: dict[int, str] = {}
        failed = 0
        for slot in range(64):
            sel = await backend.call("select_patch", {"synth": synth, "patch_number": slot})
            if sel.is_error:
                return f"Einlesen abgebrochen bei Patch {slot + 1}: {sel.text}"
            await asyncio.sleep(SETTLE_S)
            dump = await backend.call("get_synth_patch", {"synth": synth})
            name = dump.data.get("name") if isinstance(dump.data, dict) else None
            if dump.is_error or not name:
                failed += 1
                if failed >= 3 and not found:
                    return f"Das Gerät antwortet nicht auf Patch-Anfragen: {dump.text[:120]}"
                continue
            found[slot + 1] = str(name).strip()
        catalog.names.update(found)
        catalog.save()
        previous = ledger.selected_patch.get(synth)
        if previous:
            await backend.call("select_patch", {"synth": synth, "patch_number": previous - 1})
            back = f"Synth {synth} steht wieder auf Patch {previous}."
        else:
            await backend.call("select_patch", {"synth": synth, "patch_number": 0})
            back = f"Synth {synth} steht jetzt auf Patch 1."
        return f"{len(found)} Patch-Namen eingelesen. {back}"

    async def list_patches(args: dict[str, Any]) -> str:
        if not catalog.names:
            return "Ich kenne die Patch-Namen noch nicht. Sag 'lies die Patches ein', das dauert etwa zehn Sekunden."
        query = str(args.get("filter") or "").strip().lower()
        items = sorted(catalog.names.items())
        if query:
            items = [(k, v) for k, v in items if query in v.lower()]
            if not items:
                return f"Kein Patch-Name enthält '{query}'. Insgesamt kenne ich {len(catalog.names)} Patches."
        return "Patches: " + ", ".join(f"{k} {v}" for k, v in items) + "."

    async def select_patch(args: dict[str, Any]) -> str:
        synth = _int_in_range(args, "synth", 1, 2, "Der Synth")
        number = args.get("number")
        label = ""
        if number is None:
            name = str(args.get("name") or "").strip()
            if not name:
                raise ArgumentError("Sag mir die Patch-Nummer (1 bis 64) oder den Namen.")
            hit = catalog.find(name)
            if hit is None:
                hint = "" if catalog.names else " Die Namen kenne ich erst nach 'lies die Patches ein'."
                raise ArgumentError(f"Ich finde keinen Patch namens {name}.{hint}")
            number, label = hit
        number = _int_in_range({"n": number}, "n", 1, 64, "Die Patch-Nummer")
        result = await backend.call("select_patch", {"synth": synth, "patch_number": number - 1})
        if result.is_error:
            return f"Patch wählen ging nicht: {result.text}"
        ledger.selected_patch[synth] = number
        ledger.designed_patch.pop(synth, None)
        ledger.unsaved = True
        ledger.device_dirty = True
        label = label or catalog.names.get(number, "")
        shown = f"{number} ({label})" if label else str(number)
        return f"Patch {shown} auf Synth {synth} gewählt."

    bridge.register_virtual("scan_patches", scan_patches)
    bridge.register_virtual("list_patches", list_patches)
    bridge.register_virtual("select_patch", select_patch)
