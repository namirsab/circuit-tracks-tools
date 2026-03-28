"""Synth patch builder for Novation Circuit Tracks.

Builds complete 340-byte synth patches from scratch with a fluent API.
All addresses and defaults from the official Programmer's Reference Guide v3.

Important macro behavior (verified on hardware):
  - Macros ADD to the parameter's base value, they don't replace it.
    Set base values at a usable midpoint (e.g. filter_frequency=60) so
    the sound is immediately playable, while leaving room for the macro
    to sweep. Avoid base=0 for filter as it mutes the sound completely.
  - wave_interpolate only works when the oscillator uses a wavetable
    waveform (wave >= 14). With basic waveforms (0-13), use
    pulse_width_index or virtual_sync_depth for macro knob 1 instead.

Usage:
    patch = (PatchBuilder("MyPad")
        .osc1(wave=2, density=20, density_detune=30)
        .osc2(wave=2, semitones=66, cents=70)
        .filter(frequency=0, resonance=0, filter_type=1)
        .env_amp(attack=60, decay=90, sustain=127, release=80)
        .env_filter(attack=10, decay=75, sustain=35, release=45)
        .mixer(osc1_level=100, osc2_level=90)
        .chorus(level=0)
        .add_mod("LFO 1+", "filter frequency", depth=80)
        .set_macro(5, [{"dest": "filter_frequency", "start": 0, "end": 127}])
        .set_macro(8, [{"dest": 46, "start": 0, "end": 80}])
        .build())
"""

from __future__ import annotations

from circuit_mcp.constants import (
    MACRO_DESTINATIONS,
    MACRO_DEST_BY_NAME,
    MOD_MATRIX_SOURCES,
    MOD_MATRIX_DESTINATIONS,
    MOD_SOURCE_BY_NAME,
    MOD_DEST_BY_NAME,
    SYSEX_MANUFACTURER_ID,
    SYSEX_PRODUCT_TYPE,
    SYSEX_PRODUCT_NUMBER,
)

# --- Patch format constants ---

PATCH_SIZE = 340
_NAME_LEN = 16

# Init patch template: 340 bytes with all official defaults.
# Built from the Synth Patch Format table in Programmer's Reference Guide v3.
_INIT_PATCH = bytearray(PATCH_SIZE)

# Patch name: "Init" padded with spaces
_init_name = b"Init            "
_INIT_PATCH[0:16] = _init_name
# Category=0, Genre=0 (already zero)
# Reserved bytes 18-31 (already zero)

# Voice (32-35)
_INIT_PATCH[32] = 2    # PolyphonyMode (Poly)
_INIT_PATCH[33] = 0    # PortamentoRate
_INIT_PATCH[34] = 64   # PreGlide (center)
_INIT_PATCH[35] = 64   # KeyboardOctave (center)

# Osc1 (36-44)
_INIT_PATCH[36] = 2    # Wave (sawtooth)
_INIT_PATCH[37] = 127  # WaveInterpolate
_INIT_PATCH[38] = 64   # PulseWidthIndex
_INIT_PATCH[42] = 64   # Semitones (center)
_INIT_PATCH[43] = 64   # Cents (center)
_INIT_PATCH[44] = 76   # PitchBend (+12)

# Osc2 (45-53)
_INIT_PATCH[45] = 2    # Wave (sawtooth)
_INIT_PATCH[46] = 127  # WaveInterpolate
_INIT_PATCH[47] = 64   # PulseWidthIndex
_INIT_PATCH[51] = 64   # Semitones (center)
_INIT_PATCH[52] = 64   # Cents (center)
_INIT_PATCH[53] = 76   # PitchBend (+12)

# Mixer (54-59)
_INIT_PATCH[54] = 127  # Osc1Level
_INIT_PATCH[58] = 64   # PreFXLevel (center)
_INIT_PATCH[59] = 64   # PostFXLevel (center)

# Filter (60-68)
_INIT_PATCH[63] = 1    # Type (LP24)
_INIT_PATCH[64] = 127  # Frequency (fully open)
_INIT_PATCH[67] = 64   # QNormalise
_INIT_PATCH[68] = 64   # Env2ToFreq (center)

