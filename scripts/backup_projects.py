"""Download byte-perfect .ncs backups of project slots from the Circuit Tracks."""

import sys
import time
from pathlib import Path

from circuit_tracks.midi import MidiConnection
from circuit_tracks.ncs_transfer import (
    NCS_FILE_SIZE,
    list_directory,
    receive_ncs_project,
)

PORT = "Circuit Tracks MIDI"
OUT_DIR = Path(__file__).resolve().parent.parent / "project-backups"


def safe_name(name: str) -> str:
    keep = [c if (c.isalnum() or c in " -_") else "_" for c in name]
    return "".join(keep).strip().replace(" ", "_") or "UNNAMED"


def main(slots: list[int]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    midi = MidiConnection()
    midi.connect(PORT)
    print(f"Connected to {PORT}")

    # Get real project names from the device directory.
    names: dict[int, str] = {}
    try:
        for entry in list_directory(midi):
            names[entry["slot"]] = entry["filename"]
        print(f"Directory listing: {len(names)} entries")
    except Exception as e:  # noqa: BLE001
        print(f"WARN: directory listing failed: {e}")

    time.sleep(0.3)

    for slot in slots:
        label = names.get(slot, "")
        try:
            raw = receive_ncs_project(midi, slot)
        except Exception as e:  # noqa: BLE001
            print(f"slot {slot:02d}: ERROR {e}")
            continue
        ok = len(raw) == NCS_FILE_SIZE
        stem = f"{slot:02d}_{safe_name(label)}" if label else f"{slot:02d}_SESSION"
        out = OUT_DIR / f"{stem}.ncs"
        out.write_bytes(raw)
        print(f"slot {slot:02d}: {len(raw)} bytes {'OK' if ok else 'SIZE-MISMATCH'} name={label!r} -> {out.name}")
        time.sleep(0.3)

    midi.disconnect()
    print(f"Done. Files in {OUT_DIR}")


if __name__ == "__main__":
    slots = [int(a) for a in sys.argv[1:]] or list(range(24, 32))
    main(slots)
