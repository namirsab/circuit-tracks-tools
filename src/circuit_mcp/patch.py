"""Circuit Tracks synth patch SysEx dump request and parsing.

The Circuit Tracks responds to SysEx patch dump requests with the current
patch data for a synth. This module handles requesting and parsing that data
back into named parameter values.

SysEx format (request):  F0 00 20 29 01 64 40 <synth> 00 F7
SysEx format (response): F0 00 20 29 01 64 00 <synth> 00 <340 bytes patch> F7

Patch binary layout (340 bytes, from Programmer's Reference Guide v3):
  Bytes 0-15:   Patch name (16 ASCII chars, space-padded)
  Byte 16:      Category (0-14)
  Byte 17:      Genre (0-9)
  Bytes 18-31:  Reserved (14 bytes, all zeros)
  Bytes 32-123: Synth parameters (voice, osc, filter, env, LFO, FX, EQ)
  Bytes 124-203: Mod matrix (20 slots × 4 bytes)
  Bytes 204-339: Macro knobs (8 knobs × 17 bytes)
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

# Absolute byte offsets within the 340-byte patch binary.
# From the official Programmer's Reference Guide v3, Synth Patch Format table.
# name -> absolute patch address
_PARAM_OFFSETS: dict[str, int] = {
    # --- Voice (32-35) ---
    "polyphony_mode": 32,
    "portamento_rate": 33,
    "pre_glide": 34,
    "keyboard_octave": 35,

    # --- Oscillator 1 (36-44) ---
    "osc1_wave": 36,
    "osc1_wave_interpolate": 37,
    "osc1_pulse_width_index": 38,
    "osc1_virtual_sync_depth": 39,
    "osc1_density": 40,
    "osc1_density_detune": 41,
    "osc1_semitones": 42,
    "osc1_cents": 43,
    "osc1_pitchbend": 44,

    # --- Oscillator 2 (45-53) ---
    "osc2_wave": 45,
    "osc2_wave_interpolate": 46,
    "osc2_pulse_width_index": 47,
    "osc2_virtual_sync_depth": 48,
    "osc2_density": 49,
    "osc2_density_detune": 50,
    "osc2_semitones": 51,
    "osc2_cents": 52,
    "osc2_pitchbend": 53,

    # --- Mixer (54-59) ---
    "osc1_level": 54,
    "osc2_level": 55,
    "ring_mod_level": 56,
    "noise_level": 57,
    "pre_fx_level": 58,
    "post_fx_level": 59,

    # --- Filter (60-68) ---
    "routing": 60,
    "drive": 61,
    "drive_type": 62,
    "filter_type": 63,
    "filter_frequency": 64,
    "filter_tracking": 65,
    "filter_resonance": 66,
    "filter_q_normalize": 67,
    "env2_to_filter_freq": 68,

    # --- Envelope 1 / Amp (69-73) ---
    "env1_velocity": 69,
    "env1_attack": 70,
    "env1_decay": 71,
    "env1_sustain": 72,
    "env1_release": 73,

    # --- Envelope 2 / Filter (74-78) ---
    "env2_velocity": 74,
    "env2_attack": 75,
    "env2_decay": 76,
    "env2_sustain": 77,
    "env2_release": 78,

    # --- Envelope 3 (79-83) ---
    "env3_delay": 79,
    "env3_attack": 80,
    "env3_decay": 81,
    "env3_sustain": 82,
    "env3_release": 83,

    # --- LFO 1 (84-91) ---
    "lfo1_waveform": 84,
    "lfo1_phase_offset": 85,
    "lfo1_slew_rate": 86,
    "lfo1_delay": 87,
    "lfo1_delay_sync": 88,
    "lfo1_rate": 89,
    "lfo1_rate_sync": 90,
    "lfo1_flags": 91,  # bit0=OneShot, bit1=KeySync, bit2=CommonSync, bit3=DelayTrigger, bit4-5=FadeMode

    # --- LFO 2 (92-99) ---
    "lfo2_waveform": 92,
    "lfo2_phase_offset": 93,
    "lfo2_slew_rate": 94,
    "lfo2_delay": 95,
    "lfo2_delay_sync": 96,
    "lfo2_rate": 97,
    "lfo2_rate_sync": 98,
    "lfo2_flags": 99,  # same bitfield as LFO1, FadeMode bits use values 4-7

    # --- Effects (100-102) ---
    "distortion_level": 100,
    "chorus_level": 102,

    # --- Equaliser (105-110) ---
    "eq_bass_frequency": 105,
    "eq_bass_level": 106,
    "eq_mid_frequency": 107,
    "eq_mid_level": 108,
    "eq_treble_frequency": 109,
    "eq_treble_level": 110,

    # --- Distortion & Chorus details (116-123) ---
    "distortion_type": 116,
    "distortion_compensation": 117,
    "chorus_type": 118,
    "chorus_rate": 119,
    "chorus_rate_sync": 120,
    "chorus_feedback": 121,
    "chorus_mod_depth": 122,
    "chorus_delay": 123,
}

# Add mod matrix slots (20 slots × 4 bytes at addresses 124-203)
for _slot in range(1, 21):
    _base = 124 + (_slot - 1) * 4
    _PARAM_OFFSETS[f"mod{_slot}_source1"] = _base
    _PARAM_OFFSETS[f"mod{_slot}_source2"] = _base + 1
    _PARAM_OFFSETS[f"mod{_slot}_depth"] = _base + 2
    _PARAM_OFFSETS[f"mod{_slot}_destination"] = _base + 3

# Backward-compatible alias: old code used _PARAM_BYTE_OFFSETS with offsets
# relative to byte 18. Keep this for modify_patch_bytes() which adds
# _PATCH_PARAMS_OFFSET (18) before writing.
_PARAM_BYTE_OFFSETS = {name: addr - _PATCH_PARAMS_OFFSET for name, addr in _PARAM_OFFSETS.items()}


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

    # Extract parameter data using absolute addresses
    params = {}
    for param_name, addr in _PARAM_OFFSETS.items():
        if addr < len(patch_bytes):
            params[param_name] = patch_bytes[addr]

    # Raw hex of the parameter region for inspection
    raw_param_hex = " ".join(f"{b:02x}" for b in patch_bytes[_PATCH_PARAMS_OFFSET:_PATCH_PARAMS_OFFSET + 100])

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


def save_patch_to_slot(midi: MidiConnection, synth: int, slot: int, patch_bytes: list[int]) -> None:
    """Save a patch to a numbered slot in flash memory.

    Args:
        midi: Connected MidiConnection.
        synth: Synth number (1 or 2).
        slot: Patch slot number (0-63).
        patch_bytes: The 340-byte patch binary.
    """
    if len(patch_bytes) != _PATCH_SIZE:
        raise ValueError(f"Patch must be {_PATCH_SIZE} bytes, got {len(patch_bytes)}")
    if not 0 <= slot <= 63:
        raise ValueError(f"Slot must be 0-63, got {slot}")

    # Replace Patch format (from Programmer's Reference):
    # header + cmd(0x01) + patch_number(0-63) + reserved(0) + 340 bytes
    # Note: no synth location byte — patches are stored in a shared bank.
    _CMD_REPLACE_PATCH = 0x01
    sysex_data = _SYSEX_HEADER + [_CMD_REPLACE_PATCH, slot, 0] + patch_bytes
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
