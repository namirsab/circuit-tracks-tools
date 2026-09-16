"""Workflow B: performing projects stored on the Circuit Tracks.

The device does not report its state, so these tools keep the ledger honest:
which project is selected, whether our clock drives the transport, the tempo
per project. ``transport`` and ``select_project`` also enforce the one-source
rule together with the internal sequencer tools (see ``bridge``).
"""

from __future__ import annotations

import re
from typing import Any

from .bridge import Bridge
from .ledger import TRACK_NAMES_DE, Change
from .tool_cards import TRACKS, ArgumentError, _int_in_range

_BPM_RE = re.compile(r"clock at ([0-9.]+) BPM")


def register_device_tools(bridge: Bridge) -> None:
    ledger, backend = bridge.ledger, bridge.backend

    async def transport(args: dict[str, Any]) -> str:
        action = str(args.get("action", "")).lower()
        if action not in ("start", "stop", "continue"):
            raise ArgumentError("Die Aktion muss start, stop oder continue sein.")
        if action == "stop":
            result = await backend.call("transport", {"action": "stop"})
            if result.is_error:
                return f"Stoppen ging nicht: {result.text}"
            ledger.running = False
            return "Das Gerät ist gestoppt."
        note = ""
        if ledger.running and ledger.source == "internal":
            await bridge.dispatch("stop_sequencer", {})
            note = "Ich habe den internen Sequenzer gestoppt. "
        payload: dict[str, Any] = {"action": action}
        bpm = args.get("bpm") or ledger.device_bpm.get(ledger.device_project)
        if bpm:
            payload["bpm"] = float(bpm)
        result = await backend.call("transport", payload)
        if result.is_error:
            return f"Starten ging nicht: {result.text}"
        match = _BPM_RE.search(result.text or "")
        if match:
            ledger.bpm = float(match.group(1))
            if ledger.device_project is not None:
                ledger.device_bpm[ledger.device_project] = ledger.bpm
        ledger.source, ledger.running = "device", True
        where = f"Projekt {ledger.device_project}" if ledger.device_project else "das Gerät"
        tempo = f", {int(ledger.bpm)} BPM" if ledger.bpm else ""
        return f"{note}{where} läuft{tempo}."

    async def set_tempo(args: dict[str, Any]) -> str:
        bpm = float(_int_in_range(args, "bpm", 20, 300, "Das Tempo"))
        result = await backend.call("set_clock_bpm", {"bpm": bpm})
        if result.is_error:
            return f"Tempo ändern ging nicht: {result.text}"
        ledger.bpm = bpm
        if ledger.device_project is not None:
            ledger.device_bpm[ledger.device_project] = bpm
        if "not running" in (result.text or ""):
            return f"Tempo auf {int(bpm)} vorgemerkt; es gilt, sobald das Gerät von hier gestartet wird."
        return f"Tempo ist jetzt {int(bpm)}."

    async def select_project(args: dict[str, Any]) -> Any:
        number = _int_in_range(args, "number", 1, 64, "Die Projektnummer")
        immediate = bool(args.get("immediate", False))
        if ledger.device_dirty and not args.get("confirmed"):
            return {
                "needs_confirmation": f"Projekt {number} wählen? Die Änderungen an den Sounds gehen dabei verloren.",
                "hint": "Ask the user; on 'ja' call select_project again with confirmed=true.",
            }
        device_running = ledger.running and ledger.source == "device"
        queued = device_running and not immediate
        result = await backend.call("select_project", {"project_number": number - 1, "queued": queued})
        if result.is_error:
            return f"Projekt wählen ging nicht: {result.text}"
        ledger.device_project = number
        ledger.device_dirty = False
        if device_running:
            ledger.device_bpm[number] = ledger.bpm  # loaded while running: keeps the current tempo
            when = "sofort" if immediate else "am Ende des Patterns"
            return f"Projekt {number} kommt {when}."
        ledger.device_bpm.pop(number, None)  # loaded while stopped: brings its own saved tempo
        ledger.source = "device"
        return f"Projekt {number} ist gewählt."

    async def import_project(args: dict[str, Any]) -> str:
        number = _int_in_range(args, "number", 1, 64, "Die Projektnummer")
        result = await backend.call("read_project", {"slot": number - 1})
        if result.is_error or not isinstance(result.data, dict):
            return f"Projekt {number} konnte nicht gelesen werden: {result.text}"
        data = {k: v for k, v in result.data.items() if not k.startswith("_")}
        patterns = list((data.get("patterns") or {}).keys())
        ledger.song_loaded(
            data.get("name") or f"Projekt {number}",
            float(data.get("bpm") or 120),
            patterns,
            list(data.get("song") or []),
        )
        ledger.song_json = data
        ledger.unsaved = False
        ledger.slot = number
        return (
            f"Projekt {number} ist als Song {ledger.song_name} geladen: "
            f"{len(patterns)} Patterns, {int(ledger.bpm)} BPM."
        )

    async def mute_track(args: dict[str, Any]) -> str:
        track = str(args.get("track", ""))
        muted = bool(args.get("muted", True))
        if track not in TRACKS:
            raise ArgumentError(f"Unbekannte Spur {track!r}. Möglich: {', '.join(TRACKS)}.")
        name = TRACK_NAMES_DE.get(track, track)
        if ledger.source != "device":
            result = await backend.call("mute_track", {"track": track, "muted": muted})
            if result.is_error:
                return f"Stummschalten ging nicht: {result.text}"
            ledger.observe("mute_track", {"track": track, "muted": muted}, result.data)
            return f"{name} ist jetzt {'stumm' if muted else 'wieder an'}."
        # Device mode: no mute over MIDI, so use the level parameters and remember the old level.
        if track.startswith("midi"):
            return f"{name} kann ich auf dem Gerät nicht stumm schalten."
        if muted:
            previous = ledger.known.get("project", {}).get(f"{track}_level") if track.startswith("synth") else None
            if previous is None and track.startswith("drum"):
                previous = ledger.known.get(track, {}).get("level")
            ledger.saved_levels[track] = int(previous) if previous is not None else 100
            level = 0
        else:
            level = ledger.saved_levels.pop(track, 100)
        if track.startswith("synth"):
            result = await backend.call("set_project_params", {"params": {f"{track}_level": level}})
        else:
            result = await backend.call("set_drum_params", {"drum": int(track[-1]), "params": {"level": level}})
        if result.is_error:
            return f"Stummschalten ging nicht: {result.text}"
        ledger.mutes[track] = muted
        ledger.history.append(
            Change(
                "mute_track",
                {"track": track, "muted": muted},
                {"track": track, "muted": not muted},
                f"{name} wieder {'an' if muted else 'stumm'}",
            )
        )
        return f"{name} ist jetzt {'stumm' if muted else 'wieder an'} (Lautstärke {level})."

    bridge.register_virtual("mute_track", mute_track)
    bridge.register_virtual("transport", transport)
    bridge.register_virtual("set_tempo", set_tempo)
    bridge.register_virtual("select_project", select_project)
    bridge.register_virtual("import_project", import_project)
