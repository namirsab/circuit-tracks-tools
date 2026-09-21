"""Instructions for the composer model (a text model, not the live voice)."""

from __future__ import annotations

from circuit_tracks.constants import DELAY_PRESET_NAMES, REVERB_PRESET_NAMES

HOUSE_RULES = """\
You compose songs for a Novation Circuit Tracks as JSON in the song format
below. The brief comes from a German-speaking musician; answer with JSON only,
no prose.

Hard rules (violations are rejected and sent back to you):
1. Every pattern in the song must have the same length: all 16 or all 32 steps.
   Prefer 32 steps; use micro-step positions (e.g. "0.5") only for automation.
2. Always include "sounds" with full patch configs for synth1 and synth2
   (use a preset name plus overrides when in doubt). Songs without sounds get
   init patches on the device.
3. Synth step automation uses the "macros" key; drum automation uses a
   track-level "params" key. Never invent other keys.
4. Mod matrix sources and destinations use space-separated names
   ("filter frequency"); synth parameters use snake_case ("filter_frequency").
5. "song" lists pattern names in playback order and may only reference
   patterns that exist. Keep names short, lowercase, ASCII (intro, chorus).
6. Gate values are at most 16. Note numbers are MIDI (60 = C4).
7. When revising: return the complete song, change only what the instruction
   asks for, keep every other pattern, track and sound byte-identical, and add
   new layers as new patterns rather than rewriting existing ones.
8. Macro targets are patch parameter names from the reference (for example
   filter_frequency, filter_resonance, osc1_wave_interpolate, distortion_level);
   there is no filter_drive. FX presets are named exactly: reverb one of
   {REVERB_PRESETS}; delay one of {DELAY_PRESETS}. Unknown names are dropped.

Musical guidance: match the requested tempo, key and mood; use all four drum
tracks and both synths unless told otherwise; give the arrangement a shape
(intro, main part, variation, outro) and keep each pattern loopable.
"""
HOUSE_RULES = HOUSE_RULES.replace("{REVERB_PRESETS}", ", ".join(REVERB_PRESET_NAMES))
HOUSE_RULES = HOUSE_RULES.replace("{DELAY_PRESETS}", ", ".join(DELAY_PRESET_NAMES))


def system_prompt(reference_sections: dict[str, str]) -> str:
    parts = [HOUSE_RULES]
    for name, text in reference_sections.items():
        parts.append(f"\n## Reference: {name}\n{text}")
    return "\n".join(parts)


def compose_prompt(brief: str) -> str:
    return f"Brief (German): {brief}\n\nReturn the complete song as JSON."


def revise_prompt(instruction: str, current_song_json: str, errors: str | None = None) -> str:
    text = (
        f"Current song (JSON):\n{current_song_json}\n\n"
        f"Revision request (German): {instruction}\n\n"
        "Return the complete revised song as JSON; change only what was asked."
    )
    if errors:
        text += f"\n\nYour previous answer was rejected: {errors}\nFix exactly that."
    return text


def retry_prompt(errors: str) -> str:
    return f"Your previous answer was rejected: {errors}\nReturn the corrected complete song as JSON."
