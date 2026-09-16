"""The compose and revise tools: run the composer step, load the result."""

from __future__ import annotations

from typing import Any

from .bridge import Bridge
from .composer import ComposeFailed, Composer, track_edits


def register_compose_tools(bridge: Bridge, composer: Composer) -> None:
    ledger, backend = bridge.ledger, bridge.backend

    def summary(song: dict[str, Any], verb: str) -> str:
        playing = [t for p in song.get("patterns", {}).values() for t in (p.get("tracks") or {})]
        tracks = ", ".join(sorted(set(playing))) or "keine Spuren"
        patterns = ", ".join(song.get("patterns", {}))
        return f"{verb}: {ledger.song_name}, {int(ledger.bpm or 0)} BPM, Patterns {patterns}, Spuren {tracks}."

    async def load_new(song: dict[str, Any], label: str, verb: str) -> str:
        was_running = ledger.running and ledger.source == "internal"
        if was_running:
            await bridge.dispatch("stop_sequencer", {})
        text = await bridge.load_song_dict(song, label)  # type: ignore[attr-defined]
        if not ledger.last_load_ok:
            return text  # load failed; the text says why
        if was_running:
            await bridge.dispatch("start_sequencer", {})
        return summary(song, verb)

    async def compose(args: dict[str, Any]) -> str:
        brief = str(args.get("brief", "")).strip()
        if not brief:
            return "Sag mir, was für Musik du möchtest: Stil, Tempo, Stimmung."
        ledger.composer_job = brief
        try:
            song = await composer.compose(brief)
        except ComposeFailed as e:
            return f"Das Bauen hat nicht geklappt: {str(e)[:160]}. Beschreib es anders oder kürzer."
        finally:
            ledger.composer_job = None
        return await load_new(song, song.get("name") or brief[:16], "Fertig gebaut")

    async def revise(args: dict[str, Any]) -> str:
        instruction = str(args.get("instruction", "")).strip()
        if ledger.song_json is None:
            return "Es ist kein Song geladen, den ich ändern könnte."
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
