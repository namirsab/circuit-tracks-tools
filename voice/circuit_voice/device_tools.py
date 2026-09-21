"""Workflow B: performing projects stored on the Circuit Tracks.

The device does not report its state, so these tools keep the ledger honest:
which project is selected, whether our clock drives the transport, the tempo
per project. ``transport`` and ``select_project`` also enforce the one-source
rule together with the internal sequencer tools (see ``bridge``).
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from .bridge import Bridge
from .ledger import TRACK_NAMES_DE, Change
from .tool_cards import TRACKS, ArgumentError, _int_in_range

_BPM_RE = re.compile(r"clock at ([0-9.]+) BPM")
# The device loads a project for a moment after the program change. Clock, start or a
# SysEx read during that window hung it on hardware, so a start waits this long.
PROJECT_LOAD_S = 2.0


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
        state = await bridge.sync_device()
        if ledger.source != "device" and ledger.song_json is not None:
            # Coming from the internal sequencer, "play it on the device" means the project this
            # song was saved to, not whatever happens to be loaded there.
            if ledger.slot is None:
                return (
                    "Der Song ist noch nicht auf dem Gerät. Sag 'speicher das in Slot …', dann spiele ich ihn "
                    "vom Gerät; oder sag 'spiel Projekt …' für ein gespeichertes Projekt."
                )
            if ledger.device_project != ledger.slot:
                chosen = await select_project({"number": ledger.slot, "immediate": True, "confirmed": True})
                if not str(chosen).endswith("gewählt."):
                    return chosen
            if ledger.unsaved:
                note += "Das Gerät spielt den Stand vom letzten Speichern. "
        if ledger.project_selected_at is not None:
            wait = PROJECT_LOAD_S - (time.monotonic() - ledger.project_selected_at)
            ledger.project_selected_at = None
            if wait > 0:
                await asyncio.sleep(wait)
        payload: dict[str, Any] = {"action": action}
        # A tempo the user asked for, else the device's own clock (it sends its project tempo even
        # while stopped), else what we remember for the project; the server assumes 120 otherwise.
        bpm = (
            args.get("bpm")
            or ledger.pending_bpm
            or state.get("bpm")
            or ledger.device_bpm.get(ledger.device_project)
            or ledger.saved_bpm.get(ledger.device_project)
        )
        if bpm:
            payload["bpm"] = float(bpm)
        result = await backend.call("transport", payload)
        if result.is_error:
            return f"Starten ging nicht: {result.text}"
        ledger.pending_bpm = None
        match = _BPM_RE.search(result.text or "")
        if match:
            ledger.bpm = float(match.group(1))
            if ledger.device_project is not None:
                ledger.device_bpm[ledger.device_project] = ledger.bpm
        ledger.source, ledger.running = "device", True
        where = f"Projekt {ledger.device_project}" if ledger.device_project else "das Gerät"
        tempo = f", {int(ledger.bpm)} BPM" if ledger.bpm else ""
        if "assumed 120" in (result.text or ""):
            tempo = ", 120 BPM angenommen, das Gerät meldet kein Tempo"
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
            ledger.pending_bpm = bpm
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
            known = ledger.saved_bpm.get(number)
            if known and known != ledger.bpm:  # a project saved from here: follow its tempo
                changed = await backend.call("set_clock_bpm", {"bpm": known})
                if not changed.is_error:
                    ledger.bpm = ledger.device_bpm[number] = known
            when = "sofort" if immediate else "am Ende des Patterns"
            return f"Projekt {number} kommt {when}."
        # Loaded while stopped: the project brings its own tempo. We know it for projects saved
        # from here; otherwise the next start reads it from the device.
        ledger.device_bpm.pop(number, None)
        if number in ledger.saved_bpm:
            ledger.device_bpm[number] = ledger.saved_bpm[number]
        ledger.project_selected_at = time.monotonic()
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

    async def set_macro(args: dict[str, Any]) -> str:
        synth = _int_in_range(args, "synth", 1, 2, "Der Synth")
        macro = _int_in_range(args, "macro", 1, 8, "Die Makro-Nummer")
        value = _int_in_range(args, "value", 0, 127, "Der Wert")
        # CC 80-87 move the hardware knobs themselves; the server's set_macro only emulates them.
        result = await backend.call("set_synth_params", {"synth": synth, "params": {f"macro_knob{macro}": value}})
        if result.is_error:
            return f"Makro {macro} ging nicht: {result.text}"
        ledger.observe("set_macro", {"synth": synth, "macro": macro, "value": value}, result.data)
        return f"Makro {macro} auf Synth {synth} steht auf {value}."

    bridge.register_virtual("set_macro", set_macro)
    bridge.register_virtual("mute_track", mute_track)
    bridge.register_virtual("transport", transport)
    bridge.register_virtual("set_tempo", set_tempo)
    bridge.register_virtual("select_project", select_project)
    bridge.register_virtual("import_project", import_project)