# Envelope 1 / Amp (69-73)
_INIT_PATCH[69] = 64   # Velocity (center)
_INIT_PATCH[70] = 2    # Attack
_INIT_PATCH[71] = 90   # Decay
_INIT_PATCH[72] = 127  # Sustain
_INIT_PATCH[73] = 40   # Release

# Envelope 2 / Filter (74-78)
_INIT_PATCH[74] = 64   # Velocity (center)
_INIT_PATCH[75] = 2    # Attack
_INIT_PATCH[76] = 75   # Decay
_INIT_PATCH[77] = 35   # Sustain
_INIT_PATCH[78] = 45   # Release

# Envelope 3 (79-83)
_INIT_PATCH[80] = 10   # Attack
_INIT_PATCH[81] = 70   # Decay
_INIT_PATCH[82] = 64   # Sustain
_INIT_PATCH[83] = 40   # Release

# LFO 1 (84-91)
_INIT_PATCH[89] = 68   # Rate

# LFO 2 (92-99)
_INIT_PATCH[97] = 68   # Rate

# EQ (105-110)
_INIT_PATCH[105] = 64  # BassFrequency
_INIT_PATCH[106] = 64  # BassLevel
_INIT_PATCH[107] = 64  # MidFrequency
_INIT_PATCH[108] = 64  # MidLevel
_INIT_PATCH[109] = 125 # TrebleFrequency
_INIT_PATCH[110] = 64  # TrebleLevel

# Distortion & Chorus details (116-123)
_INIT_PATCH[117] = 100 # Distortion_Compensation
_INIT_PATCH[118] = 1   # Chorus_Type (Chorus)
_INIT_PATCH[119] = 20  # Chorus_Rate
_INIT_PATCH[121] = 74  # Chorus_Feedback
_INIT_PATCH[122] = 64  # Chorus_ModDepth
_INIT_PATCH[123] = 64  # Chorus_Delay

# Mod matrix (124-203): all depths default to 64
for _s in range(20):
    _INIT_PATCH[124 + _s * 4 + 2] = 64  # depth

# Macro knobs (204-339): all EndPos=127, Depth=64
for _k in range(8):
    _kb = 204 + _k * 17
    for _t in range(4):
        _tb = _kb + 1 + _t * 4
        _INIT_PATCH[_tb + 2] = 127  # EndPos
        _INIT_PATCH[_tb + 3] = 64   # Depth

_INIT_PATCH = bytes(_INIT_PATCH)

# Mod matrix address range
_MOD_MATRIX_START = 124
_MOD_MATRIX_SLOTS = 20

# Macro address range
_MACRO_START = 204
_MACRO_COUNT = 8
_MACRO_SIZE = 17  # 1 (position) + 4 * 4 (targets)
_MACRO_TARGETS = 4


def _resolve_mod_source(s: int | str) -> int:
    if isinstance(s, int):
        return s
    key = s.lower()
    if key in MOD_SOURCE_BY_NAME:
        return MOD_SOURCE_BY_NAME[key]
    raise ValueError(f"Unknown mod source: {s!r}. Valid: {list(MOD_MATRIX_SOURCES.values())}")


def _resolve_mod_dest(d: int | str) -> int:
    if isinstance(d, int):
        return d
    key = d.lower()
    if key in MOD_DEST_BY_NAME:
        return MOD_DEST_BY_NAME[key]
    raise ValueError(f"Unknown mod destination: {d!r}. Valid: {list(MOD_MATRIX_DESTINATIONS.values())}")


def _resolve_macro_dest(d: int | str) -> int:
    if isinstance(d, int):
        return d
    if d in MACRO_DEST_BY_NAME:
        return MACRO_DEST_BY_NAME[d]
    raise ValueError(f"Unknown macro destination: {d!r}. Valid: {list(MACRO_DESTINATIONS.values())}")


def _clamp(val: int, lo: int = 0, hi: int = 127) -> int:
    return max(lo, min(hi, val))


