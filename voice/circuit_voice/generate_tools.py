"""Small, fast jobs for the composer model: one track, or one patch.

``generate_track`` asks the text model for the steps of a single track (a
few seconds, the rest of the song untouched) and writes them like
``write_notes`` would, undoable. ``design_patch`` asks it for a complete
patch in the ``create_synth_patch`` shape and sends it to the synth's working
memory; ``save_patch`` writes that to a flash slot after one confirmation.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from circuit_tracks.song_schema import get_song_json_schema

from .bridge import Bridge
from .composer import ComposeFailed, ComposerModel, validate_song
from .edit_tools import DRUM_TRACKS, SYNTH_TRACKS
from .ledger import Change
from .tool_cards import ArgumentError, _int_in_range

TRACK_RULES = """\
You write one track of a step sequencer pattern for a Novation Circuit Tracks.
Answer with JSON only: {"steps": {"<whole-number step index as string, 0-based>": {...}}}.
Step indices are whole numbers only (no "2.5").
Synth/MIDI steps: {"note": <MIDI 0-127>, "velocity": 1-127, "gate": 0.25-16 (steps)}.
Drum steps: {"velocity": 1-127}. Use only step indices below the pattern length.
Fit the song's tempo and key; leave space; make it loop well. No prose.
"""

PATCH_RULES = """\
You design synth patches for a Novation Circuit Tracks. Answer with JSON only
in the shape given (name up to 16 characters; params are 0-127; mod matrix
sources and destinations use space-separated names such as "filter frequency";
depth is -64..63; macros add on top of base values, so set a base low when a
macro should sweep it up). Use the standard macro layout as orientation:
1 Oscillator, 2 Oscillator Mod, 3 Amp Envelope, 4 Filter Envelope,
5 Filter Frequency, 6 Resonance, 7 Modulation, 8 FX. Give macro 5 a filter
frequency target with a low base so the player can open the filter by hand.
"""


STEP_FIELDS = {"note", "notes", "velocity", "gate", "tie", "probability"}
DRUM_STEP_FIELDS = {"velocity", "probability", "sample"}


def sanitize_steps(steps: Any, drum: bool) -> dict[str, Any] | None:
    """Keep whole-number step keys and the fields set_track accepts."""
    if not isinstance(steps, dict):
        return None
    allowed = DRUM_STEP_FIELDS if drum else STEP_FIELDS
    out: dict[str, Any] = {}
    for key, value in steps.items():
        try:
            index = int(float(key))
        except (TypeError, ValueError):
            continue
        if index < 0 or float(key) != index or not isinstance(value, dict):
            continue
        cleaned = {k: v for k, v in value.items() if k in allowed and v is not None}
        if not drum and "note" not in cleaned and "notes" not in cleaned:
            continue
        out[str(index)] = cleaned
    return out


def _track_schema() -> dict[str, Any]:
    song = get_song_json_schema()
    return {
        "type": "object",
        "properties": {
            "steps": {
                "type": "object",
                "additionalProperties": {
                    "anyOf": [{"$ref": "#/$defs/SynthStepConfig"}, {"$ref": "#/$defs/DrumStepConfig"}]
                },
            }
        },
        "required": ["steps"],
        "$defs": {k: v for k, v in song["$defs"].items() if k in ("SynthStepConfig", "DrumStepConfig")},
    }


def register_generate_tools(bridge: Bridge, model: ComposerModel, patch_schema: dict[str, Any] | None = None) -> None:
    ledger, backend = bridge.ledger, bridge.backend
    track_schema = _track_schema()
    reference: dict[str, str] = {}

    async def prepare() -> None:
        for section in ("patch", "mod_matrix", "macros"):
            result = await backend.call("get_parameter_reference", {"section": section})
            if not result.is_error:
                reference[section] = result.text

    bridge.generate_prepare = prepare  # type: ignore[attr-defined]

    async def generate_track(args: dict[str, Any]) -> str:
        track = str(args.get("track", "")).lower()
        if track not in SYNTH_TRACKS + DRUM_TRACKS:
            raise ArgumentError("Welche Spur? synth1, synth2, drum1 bis drum4, midi1 oder midi2.")
        brief = str(args.get("brief", "")).strip()
        if not brief:
            raise ArgumentError("Sag mir, wie die Spur klingen soll.")
        if ledger.song_json is None:
            raise ArgumentError("Es ist kein Song geladen. Sag 'neuer Song' oder lade einen.")
        song = ledger.song_json
        pattern_name = str(args.get("pattern") or ledger.first_pattern() or next(iter(song["patterns"])))
        key = pattern_name.strip().lower().replace(" ", "")
        if key not in song["patterns"]:
            raise ArgumentError(f"Das Pattern {pattern_name} gibt es nicht. Vorhanden: {', '.join(song['patterns'])}.")
        pat = song["patterns"][key]
        length = int(pat.get("length", 32))
        others = {
            t: sorted(int(i) for i in (d.get("steps") or {}))
            for t, d in (pat.get("tracks") or {}).items()
            if t != track
        }
        context = {
            "bpm": song.get("bpm"),
            "scale": song.get("scale"),
            "pattern_length": length,
            "track": track,
            "kind": "drum" if track in DRUM_TRACKS else "synth",
            "other_tracks_step_positions": others,
        }
        user = f"Context: {json.dumps(context)}\nBrief (German): {brief}\nReturn the steps for {track}."
        messages: list[tuple[str, str]] = [("user", user)]
        ledger.composer_job = brief
        try:
            for _attempt in range(3):
                answer = await model.generate(TRACK_RULES, messages, track_schema)
                steps = sanitize_steps(answer.get("steps"), track in DRUM_TRACKS) if isinstance(answer, dict) else None
                candidate = copy.deepcopy(song)
                candidate["patterns"][key].setdefault("tracks", {})[track] = {"steps": steps or {}}
                ok, error = validate_song(candidate)
                bad_index = [i for i in (steps or {}) if not str(i).lstrip("-").isdigit() or int(i) >= length]
                if ok is not None and steps and not bad_index:
                    break
                error = error or ("step indices out of range: " + ", ".join(bad_index) if bad_index else "no steps")
                messages.append(("model", json.dumps(answer)[:4000]))
                messages.append(("user", f"Rejected: {error}. Return corrected JSON."))
            else:
                return f"Die Spur ist nicht gelungen: {str(error)[:120]}. Beschreib es anders."
        except ComposeFailed as e:
            return f"Die Spur ist nicht gelungen: {e}"
        finally:
            ledger.composer_job = None
        before = copy.deepcopy((pat.get("tracks") or {}).get(track, {}).get("steps") or {})
        result = await backend.call(
            "set_track", {"pattern_name": key, "track": track, "steps": steps, "clear_existing": True}
        )
        if result.is_error:
            return f"Das Schreiben ging nicht: {result.text}"
        pat.setdefault("tracks", {})[track] = {"steps": steps}
        ledger.unsaved = True
        ledger.history.append(
            Change(
                "restore_track",
                {"pattern": key, "track": track},
                {"pattern": key, "track": track, "steps": before},
                f"{track} in {key} wie vorher",
            )
        )
        return f"{track} in {key} hat jetzt {len(steps)} Schritte: {brief[:60]}."

    async def design_patch(args: dict[str, Any]) -> str:
        synth = _int_in_range(args, "synth", 1, 2, "Der Synth")
        brief = str(args.get("brief", "")).strip()
        if not brief:
            raise ArgumentError("Beschreib den Sound: zum Beispiel 'warmer, weicher Bass mit langsamem Filter'.")
        schema = patch_schema or await backend.input_schema("create_synth_patch")
        schema = {k: v for k, v in schema.items() if k != "title"}
        props = {k: v for k, v in schema.get("properties", {}).items() if k != "synth"}
        schema = {**schema, "properties": props, "required": [r for r in schema.get("required", []) if r != "synth"]}
        system = PATCH_RULES + "".join(f"\n## Reference: {k}\n{v}" for k, v in reference.items())
        messages: list[tuple[str, str]] = [("user", f"Brief (German): {brief}\nReturn the patch as JSON.")]
        ledger.composer_job = brief
        try:
            for _attempt in range(3):
                answer = await model.generate(system, messages, schema)
                if not isinstance(answer, dict) or not answer.get("name"):
                    messages.append(("user", "Rejected: not a patch object with a name. Return corrected JSON."))
                    continue
                answer.pop("synth", None)
                result = await backend.call("create_synth_patch", {"synth": synth, **answer})
                if not result.is_error:
                    break
                messages.append(("model", json.dumps(answer)[:6000]))
                messages.append(("user", f"Rejected by the device tool: {result.text[:600]}. Return corrected JSON."))
            else:
                return f"Der Patch ist nicht gelungen: {result.text[:160]}"
        finally:
            ledger.composer_job = None
        ledger.device_dirty = True
        ledger.unsaved = True
        name = str(answer.get("name"))[:16]
        return (
            f"Patch '{name}' ist auf Synth {synth} geladen, aber noch nicht gespeichert. "
            "Sag 'speicher den Patch in Slot …', wenn er bleiben soll."
        )

    async def save_patch(args: dict[str, Any]) -> Any:
        synth = _int_in_range(args, "synth", 1, 2, "Der Synth")
        slot = _int_in_range(args, "slot", 1, 64, "Der Slot")
        if not args.get("confirmed"):
            return {
                "needs_confirmation": f"Patch-Slot {slot} wird überschrieben. Speichern?",
                "hint": "Ask the user; on 'ja' call save_patch again with confirmed=true.",
            }
        result = await backend.call("save_synth_patch", {"synth": synth, "slot": slot - 1})
        if result.is_error:
            return f"Speichern ging nicht: {result.text}"
        return f"Der Patch von Synth {synth} ist in Slot {slot} gespeichert."

    bridge.register_virtual("generate_track", generate_track)
    bridge.register_virtual("design_patch", design_patch)
    bridge.register_virtual("save_patch", save_patch)
