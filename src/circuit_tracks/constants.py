"""Novation Circuit Tracks MIDI parameter mappings.

All values extracted from the Circuit Tracks Programmer's Reference Guide v3.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# MIDI channels (0-indexed for mido)
SYNTH1_CHANNEL = 0  # Channel 1
SYNTH2_CHANNEL = 1  # Channel 2
DRUMS_CHANNEL = 9  # Channel 10
MIDI1_CHANNEL = 2  # Channel 3
MIDI2_CHANNEL = 3  # Channel 4
PROJECT_CHANNEL = 15  # Channel 16

# Drum trigger notes (on channel 10)
DRUM_NOTES = {
    1: 60,
    2: 62,
    3: 64,
    4: 65,
}

# --- Synth Parameters (Channels 1 & 2) ---
# CC-based parameters: name -> cc_number
SYNTH_CC = {
    # Voice
    "polyphony_mode": 3,  # 0=Mono, 1=Mono AG, 2=Poly
    "portamento_rate": 5,
    "pre_glide": 9,  # 52-76 (-12 to 12), center=64
    "keyboard_octave": 13,  # 58-69 (-6 to 5), center=64
    # Oscillator 1
    "osc1_wave": 19,  # 0-29, see OSC_WAVEFORMS
    "osc1_wave_interpolate": 20,
    "osc1_pulse_width_index": 21,  # 0-127 (-64 to 63), center=127
    "osc1_virtual_sync_depth": 22,
    "osc1_density": 24,
    "osc1_density_detune": 25,
    "osc1_semitones": 26,  # 0-127 (-64 to 63), center=64
    "osc1_cents": 27,  # 0-127 (-64 to 63), center=64
    "osc1_pitchbend": 28,  # 52-76 (-12 to 12)
    # Oscillator 2
    "osc2_wave": 29,
    "osc2_wave_interpolate": 30,
    "osc2_pulse_width_index": 31,
    "osc2_virtual_sync_depth": 33,
    "osc2_density": 35,
    "osc2_density_detune": 36,
    "osc2_semitones": 37,
    "osc2_cents": 39,
    "osc2_pitchbend": 40,
    # Mixer
    "osc1_level": 51,
    "osc2_level": 52,
    "ring_mod_level": 54,
    "noise_level": 56,
    "pre_fx_level": 58,  # 52-82 (-12 to 18 dB), center=64
    "post_fx_level": 59,  # 52-82 (-12 to 18 dB), center=64
    # Filter
    "routing": 60,  # 0=Normal, 1=Osc1 bypass, 2=Both bypass
    "drive": 63,
    "drive_type": 65,  # 0-6, see DISTORTION_TYPES
    "filter_type": 68,  # 0-5, see FILTER_TYPES
    "filter_frequency": 74,
    "filter_tracking": 69,
    "filter_resonance": 71,
    "filter_q_normalize": 78,
    "env2_to_filter_freq": 79,  # 0-127 (-64 to 63), center=64
    # Envelope 1 (Amp)
    "env1_velocity": 108,  # 0-127 (-64 to 63), center=64
    "env1_attack": 73,
    "env1_decay": 75,
    "env1_sustain": 70,
    "env1_release": 72,
    # Effects
    "distortion_level": 91,
    "chorus_level": 93,
    # Macro knobs
    "macro_knob1": 80,
    "macro_knob2": 81,
    "macro_knob3": 82,
    "macro_knob4": 83,
    "macro_knob5": 84,
    "macro_knob6": 85,
    "macro_knob7": 86,
    "macro_knob8": 87,
}

# NRPN-based synth parameters: name -> (msb, lsb)
SYNTH_NRPN = {
    # Envelope 2 (Filter)
    "env2_velocity": (0, 0),
    "env2_attack": (0, 1),
    "env2_decay": (0, 2),
    "env2_sustain": (0, 3),
    "env2_release": (0, 4),
    # Envelope 3
    "env3_delay": (0, 14),
    "env3_attack": (0, 15),
    "env3_decay": (0, 16),
    "env3_sustain": (0, 17),
    "env3_release": (0, 18),
    # LFO 1
    "lfo1_waveform": (0, 70),  # 0-37, see LFO_WAVEFORMS
    "lfo1_phase_offset": (0, 71),  # 0-119 (0-357 degrees, steps of 3)
    "lfo1_slew_rate": (0, 72),
    "lfo1_delay": (0, 74),
    "lfo1_delay_sync": (0, 75),  # 0-35
    "lfo1_rate": (0, 76),
    "lfo1_rate_sync": (0, 77),  # 0-35
    "lfo1_one_shot": (0, 122),  # 12=OFF, 13=ON
    "lfo1_key_sync": (0, 122),  # 14=OFF, 15=ON
    "lfo1_common_sync": (0, 122),  # 16=OFF, 17=ON
    "lfo1_delay_trigger": (0, 122),  # 18=OFF, 19=ON
    "lfo1_fade_mode": (0, 123),  # 0-3
    # LFO 2
    "lfo2_waveform": (0, 79),
    "lfo2_phase_offset": (0, 80),
    "lfo2_slew_rate": (0, 81),
    "lfo2_delay": (0, 83),
    "lfo2_delay_sync": (0, 84),
    "lfo2_rate": (0, 85),
    "lfo2_rate_sync": (0, 86),
    "lfo2_one_shot": (0, 122),  # 22=OFF, 23=ON
    "lfo2_key_sync": (0, 122),  # 24=OFF, 25=ON
    "lfo2_common_sync": (0, 122),  # 26=OFF, 27=ON
    "lfo2_delay_trigger": (0, 122),  # 28=OFF, 29=ON
    "lfo2_fade_mode": (0, 123),  # 4-7
    # EQ
    "eq_bass_frequency": (0, 104),
    "eq_bass_level": (0, 105),
    "eq_mid_frequency": (0, 106),
    "eq_mid_level": (0, 107),
    "eq_treble_frequency": (0, 108),
    "eq_treble_level": (0, 109),
    # Distortion & Chorus details
    "distortion_type": (1, 0),  # 0-6, see DISTORTION_TYPES
    "distortion_compensation": (1, 1),
    "chorus_type": (1, 24),  # 0=Phaser, 1=Chorus
    "chorus_rate": (1, 25),
    "chorus_rate_sync": (1, 26),
    "chorus_feedback": (1, 27),
    "chorus_mod_depth": (1, 28),
    "chorus_delay": (1, 29),
    # Mod Matrix (slots 1-12, each has source1, source2, depth, destination)
    "mod1_source1": (1, 83),
    "mod1_source2": (1, 84),
    "mod1_depth": (1, 86),
    "mod1_destination": (1, 87),
    "mod2_source1": (1, 88),
    "mod2_source2": (1, 89),
    "mod2_depth": (1, 91),
    "mod2_destination": (1, 92),
    "mod3_source1": (1, 93),
    "mod3_source2": (1, 94),
    "mod3_depth": (1, 96),
    "mod3_destination": (1, 97),
    "mod4_source1": (1, 98),
    "mod4_source2": (1, 99),
    "mod4_depth": (1, 101),
    "mod4_destination": (1, 102),
    "mod5_source1": (1, 103),
    "mod5_source2": (1, 104),
    "mod5_depth": (1, 106),
    "mod5_destination": (1, 107),
    "mod6_source1": (1, 108),
    "mod6_source2": (1, 109),
    "mod6_depth": (1, 111),
    "mod6_destination": (1, 112),
    "mod7_source1": (1, 113),
    "mod7_source2": (1, 114),
    "mod7_depth": (1, 116),
    "mod7_destination": (1, 117),
    "mod8_source1": (1, 118),
    "mod8_source2": (1, 119),
    "mod8_depth": (1, 121),
    "mod8_destination": (1, 122),
    "mod9_source1": (1, 123),
    "mod9_source2": (1, 124),
    "mod9_depth": (1, 126),
    "mod9_destination": (1, 127),
    "mod10_source1": (2, 0),
    "mod10_source2": (2, 1),
    "mod10_depth": (2, 3),
    "mod10_destination": (2, 4),
    "mod11_source1": (2, 5),
    "mod11_source2": (2, 6),
    "mod11_depth": (2, 8),
    "mod11_destination": (2, 9),
    "mod12_source1": (2, 10),
    "mod12_source2": (2, 11),
    "mod12_depth": (2, 12),
    "mod12_destination": (2, 13),
}

# --- Drum Parameters (Channel 10) ---
# Per-drum CC mappings: drum_number -> {param_name: cc_number}
DRUM_CC = {
    1: {
        "patch_select": 8,
        "level": 12,
        "pitch": 14,
        "decay": 15,
        "distortion": 16,
        "eq": 17,
        "pan": 77,
    },
    2: {
        "patch_select": 18,
        "level": 23,
        "pitch": 34,
        "decay": 40,
        "distortion": 42,
        "eq": 43,
        "pan": 78,
    },
    3: {
        "patch_select": 44,
        "level": 45,
        "pitch": 46,
        "decay": 47,
        "distortion": 48,
        "eq": 49,
        "pan": 79,
    },
    4: {
        "patch_select": 50,
        "level": 53,
        "pitch": 55,
        "decay": 57,
        "distortion": 61,
        "eq": 76,
        "pan": 80,
    },
}

# --- Project Control (Channel 16) ---
PROJECT_CC = {
    # Reverb send levels
    "reverb_synth1_send": 88,
    "reverb_synth2_send": 89,
    "reverb_drum1_send": 90,
    "reverb_drum2_send": 106,
    "reverb_drum3_send": 109,
    "reverb_drum4_send": 110,
    # Delay send levels
    "delay_synth1_send": 111,
    "delay_synth2_send": 112,
    "delay_drum1_send": 113,
    "delay_drum2_send": 114,
    "delay_drum3_send": 115,
    "delay_drum4_send": 116,
    # Mixer
    "synth1_level": 12,
    "synth2_level": 14,
    "synth1_pan": 117,
    "synth2_pan": 118,
    # Master filter
    "master_filter_frequency": 74,  # 0-63=LP, 64=OFF, 65-127=HP
    "master_filter_resonance": 71,
}

PROJECT_NRPN = {
    # Reverb
    "reverb_type": (1, 18),  # 0-5: Chamber/Small Room/Large Room/Small Hall/Large Hall/Great Hall
    "reverb_decay": (1, 19),
    "reverb_damping": (1, 20),
    # FX bypass
    "fx_bypass": (1, 21),  # 0=Off (FX enabled), 1=On (FX disabled)
    # Delay
    "delay_time": (1, 6),
    "delay_time_sync": (1, 7),  # 0-35
    "delay_feedback": (1, 8),
    "delay_width": (1, 9),
    "delay_lr_ratio": (1, 10),  # 0-12: various ratios
    "delay_slew_rate": (1, 11),
    # Sidechain - Synth 1
    "sidechain_synth1_source": (2, 55),  # 0-4: Drum1-4, OFF
    "sidechain_synth1_attack": (2, 56),
    "sidechain_synth1_hold": (2, 57),
    "sidechain_synth1_decay": (2, 58),
    "sidechain_synth1_depth": (2, 59),
    # Sidechain - Synth 2
    "sidechain_synth2_source": (2, 65),
    "sidechain_synth2_attack": (2, 66),
    "sidechain_synth2_hold": (2, 67),
    "sidechain_synth2_decay": (2, 68),
    "sidechain_synth2_depth": (1, 69),
}

# --- Audio Input Control (Channel 16) ---
AUDIO_CC = {
    "audio1_level": 13,
    "audio2_level": 15,
    "audio1_reverb_level": 31,
    "audio2_reverb_level": 32,
    "audio1_delay_level": 33,
    "audio2_delay_level": 34,
    "audio1_pan": 35,
    "audio2_pan": 36,
}

# --- Lookup Tables ---

OSC_WAVEFORMS = {
    0: "sine",
    1: "triangle",
    2: "sawtooth",
    3: "saw 9:1 PW",
    4: "saw 8:2 PW",
    5: "saw 7:3 PW",
    6: "saw 6:4 PW",
    7: "saw 5:5 PW",
    8: "saw 4:6 PW",
    9: "saw 3:7 PW",
    10: "saw 2:8 PW",
    11: "saw 1:9 PW",
    12: "pulse width",
    13: "square",
    14: "sine table",
    15: "analogue pulse",
    16: "analogue sync",
    17: "triangle-saw blend",
    18: "digital nasty 1",
    19: "digital nasty 2",
    20: "digital saw-square",
    21: "digital vocal 1",
    22: "digital vocal 2",
    23: "digital vocal 3",
    24: "digital vocal 4",
    25: "digital vocal 5",
    26: "digital vocal 6",
    27: "random collection 1",
    28: "random collection 2",
    29: "random collection 3",
}

FILTER_TYPES = {
    0: "low pass 12dB",
    1: "low pass 24dB",
    2: "band pass 6/6 dB",
    3: "band pass 12/12 dB",
    4: "high pass 12dB",
    5: "high pass 24dB",
}

DISTORTION_TYPES = {
    0: "diode",
    1: "valve",
    2: "clipper",
    3: "cross-over",
    4: "rectifier",
    5: "bit reducer",
    6: "rate reducer",
}

LFO_WAVEFORMS = {
    0: "sine",
    1: "triangle",
    2: "sawtooth",
    3: "square",
    4: "random S/H",
    5: "time S/H",
    6: "piano envelope",
    7: "sequence 1",
    8: "sequence 2",
    9: "sequence 3",
    10: "sequence 4",
    11: "sequence 5",
    12: "sequence 6",
    13: "sequence 7",
    14: "alternative 1",
    15: "alternative 2",
    16: "alternative 3",
    17: "alternative 4",
    18: "alternative 5",
    19: "alternative 6",
    20: "alternative 7",
    21: "alternative 8",
    22: "chromatic",
    23: "chromatic 16",
    24: "major",
    25: "major 7",
    26: "minor 7",
    27: "min arp 1",
    28: "min arp 2",
    29: "diminished",
    30: "dec minor",
    31: "minor 3rd",
    32: "pedal",
    33: "4ths",
    34: "4ths x12",
    35: "1625 maj",
    36: "1625 min",
    37: "2511",
}

MOD_MATRIX_SOURCES = {
    0: "direct",
    4: "velocity",
    5: "keyboard",
    6: "LFO 1+",
    7: "LFO 1+/-",
    8: "LFO 2+",
    9: "LFO 2+/-",
    10: "env amp",
    11: "env filter",
    12: "env 3",
}

MOD_MATRIX_DESTINATIONS = {
    0: "osc 1 & 2 pitch",
    1: "osc 1 pitch",
    2: "osc 2 pitch",
    3: "osc 1 v-sync",
    4: "osc 2 v-sync",
    5: "osc 1 pulse width / index",
    6: "osc 2 pulse width / index",
    7: "osc 1 level",
    8: "osc 2 level",
    9: "noise level",
    10: "ring modulation 1*2 level",
    11: "filter drive amount",
    12: "filter frequency",
    13: "filter resonance",
    14: "LFO 1 rate",
    15: "LFO 2 rate",
    16: "amp envelope decay",
    17: "filter envelope decay",
}

# Macro knob destination index → parameter name
# Ordering follows the Novation Components UI layout, NOT patch byte addresses.
# Verified by dumping .syx patches with known macro assignments from hardware.
# dest 0 and 40,43 are unverified best-guesses (marked with ?).
MACRO_DESTINATIONS = {
    0: "pre_fx_level",  # ? (unverified, likely)
    1: "portamento_rate",  # confirmed
    2: "post_fx_level",  # confirmed
    3: "osc1_wave_interpolate",  # confirmed
    4: "osc1_pulse_width_index",  # confirmed
    5: "osc1_virtual_sync_depth",  # confirmed
    6: "osc1_density",  # confirmed
    7: "osc1_density_detune",  # confirmed
    8: "osc1_semitones",  # confirmed
    9: "osc1_cents",  # confirmed
    10: "osc2_wave_interpolate",  # confirmed
    11: "osc2_pulse_width_index",  # inferred (mirrors osc1)
    12: "osc2_virtual_sync_depth",  # confirmed
    13: "osc2_density",  # inferred (mirrors osc1)
    14: "osc2_density_detune",  # inferred (mirrors osc1)
    15: "osc2_semitones",  # confirmed
    16: "osc2_cents",  # confirmed
    17: "osc1_level",  # confirmed
    18: "osc2_level",  # confirmed
    19: "ring_mod_level",  # confirmed
    20: "noise_level",  # confirmed
    21: "filter_frequency",  # confirmed
    22: "filter_resonance",  # confirmed
    23: "drive",  # confirmed
    24: "filter_tracking",  # confirmed
    25: "env2_to_filter_freq",  # confirmed
    26: "env1_attack",  # confirmed
    27: "env1_decay",  # confirmed
    28: "env1_sustain",  # confirmed
    29: "env1_release",  # confirmed
    30: "env2_attack",  # confirmed
    31: "env2_decay",  # confirmed
    32: "env2_sustain",  # confirmed
    33: "env2_release",  # confirmed
    34: "env3_delay",  # confirmed
    35: "env3_attack",  # confirmed
    36: "env3_decay",  # confirmed
    37: "env3_sustain",  # confirmed
    38: "env3_release",  # confirmed
    39: "lfo1_rate",  # confirmed
    40: "lfo1_delay",  # ? (unverified, likely)
    41: "lfo1_slew_rate",  # confirmed
    42: "lfo2_rate",  # confirmed
    43: "lfo2_delay",  # ? (unverified, likely)
    44: "lfo2_slew_rate",  # confirmed
    45: "distortion_level",  # confirmed
    46: "chorus_level",  # confirmed
    47: "chorus_rate",  # confirmed
    48: "chorus_feedback",  # confirmed
    49: "chorus_mod_depth",  # confirmed
    50: "chorus_delay",  # confirmed
    51: "mod_matrix_1_depth",  # confirmed
    52: "mod_matrix_2_depth",  # confirmed
    53: "mod_matrix_3_depth",  # confirmed
    54: "mod_matrix_4_depth",  # confirmed
    55: "mod_matrix_5_depth",  # inferred (sequential)
    56: "mod_matrix_6_depth",
    57: "mod_matrix_7_depth",
    58: "mod_matrix_8_depth",
    59: "mod_matrix_9_depth",
    60: "mod_matrix_10_depth",
    61: "mod_matrix_11_depth",
    62: "mod_matrix_12_depth",
    63: "mod_matrix_13_depth",
    64: "mod_matrix_14_depth",
    65: "mod_matrix_15_depth",
    66: "mod_matrix_16_depth",
    67: "mod_matrix_17_depth",
    68: "mod_matrix_18_depth",
    69: "mod_matrix_19_depth",
    70: "mod_matrix_20_depth",
}

# Reverse lookups for name → index resolution
MACRO_DEST_BY_NAME = {v: k for k, v in MACRO_DESTINATIONS.items()}
MOD_SOURCE_BY_NAME = {v.lower(): k for k, v in MOD_MATRIX_SOURCES.items()}
MOD_DEST_BY_NAME = {v.lower(): k for k, v in MOD_MATRIX_DESTINATIONS.items()}
OSC_WAVEFORM_BY_NAME = {v.lower(): k for k, v in OSC_WAVEFORMS.items()}
FILTER_TYPE_BY_NAME = {v.lower(): k for k, v in FILTER_TYPES.items()}
DISTORTION_TYPE_BY_NAME = {v.lower(): k for k, v in DISTORTION_TYPES.items()}
LFO_WAVEFORM_BY_NAME = {v.lower(): k for k, v in LFO_WAVEFORMS.items()}

REVERB_TYPES = {
    0: "chamber",
    1: "small room",
    2: "large room",
    3: "small hall",
    4: "large hall",
    5: "great hall",
}

DELAY_LR_RATIOS = {
    0: "1:1",
    1: "4:3",
    2: "3:4",
    3: "3:2",
    4: "2:3",
    5: "2:1",
    6: "1:2",
    7: "3:1",
    8: "1:3",
    9: "4:1",
    10: "1:4",
    11: "1:OFF",
    12: "OFF:1",
}

# FX Preset tables — factory parameter values captured from hardware.
REVERB_PRESETS: dict[int, dict[str, int]] = {
    0: {"type": 0, "decay": 80, "damping": 120},
    1: {"type": 1, "decay": 90, "damping": 100},
    2: {"type": 2, "decay": 80, "damping": 80},
    3: {"type": 2, "decay": 100, "damping": 110},
    4: {"type": 3, "decay": 90, "damping": 100},
    5: {"type": 4, "decay": 105, "damping": 105},
    6: {"type": 5, "decay": 90, "damping": 80},
    7: {"type": 5, "decay": 120, "damping": 115},
}

DELAY_PRESETS: dict[int, dict[str, int]] = {
    0: {"time": 3, "sync": 0, "feedback": 100, "width": 115, "lr_ratio": 5, "slew": 115},
    1: {"time": 6, "sync": 0, "feedback": 45, "width": 104, "lr_ratio": 6, "slew": 26},
    2: {"time": 0, "sync": 2, "feedback": 63, "width": 62, "lr_ratio": 5, "slew": 40},
    3: {"time": 0, "sync": 4, "feedback": 25, "width": 10, "lr_ratio": 5, "slew": 75},
    4: {"time": 0, "sync": 5, "feedback": 59, "width": 15, "lr_ratio": 5, "slew": 39},
    5: {"time": 0, "sync": 7, "feedback": 15, "width": 34, "lr_ratio": 6, "slew": 56},
    6: {"time": 0, "sync": 7, "feedback": 75, "width": 115, "lr_ratio": 5, "slew": 98},
    7: {"time": 0, "sync": 7, "feedback": 75, "width": 75, "lr_ratio": 3, "slew": 23},
    8: {"time": 0, "sync": 8, "feedback": 80, "width": 10, "lr_ratio": 6, "slew": 68},
    9: {"time": 0, "sync": 9, "feedback": 50, "width": 100, "lr_ratio": 5, "slew": 33},
    10: {"time": 0, "sync": 10, "feedback": 82, "width": 23, "lr_ratio": 5, "slew": 56},
    11: {"time": 0, "sync": 10, "feedback": 78, "width": 88, "lr_ratio": 6, "slew": 47},
    12: {"time": 0, "sync": 10, "feedback": 33, "width": 127, "lr_ratio": 3, "slew": 33},
    13: {"time": 0, "sync": 11, "feedback": 50, "width": 60, "lr_ratio": 6, "slew": 86},
    14: {"time": 0, "sync": 12, "feedback": 24, "width": 90, "lr_ratio": 3, "slew": 106},
    15: {"time": 0, "sync": 12, "feedback": 50, "width": 115, "lr_ratio": 5, "slew": 111},
}

REVERB_PRESET_BY_NAME: dict[str, int] = {}
DELAY_PRESET_BY_NAME: dict[str, int] = {}

# SysEx constants
SYSEX_MANUFACTURER_ID = [0x00, 0x20, 0x29]  # Novation
SYSEX_PRODUCT_TYPE = 0x01  # Synth
SYSEX_PRODUCT_NUMBER = 0x64  # Circuit Tracks (100)

SYSEX_CMD_REPLACE_CURRENT_PATCH = 0x00
SYSEX_CMD_REPLACE_PATCH = 0x01
SYSEX_CMD_PATCH_DUMP_REQUEST = 0x40

SYSEX_LOCATION_SYNTH1 = 0x00
SYSEX_LOCATION_SYNTH2 = 0x01

SYNTH_PATCH_DATA_SIZE = 340  # bytes

# --- Factory Drum Sample Names ---
# Circuit Tracks ships with 64 factory samples per drum track.
# Samples are organized in 4 pages of 16 (kit pages).
# Each page follows the same structure:
#   Slots 1-2: Kicks, 3-4: Snares, 5-6: Closed Hi-Hats,
#   7-8: Open Hi-Hats, 9-12: Percussion, 13-16: Melodic
#
# patch_select CC range is 0-63: the CC value IS the sample index directly.
# (Confirmed in Programmer's Reference Guide v3, Drum Control table, p.12)
#
# These names are descriptive based on the factory kit layout.
# If you've loaded custom samples via Components, update this list to match.
FACTORY_DRUM_SAMPLES = {
    # Page 1 - Kit A
    0: "Kick A1",
    1: "Kick A2",
    2: "Snare A1",
    3: "Snare A2",
    4: "Closed HH A1",
    5: "Closed HH A2",
    6: "Open HH A1",
    7: "Open HH A2",
    8: "Perc A1",
    9: "Perc A2",
    10: "Perc A3",
    11: "Perc A4",
    12: "Melodic A1",
    13: "Melodic A2",
    14: "Melodic A3",
    15: "Melodic A4",
    # Page 2 - Kit B
    16: "Kick B1",
    17: "Kick B2",
    18: "Snare B1",
    19: "Snare B2",
    20: "Closed HH B1",
    21: "Closed HH B2",
    22: "Open HH B1",
    23: "Open HH B2",
    24: "Perc B1",
    25: "Perc B2",
    26: "Perc B3",
    27: "Perc B4",
    28: "Melodic B1",
    29: "Melodic B2",
    30: "Melodic B3",
    31: "Melodic B4",
    # Page 3 - Kit C
    32: "Kick C1",
    33: "Kick C2",
    34: "Snare C1",
    35: "Snare C2",
    36: "Closed HH C1",
    37: "Closed HH C2",
    38: "Open HH C1",
    39: "Open HH C2",
    40: "Perc C1",
    41: "Perc C2",
    42: "Perc C3",
    43: "Perc C4",
    44: "Melodic C1",
    45: "Melodic C2",
    46: "Melodic C3",
    47: "Melodic C4",
    # Page 4 - Kit D
    48: "Kick D1",
    49: "Kick D2",
    50: "Snare D1",
    51: "Snare D2",
    52: "Closed HH D1",
    53: "Closed HH D2",
    54: "Open HH D1",
    55: "Open HH D2",
    56: "Perc D1",
    57: "Perc D2",
    58: "Perc D3",
    59: "Perc D4",
    60: "Melodic D1",
    61: "Melodic D2",
    62: "Melodic D3",
    63: "Melodic D4",
}

# --- User-configurable sample map ---
SAMPLE_MAP_PATH = Path(os.path.expanduser("~/.config/circuit-mcp/drum_samples.json"))


def load_drum_sample_names() -> dict[int, str]:
    """Load drum sample names: user config > factory defaults.

    Returns a dict mapping sample index (0-63) to name string.
    User entries override factory defaults; factory fills gaps.
    """
    samples = dict(FACTORY_DRUM_SAMPLES)
    if SAMPLE_MAP_PATH.exists():
        try:
            data = json.loads(SAMPLE_MAP_PATH.read_text())
            for k, v in data.get("samples", {}).items():
                samples[int(k)] = str(v)
        except (json.JSONDecodeError, ValueError, OSError):
            pass
    return samples


def save_drum_sample_names(samples: dict[int, str]) -> Path:
    """Save drum sample names to the user config file.

    Args:
        samples: Dict mapping sample index (0-63) to name.

    Returns:
        Path to the saved config file.
    """
    SAMPLE_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Merge with existing file if present
    existing: dict[str, str] = {}
    if SAMPLE_MAP_PATH.exists():
        try:
            data = json.loads(SAMPLE_MAP_PATH.read_text())
            existing = data.get("samples", {})
        except (json.JSONDecodeError, ValueError, OSError):
            pass
    for k, v in samples.items():
        existing[str(k)] = v
    SAMPLE_MAP_PATH.write_text(json.dumps({"samples": existing}, indent=2) + "\n")
    return SAMPLE_MAP_PATH