class PatchBuilder:
    """Fluent builder for Circuit Tracks synth patches."""

    def __init__(self, name: str = "Init"):
        self._bytes = bytearray(_INIT_PATCH)
        self._mod_slot_cursor = 0
        if name != "Init":
            self.name(name)

    def _set(self, addr: int, val: int) -> PatchBuilder:
        self._bytes[addr] = _clamp(val)
        return self

    def name(self, n: str) -> PatchBuilder:
        encoded = n.encode("ascii", errors="replace")[:_NAME_LEN].ljust(_NAME_LEN)
        self._bytes[0:_NAME_LEN] = encoded
        return self

    def category(self, c: int) -> PatchBuilder:
        self._bytes[16] = _clamp(c, 0, 14)
        return self

    def genre(self, g: int) -> PatchBuilder:
        self._bytes[17] = _clamp(g, 0, 9)
        return self

    # --- Voice ---

    def voice(self, polyphony: int | None = None, portamento: int | None = None,
              pre_glide: int | None = None, octave: int | None = None) -> PatchBuilder:
        if polyphony is not None:
            self._bytes[32] = _clamp(polyphony, 0, 2)
        if portamento is not None:
            self._set(33, portamento)
        if pre_glide is not None:
            self._bytes[34] = _clamp(pre_glide, 52, 76)
        if octave is not None:
            self._bytes[35] = _clamp(octave, 58, 69)
        return self

    # --- Oscillators ---

    def osc1(self, wave: int | None = None, interpolate: int | None = None,
             pulse_width: int | None = None, sync_depth: int | None = None,
             density: int | None = None, density_detune: int | None = None,
             semitones: int | None = None, cents: int | None = None,
             pitchbend: int | None = None) -> PatchBuilder:
        if wave is not None:
            self._bytes[36] = _clamp(wave, 0, 29)
        if interpolate is not None:
            self._set(37, interpolate)
        if pulse_width is not None:
            self._set(38, pulse_width)
        if sync_depth is not None:
            self._set(39, sync_depth)
        if density is not None:
            self._set(40, density)
        if density_detune is not None:
            self._set(41, density_detune)
        if semitones is not None:
            self._set(42, semitones)
        if cents is not None:
            self._set(43, cents)
        if pitchbend is not None:
            self._bytes[44] = _clamp(pitchbend, 52, 76)
        return self

    def osc2(self, wave: int | None = None, interpolate: int | None = None,
             pulse_width: int | None = None, sync_depth: int | None = None,
             density: int | None = None, density_detune: int | None = None,
             semitones: int | None = None, cents: int | None = None,
             pitchbend: int | None = None) -> PatchBuilder:
        if wave is not None:
            self._bytes[45] = _clamp(wave, 0, 29)
        if interpolate is not None:
            self._set(46, interpolate)
        if pulse_width is not None:
            self._set(47, pulse_width)
        if sync_depth is not None:
            self._set(48, sync_depth)
        if density is not None:
            self._set(49, density)
        if density_detune is not None:
            self._set(50, density_detune)
        if semitones is not None:
            self._set(51, semitones)
        if cents is not None:
            self._set(52, cents)
        if pitchbend is not None:
            self._bytes[53] = _clamp(pitchbend, 52, 76)
        return self

    # --- Mixer ---

    def mixer(self, osc1_level: int | None = None, osc2_level: int | None = None,
              ring_mod: int | None = None, noise: int | None = None,
              pre_fx: int | None = None, post_fx: int | None = None) -> PatchBuilder:
        if osc1_level is not None:
            self._set(54, osc1_level)
        if osc2_level is not None:
            self._set(55, osc2_level)
        if ring_mod is not None:
            self._set(56, ring_mod)
        if noise is not None:
            self._set(57, noise)
        if pre_fx is not None:
            self._bytes[58] = _clamp(pre_fx, 52, 82)
        if post_fx is not None:
            self._bytes[59] = _clamp(post_fx, 52, 82)
        return self

    # --- Filter ---

    def filter(self, frequency: int | None = None, resonance: int | None = None,
               drive: int | None = None, drive_type: int | None = None,
               filter_type: int | None = None, routing: int | None = None,
               tracking: int | None = None, q_normalize: int | None = None,
               env2_to_freq: int | None = None) -> PatchBuilder:
        if routing is not None:
            self._bytes[60] = _clamp(routing, 0, 2)
        if drive is not None:
            self._set(61, drive)
        if drive_type is not None:
            self._bytes[62] = _clamp(drive_type, 0, 6)
        if filter_type is not None:
            self._bytes[63] = _clamp(filter_type, 0, 5)
        if frequency is not None:
            self._set(64, frequency)
        if tracking is not None:
            self._set(65, tracking)
        if resonance is not None:
            self._set(66, resonance)
        if q_normalize is not None:
            self._set(67, q_normalize)
        if env2_to_freq is not None:
            self._set(68, env2_to_freq)
        return self

    # --- Envelopes ---

    def env_amp(self, attack: int | None = None, decay: int | None = None,
                sustain: int | None = None, release: int | None = None,
                velocity: int | None = None) -> PatchBuilder:
        if velocity is not None:
            self._set(69, velocity)
        if attack is not None:
            self._set(70, attack)
        if decay is not None:
            self._set(71, decay)
        if sustain is not None:
            self._set(72, sustain)
        if release is not None:
            self._set(73, release)
        return self

    def env_filter(self, attack: int | None = None, decay: int | None = None,
                   sustain: int | None = None, release: int | None = None,
                   velocity: int | None = None) -> PatchBuilder:
        if velocity is not None:
            self._set(74, velocity)
        if attack is not None:
            self._set(75, attack)
        if decay is not None:
            self._set(76, decay)
        if sustain is not None:
            self._set(77, sustain)
        if release is not None:
            self._set(78, release)
        return self

    def env3(self, delay: int | None = None, attack: int | None = None,
             decay: int | None = None, sustain: int | None = None,
             release: int | None = None) -> PatchBuilder:
        if delay is not None:
            self._set(79, delay)
        if attack is not None:
            self._set(80, attack)
        if decay is not None:
            self._set(81, decay)
        if sustain is not None:
            self._set(82, sustain)
        if release is not None:
            self._set(83, release)
        return self

    # --- LFOs ---

    def lfo1(self, waveform: int | None = None, rate: int | None = None,
             phase_offset: int | None = None, slew_rate: int | None = None,
             delay: int | None = None, delay_sync: int | None = None,
             rate_sync: int | None = None, one_shot: bool | None = None,
             key_sync: bool | None = None, common_sync: bool | None = None,
             delay_trigger: bool | None = None,
             fade_mode: int | None = None) -> PatchBuilder:
        if waveform is not None:
            self._bytes[84] = _clamp(waveform, 0, 37)
        if phase_offset is not None:
            self._bytes[85] = _clamp(phase_offset, 0, 119)
        if slew_rate is not None:
            self._set(86, slew_rate)
        if delay is not None:
            self._set(87, delay)
        if delay_sync is not None:
            self._bytes[88] = _clamp(delay_sync, 0, 35)
        if rate is not None:
            self._set(89, rate)
        if rate_sync is not None:
            self._bytes[90] = _clamp(rate_sync, 0, 35)
        # Flags byte (91): bit0=OneShot, bit1=KeySync, bit2=CommonSync,
        #                   bit3=DelayTrigger, bit4-5=FadeMode
        flags = self._bytes[91]
        if one_shot is not None:
            flags = (flags & ~0x01) | (1 if one_shot else 0)
        if key_sync is not None:
            flags = (flags & ~0x02) | (2 if key_sync else 0)
        if common_sync is not None:
            flags = (flags & ~0x04) | (4 if common_sync else 0)
        if delay_trigger is not None:
            flags = (flags & ~0x08) | (8 if delay_trigger else 0)
        if fade_mode is not None:
            flags = (flags & ~0x30) | ((_clamp(fade_mode, 0, 3) & 0x03) << 4)
        self._bytes[91] = flags
        return self

    def lfo2(self, waveform: int | None = None, rate: int | None = None,
             phase_offset: int | None = None, slew_rate: int | None = None,
             delay: int | None = None, delay_sync: int | None = None,
             rate_sync: int | None = None, one_shot: bool | None = None,
             key_sync: bool | None = None, common_sync: bool | None = None,
             delay_trigger: bool | None = None,
             fade_mode: int | None = None) -> PatchBuilder:
        if waveform is not None:
            self._bytes[92] = _clamp(waveform, 0, 37)
        if phase_offset is not None:
            self._bytes[93] = _clamp(phase_offset, 0, 119)
        if slew_rate is not None:
            self._set(94, slew_rate)
        if delay is not None:
            self._set(95, delay)
        if delay_sync is not None:
            self._bytes[96] = _clamp(delay_sync, 0, 35)
        if rate is not None:
            self._set(97, rate)
        if rate_sync is not None:
            self._bytes[98] = _clamp(rate_sync, 0, 35)
        # Flags byte (99): same bitfield, but FadeMode uses values 4-7
        flags = self._bytes[99]
        if one_shot is not None:
            flags = (flags & ~0x01) | (1 if one_shot else 0)
        if key_sync is not None:
            flags = (flags & ~0x02) | (2 if key_sync else 0)
        if common_sync is not None:
            flags = (flags & ~0x04) | (4 if common_sync else 0)
        if delay_trigger is not None:
            flags = (flags & ~0x08) | (8 if delay_trigger else 0)
        if fade_mode is not None:
            # LFO2 fade mode values are 4-7 (offset by 4 from LFO1)
            flags = (flags & ~0x30) | ((_clamp(fade_mode, 0, 3) & 0x03) << 4)
        self._bytes[99] = flags
        return self

    # --- EQ ---

    def eq(self, bass_freq: int | None = None, bass_level: int | None = None,
           mid_freq: int | None = None, mid_level: int | None = None,
           treble_freq: int | None = None, treble_level: int | None = None) -> PatchBuilder:
        if bass_freq is not None:
            self._set(105, bass_freq)
        if bass_level is not None:
            self._set(106, bass_level)
        if mid_freq is not None:
            self._set(107, mid_freq)
        if mid_level is not None:
            self._set(108, mid_level)
        if treble_freq is not None:
            self._set(109, treble_freq)
        if treble_level is not None:
            self._set(110, treble_level)
        return self

    # --- Effects ---

    def distortion(self, level: int | None = None, type: int | None = None,
                   compensation: int | None = None) -> PatchBuilder:
        if level is not None:
            self._set(100, level)
        if type is not None:
            self._bytes[116] = _clamp(type, 0, 6)
        if compensation is not None:
            self._set(117, compensation)
        return self

    def chorus(self, level: int | None = None, type: int | None = None,
               rate: int | None = None, rate_sync: int | None = None,
               feedback: int | None = None, mod_depth: int | None = None,
               delay: int | None = None) -> PatchBuilder:
        if level is not None:
            self._set(102, level)
        if type is not None:
            self._bytes[118] = _clamp(type, 0, 1)
        if rate is not None:
            self._set(119, rate)
        if rate_sync is not None:
            self._bytes[120] = _clamp(rate_sync, 0, 35)
        if feedback is not None:
            self._set(121, feedback)
        if mod_depth is not None:
            self._set(122, mod_depth)
        if delay is not None:
            self._set(123, delay)
        return self

    # --- Mod Matrix ---

    def add_mod(self, source: int | str, destination: int | str,
                depth: int = 80, source2: int | str = 0) -> PatchBuilder:
        """Add a modulation routing to the next available slot.

        Args:
            source: Mod source (name or ID from MOD_MATRIX_SOURCES)
            destination: Mod destination (name or ID from MOD_MATRIX_DESTINATIONS)
            depth: Modulation depth (0-127, 64=center/none, >64=positive, <64=negative)
            source2: Optional second source for multiplication
        """
        if self._mod_slot_cursor >= _MOD_MATRIX_SLOTS:
            raise ValueError("All 20 mod matrix slots are full")

        s1 = _resolve_mod_source(source)
        s2 = _resolve_mod_source(source2)
        d = _resolve_mod_dest(destination)

        addr = _MOD_MATRIX_START + self._mod_slot_cursor * 4
        self._bytes[addr] = s1
        self._bytes[addr + 1] = s2
        self._bytes[addr + 2] = _clamp(depth)
        self._bytes[addr + 3] = d
        self._mod_slot_cursor += 1
        return self

    def clear_mods(self) -> PatchBuilder:
        """Clear all mod matrix slots to empty (depth=64)."""
        for s in range(_MOD_MATRIX_SLOTS):
            addr = _MOD_MATRIX_START + s * 4
            self._bytes[addr] = 0
            self._bytes[addr + 1] = 0
            self._bytes[addr + 2] = 64
            self._bytes[addr + 3] = 0
        self._mod_slot_cursor = 0
        return self

    # --- Macros ---

    def set_macro(self, macro_num: int, targets: list[dict],
                  position: int = 0) -> PatchBuilder:
        """Configure a hardware macro knob.

        The Circuit Tracks standard knob layout is:
          1=Oscillator, 2=OscMod, 3=AmpEnvelope, 4=FilterEnvelope,
          5=FilterFrequency, 6=Resonance, 7=Modulation, 8=FX

        IMPORTANT: Macros ADD to the parameter's base value in the patch.
        Set base values at a usable midpoint (e.g. filter=60) so the sound
        is immediately playable while leaving headroom for the macro.

        Note: wave_interpolate only works with wavetable waveforms (wave >= 14).
        For basic waveforms (saw, square, etc.), use pulse_width_index or
        virtual_sync_depth on knob 1 instead.

        Args:
            macro_num: Macro number (1-8)
            targets: Up to 4 targets, each a dict with:
                - dest: Parameter name (str) or destination index (int, 0-70)
                - start: Start position (0-127, default 0)
                - end: End position (0-127, default 127)
                - depth: Modulation depth (0-127, default 127=full positive, 64=none)
            position: Initial knob position (0-127, default 0)
        """
        if not 1 <= macro_num <= _MACRO_COUNT:
            raise ValueError(f"macro_num must be 1-8, got {macro_num}")
        if len(targets) > _MACRO_TARGETS:
            raise ValueError(f"Maximum {_MACRO_TARGETS} targets per macro, got {len(targets)}")

        base = _MACRO_START + (macro_num - 1) * _MACRO_SIZE
        self._bytes[base] = _clamp(position)

        for i in range(_MACRO_TARGETS):
            tb = base + 1 + i * 4
            if i < len(targets):
                t = targets[i]
                self._bytes[tb] = _resolve_macro_dest(t.get("dest", 0))
                self._bytes[tb + 1] = _clamp(t.get("start", 0))
                self._bytes[tb + 2] = _clamp(t.get("end", 127))
                self._bytes[tb + 3] = _clamp(t.get("depth", 127))
            else:
                # Empty target sentinel
                self._bytes[tb] = 0
                self._bytes[tb + 1] = 0
                self._bytes[tb + 2] = 127
                self._bytes[tb + 3] = 64
        return self

    # --- Build ---

    def build(self) -> bytes:
        """Return the 340-byte patch binary."""
        return bytes(self._bytes)

    def build_syx(self, synth: int = 1) -> bytes:
        """Return a complete .syx file (Replace Current Patch format).

        Args:
            synth: Synth number (1 or 2)
        """
        synth_index = synth - 1
        header = bytes([0xF0] + SYSEX_MANUFACTURER_ID
                       + [SYSEX_PRODUCT_TYPE, SYSEX_PRODUCT_NUMBER,
                          0x00, synth_index, 0x00])
        return header + self.build() + bytes([0xF7])


