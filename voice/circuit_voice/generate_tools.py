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
Synth/MIDI steps: {"note": <MIDI 0-127>, "velocity": 1-127, "gate": 0.25-16 (steps)}; for a chord use
{"notes": [<MIDI>, <MIDI>, ...]} (up to 6 notes) instead of "note". When the brief asks for chords, write chords.
Drum steps: {"velocity": 1-127}. Use only step indices below the pattern length.
Fit the song's tempo and key; leave space; make it loop well. No prose.
"""

PATCH_RULES = """\
You design synth patches for a Novation Circuit Tracks. Answer with JSON only
in the shape given. You start from nothing: there are no presets, and every
parameter in "params" is yours to set. Two briefs must never give the same
numbers: choose waveforms, levels, detune, filter and both envelopes for THIS
sound (a kalimba is a short sine/triangle pluck with a fast decay and no
sustain; a guitar is a brighter pluck with a filter envelope and longer decay;
an electric piano has a bell-like attack, medium decay and some sustain; a pad
has slow attack and release). Pitch values centre on 64 (osc semitones and
cents). (Name up to 16 characters; params are 0-127; mod matrix
sources and destinations use space-separated names such as "filter frequency";
depth is -64..63; macros add on top of base values, so set a base low when a
macro should sweep it up). Use the standard macro layout as orientation:
1 Oscillator, 2 Oscillator Mod, 3 Amp Envelope, 4 Filter Envelope,
5 Filter Frequency, 6 Resonance, 7 Modulation, 8 FX. Give macro 5 a filter
frequency target with a low base so the player can open the filter by hand.
"macros" is optional: knobs you leave out get the standard layout.
Pads, organs, keys and anything meant for chords must be polyphonic
(polyphony_mode 2); only basses and leads may be mono. Make the patch loud
enough to hear (oscillator levels around 100 or more), and follow the brief's
character: an organ sounds like an organ (sine or organ-like waves, fast attack,
full sustain, short release), "crazy" means audibly unusual modulation.
"""


# The parameters that decide what a sound is. The designer must set every one of them: with an open
# "params" object the model returned none at all and every sound was the same preset (2026-09-17).
DESIGN_PARAMS = (
    "polyphony_mode", "portamento_rate",
    "osc1_wave", "osc1_wave_interpolate", "osc1_pulse_width_index", "osc1_virtual_sync_depth", "osc1_density",
    "osc1_density_detune", "osc1_semitones", "osc1_cents", "osc1_level",
    "osc2_wave", "osc2_wave_interpolate", "osc2_pulse_width_index", "osc2_virtual_sync_depth", "osc2_density",
    "osc2_density_detune", "osc2_semitones", "osc2_cents", "osc2_level",
    "ring_mod_level", "noise_level", "drive", "drive_type",
    "filter_type", "filter_frequency", "filter_tracking", "filter_resonance", "env2_to_filter_freq",
    "env1_velocity", "env1_attack", "env1_decay", "env1_sustain", "env1_release",
    "env2_velocity", "env2_attack", "env2_decay", "env2_sustain", "env2_release",
    "lfo1_waveform", "lfo1_rate", "lfo2_waveform", "lfo2_rate",
    "distortion_level", "distortion_type", "chorus_level", "chorus_rate", "chorus_feedback", "chorus_mod_depth",
    "eq_bass_level", "eq_mid_level", "eq_treble_level",
)  # fmt: skip
MOD_SOURCES = (
    "direct",
    "velocity",
    "keyboard",
    "LFO 1+",
    "LFO 1+/-",
    "LFO 2+",
    "LFO 2+/-",
    "env amp",
    "env filter",
    "env 3",
)  # noqa: E501
MOD_DESTS = (
    "osc 1 & 2 pitch", "osc 1 pitch", "osc 2 pitch", "osc 1 v-sync", "osc 2 v-sync", "osc 1 pulse width / index",
    "osc 2 pulse width / index", "osc 1 level", "osc 2 level", "noise level", "ring modulation 1*2 level",
    "filter drive amount", "filter frequency", "filter resonance", "LFO 1 rate", "LFO 2 rate",
    "amp envelope decay", "filter envelope decay",
)  # fmt: skip


def design_schema() -> dict[str, Any]:
    """A closed schema for the sound designer: every sound-shaping parameter is a required number."""
    value = {"type": "integer", "minimum": 0, "maximum": 127}
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "up to 16 characters"},
            "params": {
                "type": "object",
                "properties": {name: value for name in DESIGN_PARAMS},
                "required": list(DESIGN_PARAMS),
            },
            "mod_matrix": {
                "type": "array",
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "properties": {
                        "source1": {"type": "string", "enum": list(MOD_SOURCES)},
                        "dest": {"type": "string", "enum": list(MOD_DESTS)},
                        "depth": {"type": "integer", "minimum": -64, "maximum": 63},
                    },
                    "required": ["source1", "dest", "depth"],
                },
            },
            "macros": {
                "type": "array",
                "description": "one entry per knob that should differ from the standard layout",
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "properties": {
                        "knob": {"type": "integer", "minimum": 1, "maximum": 8},
                        "targets": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 4,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "dest": {
                                        "type": "string",
                                        "description": "a name from params (continuous ones only)",
                                    },
                                    "start": value,
                                    "end": value,
                                },
                                "required": ["dest", "start", "end"],
                            },
                        },
                    },
                    "required": ["knob", "targets"],
                },
            },
        },
        "required": ["name", "params", "mod_matrix"],
    }


def patch_from_design(answer: dict[str, Any]) -> dict[str, Any]:
    """The designer's answer -> arguments for create_synth_patch (macros keyed by knob, no preset)."""
    params = {k: max(0, min(127, int(v))) for k, v in (answer.get("params") or {}).items() if k in DESIGN_PARAMS}
    from circuit_tracks.constants import MACRO_DEST_BY_NAME  # noqa: PLC0415

    macros: dict[str, Any] = {}
    for m in answer.get("macros") or []:
        if not isinstance(m, dict) or not 1 <= int(m.get("knob") or 0) <= 8:
            continue
        # only continuous parameters can sit on a knob; a wrong name would reject the whole patch
        targets = [t for t in m.get("targets") or [] if isinstance(t, dict) and t.get("dest") in MACRO_DEST_BY_NAME]
        if targets:
            macros[str(m["knob"])] = {"targets": targets[:4]}
    patch = {"name": str(answer.get("name") or "Sound")[:16], "params": params, "macros": macros}
    if answer.get("mod_matrix"):
        patch["mod_matrix"] = answer["mod_matrix"]
    return with_default_macros(patch)


