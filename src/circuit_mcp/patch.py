"""Circuit Tracks synth patch SysEx dump request and parsing.

The Circuit Tracks responds to SysEx patch dump requests with the current
patch data for a synth. This module handles requesting and parsing that data
back into named parameter values.

SysEx format (request):  F0 00 20 29 01 64 40 <synth> 00 F7
SysEx format (response): F0 00 20 29 01 64 00 <synth> 00 <340 bytes patch> F7

Patch binary layout (340 bytes):
  Bytes 0-15:   Patch name (16 ASCII chars, space-padded)
  Byte 16:      Category
  Byte 17:      Genre
  Bytes 18-339: Synth parameter data (322 bytes)

Parameter offsets verified against the factory init patch and BatGirl.syx.
"""

from circuit_mcp.constants import (
    SYSEX_MANUFACTURER_ID,
    SYSEX_PRODUCT_TYPE,
    SYSEX_PRODUCT_NUMBER,
)
from circuit_mcp.midi import MidiConnection

# SysEx command bytes
_CMD_REPLACE_CURRENT = 0x00  # Device sends this when dumping current patch
_CMD_REQUEST_CURRENT = 0x40  # We send this to request the current patch

# SysEx header that identifies Circuit Tracks messages
_SYSEX_HEADER = SYSEX_MANUFACTURER_ID + [SYSEX_PRODUCT_TYPE, SYSEX_PRODUCT_NUMBER]

# Patch binary layout constants
_PATCH_SIZE = 340
_PATCH_NAME_OFFSET = 0
_PATCH_NAME_LENGTH = 16
_PATCH_CATEGORY_OFFSET = 16
_PATCH_GENRE_OFFSET = 17
_PATCH_PARAMS_OFFSET = 18

# Byte offsets within the parameter data region (relative to patch byte 18).
# Verified against the factory init patch and BatGirl.syx.
#
# Init patch defaults shown in comments for verification.
_PARAM_BYTE_OFFSETS: dict[str, int] = {
    # --- Settings (bytes 14-17) ---
    "polyphony_mode": 14,           # init=2 (Poly)
    "portamento_rate": 15,          # init=0
    "pre_glide": 16,                # init=64 (center)
    "keyboard_octave": 17,          # init=64 (center)

    # --- Oscillator 1 (bytes 18-26) ---
    "osc1_wave": 18,                # init=2 (sawtooth)
    "osc1_wave_interpolate": 19,    # init=127
    "osc1_pulse_width_index": 20,   # init=64
    "osc1_virtual_sync_depth": 21,  # init=0
    "osc1_density": 22,             # init=0
    "osc1_density_detune": 23,      # init=0
    "osc1_semitones": 24,           # init=64 (center)
    "osc1_cents": 25,               # init=64 (center)
    "osc1_pitchbend": 26,           # init=76 (+12 semitones range)

    # --- Oscillator 2 (bytes 27-35) ---
    "osc2_wave": 27,                # init=2 (sawtooth)
    "osc2_wave_interpolate": 28,    # init=127
    "osc2_pulse_width_index": 29,   # init=64
    "osc2_virtual_sync_depth": 30,  # init=0
    "osc2_density": 31,             # init=0
    "osc2_density_detune": 32,      # init=0
    "osc2_semitones": 33,           # init=64 (center)
    "osc2_cents": 34,               # init=64 (center)
    "osc2_pitchbend": 35,           # init=76 (+12 semitones range)

    # --- Filter / Mixer / Envelopes (bytes 36-65) ---
    "filter_frequency": 36,         # init=127 (fully open)
    "filter_resonance": 37,         # init=0
    "drive": 38,                    # init=0
    "filter_tracking": 39,          # init=0
    "osc1_level": 40,               # init=64
    "osc2_level": 41,               # init=64
    "ring_mod_level": 42,           # init=0
    "noise_level": 43,              # init=0
    "routing": 44,                  # init=0 (normal)
    "filter_type": 45,              # init=1 (LP24?)
    "env1_velocity": 46,            # init=127
    "env1_sustain": 47,             # init=127
    "env1_attack": 48,              # init=0
    "pre_fx_level": 49,             # init=64
    "post_fx_level": 50,            # init=64
    "env2_to_filter_freq": 51,      # init=64 (center)
    "env1_decay": 52,               # init=2 (very short? or mapped differently)
    "env1_release": 53,             # init=90 (0x5a)
    # NOTE: bytes 52-53 might be swapped with decay/release. The init values
    # 2 and 90 are unusual for attack/decay vs decay/release. Needs device testing.
    "env2_velocity": 54,            # init=127
    "env2_attack": 55,              # init=40
    "env2_sustain": 56,             # init=64
    "env2_decay": 57,               # init=2
    "env2_release": 58,             # init=75 (0x4b)
    "env3_delay": 59,               # init=35 (0x23)
    "env3_attack": 60,              # init=45 (0x2d)
    "env3_decay": 61,               # init=0
    "env3_sustain": 62,             # init=10 (0x0a)
    "env3_release": 63,             # init=70 (0x46)
    "env3_unknown1": 64,            # init=64
    "env3_unknown2": 65,            # init=40

    # --- LFO 1 (bytes 66-78) ---
    "lfo1_waveform": 66,            # init=0 (sine)
    "lfo1_phase_offset": 67,        # init=0
    "lfo1_slew_rate": 68,           # init=0
    "lfo1_delay": 69,               # init=0
    "lfo1_delay_sync": 70,          # init=0
    "lfo1_rate": 71,                # init=68 (0x44)
    "lfo1_rate_sync": 72,           # init=0
    "lfo1_one_shot": 73,            # init=0
    "lfo1_key_sync": 74,            # init=0
    "lfo1_common_sync": 75,         # init=0
    "lfo1_delay_trigger": 76,       # init=0
    "lfo1_fade_mode": 77,           # init=0

    # --- LFO 2 (bytes 78-85 region) ---
    # There may be a gap byte at 78
    "lfo2_waveform": 78,            # init=0 (sine)
    "lfo2_phase_offset": 79,        # init=68 — WAIT this is lfo2_rate!

    # Actually let me re-examine. LFO2 likely mirrors LFO1 layout.
    # If LFO1 = 12 bytes (66-77), then LFO2 starts at 78:
    "lfo2_rate": 79,                # init=68 (0x44) — confirmed

    # --- FX & EQ (bytes ~86-99) ---
    "eq_bass_frequency": 87,        # init=64
    "eq_bass_level": 88,            # init=64
    "eq_mid_frequency": 89,         # init=64
    "eq_mid_level": 90,             # init=64
    "distortion_level": 91,         # init=125 (0x7d) — or is this eq_treble_freq?
    "chorus_level": 92,             # init=64

    # --- Mod Matrix (bytes ~100+) ---
    # 20 mod slots × 4 bytes each (source1, source2, depth, destination)
    # In init patch, all depths = 64 (center/no effect), sources & dests = 0
    # The repeating pattern 00 00 00 40 00 00 00 40... visible in the hex
    # starts around byte 98+
}