# --- Sound design presets ---


def _std_macros(builder: PatchBuilder, osc_wave: int = 2) -> PatchBuilder:
    """Apply standard Circuit Tracks macro knob layout.

    Knob 1: Oscillator (pulse_width for basic waves, wave_interpolate for wavetables)
    Knob 2: Osc Mod (density + detune)
    Knob 3: Amp Envelope (attack + release)
    Knob 4: Filter Envelope (attack + decay)
    Knob 5: Filter Frequency
    Knob 6: Resonance
    Knob 7: Modulation (osc2 detune)
    Knob 8: FX — distortion + chorus
    """
    # Knob 1: use pulse_width for basic waveforms, wave_interpolate for wavetables
    if osc_wave >= 14:
        knob1 = [{"dest": "osc1_wave_interpolate", "start": 0, "end": 127},
                 {"dest": "osc2_wave_interpolate", "start": 0, "end": 127}]
    else:
        knob1 = [{"dest": "osc1_pulse_width_index", "start": 0, "end": 127},
                 {"dest": "osc2_pulse_width_index", "start": 0, "end": 127}]

    return (builder
            .set_macro(1, knob1)
            .set_macro(2, [{"dest": "osc1_density", "start": 0, "end": 80},
                           {"dest": "osc1_density_detune", "start": 0, "end": 60}])
            .set_macro(3, [{"dest": "env1_attack", "start": 0, "end": 127},
                           {"dest": "env1_release", "start": 0, "end": 127}])
            .set_macro(4, [{"dest": "env2_attack", "start": 0, "end": 100},
                           {"dest": "env2_decay", "start": 0, "end": 127}])
            .set_macro(5, [{"dest": "filter_frequency", "start": 0, "end": 127}])
            .set_macro(6, [{"dest": "filter_resonance", "start": 0, "end": 127}])
            .set_macro(7, [{"dest": "osc2_cents", "start": 52, "end": 76}])
            .set_macro(8, [{"dest": "distortion_level", "start": 0, "end": 90},
                           {"dest": "chorus_level", "start": 0, "end": 80}]))


