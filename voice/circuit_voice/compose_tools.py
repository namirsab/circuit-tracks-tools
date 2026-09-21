"""The compose and revise tools: run the composer step, load the result."""

from __future__ import annotations

import time
from typing import Any

from .bridge import Bridge
from .composer import ComposeFailed, Composer, track_edits
from .edit_tools import snap_to_scale

# A confirmed build that fails leaves the user's yes standing for a retry this long.
RETRY_WINDOW_S = 180.0


def register_compose_tools(bridge: Bridge, composer: Composer) -> None:
    ledger, backend = bridge.ledger, bridge.backend

    def summary(song: dict[str, Any], verb: str, restarted: bool = False) -> str:
        playing = [t for p in song.get("patterns", {}).values() for t in (p.get("tracks") or {})]
        tracks = ", ".join(sorted(set(playing))) or "keine Spuren"
        patterns = ", ".join(song.get("patterns", {}))
        text = f"{verb}: {ledger.song_name}, {int(ledger.bpm or 0)} BPM, Patterns {patterns}, Spuren {tracks}."
        if restarted:
            text += " Es läuft wieder."
        if composer.last_repairs:
            text += " Weggelassen, weil das Gerät es nicht kennt: " + "; ".join(composer.last_repairs[:3]) + "."
        return text

    async def load_new(song: dict[str, Any], label: str, verb: str) -> str:
        was_running = ledger.running and ledger.source == "internal"
        if was_running:
            await bridge.dispatch("stop_sequencer", {})
        text = await bridge.load_song_dict(song, label)
        if not ledger.last_load_ok:
            return text  # load failed; the text says why
        if was_running:
            await bridge.dispatch("start_sequencer", {})
        return summary(song, verb, restarted=was_running)

    async def compose(args: dict[str, Any]) -> Any:
        brief = str(args.get("brief", "")).strip()
        if not brief:
            return "Sag mir, was für Musik du möchtest: Stil, Tempo, Stimmung."
        if not args.get("confirmed") and time.monotonic() >= ledger.compose_ok_until:
            return {
                "needs_confirmation": f"Einen ganzen Song bauen ({brief[:60]}) dauert ein bis zwei Minuten und "
                "ersetzt den geladenen Song. Soll ich?",
                "hint": "Ask; on yes call compose again with confirmed=true.",
            }
        ledger.composer_job = brief
        ledger.compose_ok_until = time.monotonic() + RETRY_WINDOW_S
        try:
            song = await composer.compose(brief)
        except ComposeFailed as e:
            return (
                f"Das Bauen hat nicht geklappt: {str(e)[:160]}. Sag 'nochmal', dann versuche ich es gleich "
                "wieder, ohne neue Frage; oder beschreib es anders."
            )
        finally:
            ledger.composer_job = None
        text = await load_new(song, song.get("name") or brief[:16], "Fertig gebaut")
        if ledger.last_load_ok:
            ledger.compose_ok_until = 0.0  # the song was replaced; a new build needs a new yes
        return text

    async def revise(args: dict[str, Any]) -> Any:
        instruction = str(args.get("instruction", "")).strip()
        if ledger.song_json is None:
            return "Es ist kein Song geladen, den ich ändern könnte."
        if not args.get("confirmed"):
            return {
                "needs_confirmation": f"Den ganzen Song umbauen ({instruction[:60]}) dauert bis zu einer Minute. "
                "Soll ich?",
                "hint": "Ask; on yes call revise again with confirmed=true.",
            }
        old = ledger.song_json
        ledger.composer_job = instruction
        try:
            new = await composer.compose(instruction, current_song=old)
        except ComposeFailed as e:
            return f"Die Änderung hat nicht geklappt: {str(e)[:160]}."
        finally:
            ledger.composer_job = None
        edits = track_edits(old, new)
        if edits is None:
            return await load_new(new, ledger.song_name or "Song", "Geändert")
        for pattern, track, data in edits:
            snap_to_scale(new, data.get("steps") or {})  # set_track does not; loading and saving do
            result = await backend.call(
                "set_track", {"pattern_name": pattern, "track": track, "steps": data.get("steps", {})}
            )
            if result.is_error:
                return f"Die Änderung an {track} in {pattern} ging nicht: {result.text}"
        ledger.song_json = new
        ledger.unsaved = True
        changed = ", ".join(f"{track} in {pattern}" for pattern, track, _ in edits) or "nichts"
        return f"Geändert: {changed}. Läuft weiter."

    bridge.register_virtual("compose", compose)
    bridge.register_virtual("revise", revise)