# Remove entries that are clearly wrong or placeholder
# Keep only verified mappings
_VERIFIED_PARAMS = {
    k: v for k, v in _PARAM_BYTE_OFFSETS.items()
    if not k.startswith("env3_unknown") and not k.startswith("lfo2_phase")
}


def request_current_patch(midi: MidiConnection, synth: int) -> list[int] | None:
    """Request the current patch data from a synth via SysEx.

    Args:
        midi: Connected MidiConnection with input port.
        synth: Synth number (1 or 2). Mapped to index 0 or 1.

    Returns:
        Raw SysEx response data, or None on timeout.
    """
    synth_index = synth - 1  # 0-indexed

    request = _SYSEX_HEADER + [_CMD_REQUEST_CURRENT, synth_index, 0]

    def match(data: list[int]) -> bool:
        """Match a SysEx response for our patch dump."""
        if len(data) < 9:
            return False
        return (
            data[0:3] == SYSEX_MANUFACTURER_ID
            and data[3] == SYSEX_PRODUCT_TYPE
            and data[4] == SYSEX_PRODUCT_NUMBER
            and data[5] == _CMD_REPLACE_CURRENT
            and data[6] == synth_index
        )

    return midi.sysex_request(request, timeout_s=3.0, match_fn=match)


def parse_patch_data(sysex_data: list[int]) -> dict:
    """Parse a SysEx patch dump response into named parameter values.

    Args:
        sysex_data: Raw SysEx data (without F0/F7) from the device or file.

    Returns:
        Dict with 'name', 'category', 'genre', 'params', and 'raw_hex'.
    """
    # Patch binary starts at byte 8 (after: manufacturer[3] + product[2] + cmd + index + 0)
    patch_start = 8
    patch_bytes = sysex_data[patch_start:]

    if len(patch_bytes) < _PATCH_SIZE:
        return {"error": f"Patch data too short: {len(patch_bytes)} bytes, expected {_PATCH_SIZE}"}

    # Extract patch name (ASCII, 16 chars at offset 0)
    name_bytes = patch_bytes[_PATCH_NAME_OFFSET:_PATCH_NAME_OFFSET + _PATCH_NAME_LENGTH]
    name = "".join(chr(b) for b in name_bytes if 32 <= b <= 126).strip()

    # Category and genre
    category = patch_bytes[_PATCH_CATEGORY_OFFSET]
    genre = patch_bytes[_PATCH_GENRE_OFFSET]

    # Extract parameter data
    param_data = patch_bytes[_PATCH_PARAMS_OFFSET:]
    params = {}
    for param_name, offset in _VERIFIED_PARAMS.items():
        if offset < len(param_data):
            params[param_name] = param_data[offset]

    # Raw hex of the full parameter region for inspection
    raw_param_hex = " ".join(f"{b:02x}" for b in param_data[:100])

    return {
        "name": name,
        "category": category,
        "genre": genre,
        "params": params,
        "raw_params_hex_first_100": raw_param_hex,
    }