def preset_pad(name: str = "Pad") -> PatchBuilder:
    """Warm pad: detuned saws, slow attack/release, LP filter, chorus, LFO->filter."""
    builder = (PatchBuilder(name)
               .voice(polyphony=2)
               .osc1(wave=2, density=10, density_detune=20)
               .osc2(wave=2, semitones=64, cents=70)
               .mixer(osc1_level=100, osc2_level=90)
               .filter(frequency=65, resonance=15, filter_type=1, env2_to_freq=75)
               .env_amp(attack=60, decay=90, sustain=127, release=80)
               .env_filter(attack=30, decay=80, sustain=50, release=70)
               .chorus(level=0, rate=30, feedback=60, mod_depth=70)
               .add_mod("LFO 1+", "filter frequency", depth=75)
               .lfo1(waveform=0, rate=40))
    return _std_macros(builder, osc_wave=2)


def preset_bass(name: str = "Bass") -> PatchBuilder:
    """Mono bass: saw, LP24 filter with resonance, fast envelope."""
    builder = (PatchBuilder(name)
               .voice(polyphony=0, octave=62)
               .osc1(wave=2)
               .osc2(wave=2, semitones=52)  # -12 semitones (sub)
               .mixer(osc1_level=110, osc2_level=80)
               .filter(frequency=50, resonance=10, filter_type=1, env2_to_freq=90)
               .env_amp(attack=0, decay=70, sustain=100, release=20)
               .env_filter(attack=0, decay=60, sustain=20, release=20))
    return _std_macros(builder, osc_wave=2)


