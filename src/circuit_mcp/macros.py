"""Default macro mapping for live performance.

Defines a fixed mapping from 8 macro knobs to synth parameters.
Each macro maps a knob position (0-127) to one or more parameter changes,
with configurable range (min/max) per parameter.

This provides a consistent performance interface regardless of which
patch is loaded on the Circuit Tracks.
"""

from dataclasses import dataclass


@dataclass
class MacroTarget:
    """A single parameter target for a macro knob."""
    param: str        # Parameter name (must exist in SYNTH_CC or SYNTH_NRPN)
    min_val: int = 0  # Value when knob is at 0
    max_val: int = 127  # Value when knob is at 127


# Default macro layout for live performance.
# Each macro (1-8) maps to a list of parameters it controls.
# When the knob moves from 0 to 127, each target param scales
# from its min_val to max_val.
DEFAULT_MACROS: dict[int, dict] = {
    1: {
        "name": "Filter",
        "targets": [
            MacroTarget(param="filter_frequency", min_val=0, max_val=127),
        ],
    },
    2: {
        "name": "Resonance",
        "targets": [
            MacroTarget(param="filter_resonance", min_val=0, max_val=127),
        ],
    },
    3: {
        "name": "Amp Envelope",
        "targets": [
            MacroTarget(param="env1_attack", min_val=0, max_val=127),
            MacroTarget(param="env1_release", min_val=10, max_val=127),
        ],
    },
    4: {
        "name": "Filter Envelope",
        "targets": [
            MacroTarget(param="env2_attack", min_val=0, max_val=127),
            MacroTarget(param="env2_decay", min_val=0, max_val=127),
        ],
    },
    5: {
        "name": "Distortion",
        "targets": [
            MacroTarget(param="distortion_level", min_val=0, max_val=127),
        ],
    },
    6: {
        "name": "Chorus",
        "targets": [
            MacroTarget(param="chorus_level", min_val=0, max_val=127),
        ],
    },
    7: {
        "name": "Osc Mix",
        "targets": [
            MacroTarget(param="osc1_level", min_val=127, max_val=0),
            MacroTarget(param="osc2_level", min_val=0, max_val=127),
        ],
    },
    8: {
        "name": "Drive",
        "targets": [
            MacroTarget(param="drive", min_val=0, max_val=127),
        ],
    },
}


def scale_value(knob: int, min_val: int, max_val: int) -> int:
    """Scale a knob position (0-127) to a target range."""
    knob = max(0, min(127, knob))
    return round(min_val + (max_val - min_val) * knob / 127)


def apply_macro(macro_num: int, knob_value: int, macros: dict | None = None) -> dict[str, int]:
    """Compute the parameter values for a macro knob position.

    Args:
        macro_num: Macro number (1-8).
        knob_value: Knob position (0-127).
        macros: Custom macro mapping. Uses DEFAULT_MACROS if None.

    Returns:
        Dict of param_name -> value to send.
    """
    if macros is None:
        macros = DEFAULT_MACROS

    macro = macros.get(macro_num)
    if macro is None:
        return {}

    params = {}
    for target in macro["targets"]:
        params[target.param] = scale_value(knob_value, target.min_val, target.max_val)
    return params