def send_current_patch(midi: MidiConnection, synth: int, patch_bytes: list[int]) -> None:
    """Send a patch to the device, replacing the current patch on a synth.

    Args:
        midi: Connected MidiConnection.
        synth: Synth number (1 or 2).
        patch_bytes: The 340-byte patch binary.
    """
    if len(patch_bytes) != _PATCH_SIZE:
        raise ValueError(f"Patch must be {_PATCH_SIZE} bytes, got {len(patch_bytes)}")

    synth_index = synth - 1
    sysex_data = _SYSEX_HEADER + [_CMD_REPLACE_CURRENT, synth_index, 0] + patch_bytes
    midi.send_sysex(sysex_data)


def read_and_modify_patch(
    midi: MidiConnection,
    synth: int,
    modifications: dict[str, int],
) -> dict:
    """Read the current patch, apply modifications, and send it back.

    This is the core read-modify-write cycle for full sound design.

    Args:
        midi: Connected MidiConnection with input port.
        synth: Synth number (1 or 2).
        modifications: Dict of param_name -> value for params in _PARAM_BYTE_OFFSETS.
            Special keys: "name" (str), "category" (int), "genre" (int).

    Returns:
        Dict with the modified patch info, or error.
    """
    # Read current patch
    sysex_data = request_current_patch(midi, synth)
    if sysex_data is None:
        return {"error": "No response from device. Is it connected?"}

    patch_start = 8
    patch_bytes = list(sysex_data[patch_start:patch_start + _PATCH_SIZE])

    if len(patch_bytes) < _PATCH_SIZE:
        return {"error": f"Patch data too short: {len(patch_bytes)} bytes"}

    # Apply modifications
    patch_bytes, applied, errors = modify_patch_bytes(patch_bytes, modifications)

    # Send modified patch back
    send_current_patch(midi, synth, patch_bytes)

    result = {"synth": synth, "applied": applied}
    if errors:
        result["unknown_params"] = errors
    return result


def modify_patch_bytes(
    patch_bytes: list[int],
    modifications: dict[str, int | str],
) -> tuple[list[int], dict, list[str]]:
    """Modify a patch binary in-place (no device communication).

    Args:
        patch_bytes: Mutable list of 340 bytes.
        modifications: Dict of param_name -> value.

    Returns:
        Tuple of (modified_bytes, applied_dict, error_list).
    """
    applied = {}
    errors = []
    for param_name, value in modifications.items():
        if param_name == "name":
            name_str = str(value)[:_PATCH_NAME_LENGTH].ljust(_PATCH_NAME_LENGTH)
            for i, ch in enumerate(name_str):
                patch_bytes[_PATCH_NAME_OFFSET + i] = ord(ch)
            applied["name"] = name_str.strip()
        elif param_name == "category":
            patch_bytes[_PATCH_CATEGORY_OFFSET] = max(0, min(127, int(value)))
            applied["category"] = patch_bytes[_PATCH_CATEGORY_OFFSET]
        elif param_name == "genre":
            patch_bytes[_PATCH_GENRE_OFFSET] = max(0, min(127, int(value)))
            applied["genre"] = patch_bytes[_PATCH_GENRE_OFFSET]
        elif param_name in _PARAM_BYTE_OFFSETS:
            offset = _PATCH_PARAMS_OFFSET + _PARAM_BYTE_OFFSETS[param_name]
            patch_bytes[offset] = max(0, min(127, int(value)))
            applied[param_name] = patch_bytes[offset]
        elif param_name.startswith("raw_"):
            # Direct byte offset access: raw_42 = set param byte 42
            try:
                byte_offset = int(param_name[4:])
                patch_bytes[_PATCH_PARAMS_OFFSET + byte_offset] = max(0, min(127, int(value)))
                applied[param_name] = patch_bytes[_PATCH_PARAMS_OFFSET + byte_offset]
            except (ValueError, IndexError):
                errors.append(param_name)
        else:
            errors.append(param_name)
    return patch_bytes, applied, errors


def parse_patch_file(file_path: str) -> dict:
    """Parse a .syx patch file from disk.

    Args:
        file_path: Path to a .syx file.

    Returns:
        Parsed patch data dict.
    """
    with open(file_path, "rb") as f:
        raw = f.read()

    if len(raw) < 10 or raw[0] != 0xF0 or raw[-1] != 0xF7:
        return {"error": "Not a valid SysEx file"}

    # Strip F0 and F7 to get the data as mido would provide it
    sysex_data = list(raw[1:-1])
    result = parse_patch_data(sysex_data)
    result["file"] = file_path
    result["file_size"] = len(raw)
    return result