def preset_lead(name: str = "Lead") -> PatchBuilder:
    """Mono lead: bright, portamento, distortion, LFO vibrato."""
    builder = (PatchBuilder(name)
               .voice(polyphony=0, portamento=30)
               .osc1(wave=2)
               .osc2(wave=13, semitones=76)  # square, +12
               .mixer(osc1_level=100, osc2_level=60)
               .filter(frequency=70, resonance=10, filter_type=1, env2_to_freq=70)
               .env_amp(attack=2, decay=80, sustain=100, release=30)
               .env_filter(attack=2, decay=70, sustain=40, release=30)
               .distortion(level=40, type=0)
               .add_mod("LFO 1+/-", "osc 1 & 2 pitch", depth=67)
               .lfo1(waveform=0, rate=75, delay=40))
    return _std_macros(builder, osc_wave=2)


def preset_pluck(name: str = "Pluck") -> PatchBuilder:
    """Pluck: fast attack, short decay, filter envelope sweep."""
    builder = (PatchBuilder(name)
               .voice(polyphony=2)
               .osc1(wave=2)
               .osc2(wave=1, cents=68)
               .mixer(osc1_level=100, osc2_level=70)
               .filter(frequency=40, resonance=15, filter_type=1, env2_to_freq=100)
               .env_amp(attack=0, decay=80, sustain=0, release=40)
               .env_filter(attack=0, decay=60, sustain=0, release=30))
    return _std_macros(builder, osc_wave=2)