def default_macros(params: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """The device's standard knob layout, for a patch that arrives without macro routing.

    1 Oscillator, 2 Oscillator Mod, 3 Amp Envelope, 4 Filter Envelope, 5 Filter Frequency,
    6 Resonance, 7 Modulation, 8 FX (the same set as the library's presets).
    """
    wavetable = int((params or {}).get("osc1_wave", 0)) >= 14  # pulse width does nothing on a wavetable
    shape = "wave_interpolate" if wavetable else "pulse_width_index"

    def knob(*targets: tuple[str, int, int]) -> dict[str, Any]:
        return {"targets": [{"dest": d, "start": a, "end": b} for d, a, b in targets]}

    return {
        "1": knob((f"osc1_{shape}", 0, 127), (f"osc2_{shape}", 0, 127)),
        "2": knob(("osc1_density", 0, 80), ("osc1_density_detune", 0, 60)),
        "3": knob(("env1_attack", 0, 127), ("env1_release", 0, 127)),
        "4": knob(("env2_attack", 0, 100), ("env2_decay", 0, 127)),
        "5": knob(("filter_frequency", 0, 127)),
        "6": knob(("filter_resonance", 0, 127)),
        "7": knob(("osc2_cents", 52, 76)),
        "8": knob(("distortion_level", 0, 90), ("chorus_level", 0, 80)),
    }


def with_default_macros(patch: dict[str, Any]) -> dict[str, Any]:
    """Every knob does something: keep the designer's macros, fill the missing knobs from the standard set."""
    own = {str(k): v for k, v in (patch.get("macros") or {}).items() if isinstance(v, dict) and v.get("targets")}
    return {**patch, "macros": {**default_macros(patch.get("params")), **own}}


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
        for section in ("patch", "lookup_tables", "mod_matrix", "macros"):
            result = await backend.call("get_parameter_reference", {"section": section})
            if not result.is_error:
                reference[section] = result.text

    bridge.generate_prepare = prepare  # type: ignore[attr-defined]

    async def generate_track(args: dict[str, Any]) -> Any:
        track = str(args.get("track", "")).lower()
        if track not in SYNTH_TRACKS + DRUM_TRACKS:
            raise ArgumentError("Welche Spur? synth1, synth2, drum1 bis drum4, midi1 oder midi2.")
        brief = str(args.get("brief", "")).strip()
        if not brief:
            raise ArgumentError("Sag mir, wie die Spur klingen soll.")
        if ledger.song_json is None:
            raise ArgumentError("Es ist kein Song geladen. Sag 'neuer Song' oder lade einen.")
        if not args.get("confirmed"):
            return {
                "needs_confirmation": f"{track}: {brief[:60]} bauen lassen? Das dauert etwa zehn Sekunden und ersetzt "
                f"die Spur.",
                "hint": "Ask; on yes call generate_track again with confirmed=true.",
            }
        song = ledger.song_json
        pattern_name = str(args.get("pattern") or ledger.edit_pattern() or next(iter(song["patterns"])))
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

    async def design_patch(args: dict[str, Any]) -> Any:
        synth = _int_in_range(args, "synth", 1, 2, "Der Synth")
        brief = str(args.get("brief", "")).strip()
        if not brief:
            raise ArgumentError("Beschreib den Sound: zum Beispiel 'warmer, weicher Bass mit langsamem Filter'.")
        if not args.get("confirmed"):
            return {
                "needs_confirmation": f"Einen Sound für Synth {synth} entwerfen ({brief[:50]})? Das dauert etwa zehn "
                "Sekunden und ersetzt den aktuellen Sound auf dem Synth (nicht gespeichert).",
                "hint": "Ask; on yes call design_patch again with confirmed=true.",
            }
        schema = patch_schema or design_schema()
        system = PATCH_RULES + "".join(f"\n## Reference: {k}\n{v}" for k, v in reference.items())
        messages: list[tuple[str, str]] = [("user", f"Brief (German): {brief}\nReturn the patch as JSON.")]
        ledger.composer_job = brief
        try:
            for _attempt in range(3):
                answer = await model.generate(system, messages, schema)
                if not isinstance(answer, dict) or not answer.get("name") or not answer.get("params"):
                    messages.append(("user", "Rejected: a patch needs a name and params. Return corrected JSON."))
                    continue
                answer = patch_from_design(answer)
                result = await backend.call("create_synth_patch", {"synth": synth, **copy.deepcopy(answer)})
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
        ledger.designed_patch[synth] = answer  # the project save needs its macros and mod matrix
        ledger.selected_patch.pop(synth, None)
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
