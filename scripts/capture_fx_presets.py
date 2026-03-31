#!/usr/bin/env python3
"""Capture FX preset parameter values from exported NCS files.

Usage:
    1. On Circuit Tracks, select each delay/reverb preset and export via Components
    2. Run: python scripts/capture_fx_presets.py path/to/ncs/files/

The script parses each NCS file, extracts the preset indices and their
corresponding FX parameter values, and outputs Python dict literals
ready to paste into constants.py.
"""

import sys
from pathlib import Path

# Add src to path so we can import circuit_mcp
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from circuit_mcp.ncs_parser import parse_ncs


def extract_fx_presets(ncs_dir: Path) -> None:
    ncs_files = sorted(ncs_dir.glob("*.ncs"))
    if not ncs_files:
        print(f"No .ncs files found in {ncs_dir}")
        sys.exit(1)

    reverb_presets: dict[int, dict] = {}
    delay_presets: dict[int, dict] = {}

    for path in ncs_files:
        data = path.read_bytes()
        ncs = parse_ncs(data)

        ri = ncs.project_settings.reverb_preset
        di = ncs.project_settings.delay_preset

        reverb_entry = {
            "type": ncs.fx.reverb_type,
            "decay": ncs.fx.reverb_decay,
            "damping": ncs.fx.reverb_damping,
        }
        delay_entry = {
            "time": ncs.fx.delay_time,
            "sync": ncs.fx.delay_sync,
            "feedback": ncs.fx.delay_feedback,
            "width": ncs.fx.delay_width,
            "lr_ratio": ncs.fx.delay_lr_ratio,
            "slew": ncs.fx.delay_slew,
        }

        if ri not in reverb_presets:
            reverb_presets[ri] = reverb_entry
            print(f"  {path.name}: reverb preset {ri} -> {reverb_entry}")
        elif reverb_presets[ri] != reverb_entry:
            print(f"  WARNING: {path.name} reverb preset {ri} differs from previous capture!")
            print(f"    previous: {reverb_presets[ri]}")
            print(f"    this:     {reverb_entry}")

        if di not in delay_presets:
            delay_presets[di] = delay_entry
            print(f"  {path.name}: delay preset {di} -> {delay_entry}")
        elif delay_presets[di] != delay_entry:
            print(f"  WARNING: {path.name} delay preset {di} differs from previous capture!")
            print(f"    previous: {delay_presets[di]}")
            print(f"    this:     {delay_entry}")

    print(f"\nCaptured {len(reverb_presets)} reverb presets, {len(delay_presets)} delay presets")

    # Output as Python dicts
    print("\n# --- Paste into constants.py ---\n")
    print("REVERB_PRESETS = {")
    for idx in sorted(reverb_presets):
        p = reverb_presets[idx]
        print(f"    {idx}: {{\"type\": {p['type']}, \"decay\": {p['decay']}, \"damping\": {p['damping']}}},")
    print("}")

    print("\nDELAY_PRESETS = {")
    for idx in sorted(delay_presets):
        p = delay_presets[idx]
        vals = ", ".join(f'"{k}": {v}' for k, v in p.items())
        print(f"    {idx}: {{{vals}}},")
    print("}")

    # Report missing
    missing_reverb = set(range(8)) - set(reverb_presets.keys())
    missing_delay = set(range(16)) - set(delay_presets.keys())
    if missing_reverb:
        print(f"\nMissing reverb presets: {sorted(missing_reverb)}")
    if missing_delay:
        print(f"Missing delay presets: {sorted(missing_delay)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <directory-with-ncs-files>")
        sys.exit(1)
    extract_fx_presets(Path(sys.argv[1]))
