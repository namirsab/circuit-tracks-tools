"""Tests for the synth patch builder."""

import pytest

from circuit_tracks.constants import MACRO_DEST_BY_NAME
from circuit_tracks.patch_builder import (
    _INIT_PATCH,
    PATCH_SIZE,
    PatchBuilder,
    preset_bass,
    preset_lead,
    preset_pad,
    preset_pluck,
)


class TestInitPatch:
    def test_init_patch_size(self):
        assert len(_INIT_PATCH) == PATCH_SIZE

    def test_init_patch_name(self):
        name = _INIT_PATCH[0:16].decode("ascii").strip()
        assert name == "Init"

    def test_init_patch_defaults(self):
        """Verify key defaults from the Programmer's Reference."""
        p = _INIT_PATCH
        assert p[32] == 2  # PolyphonyMode = Poly
        assert p[36] == 2  # Osc1_Wave = sawtooth
        assert p[37] == 127  # Osc1_WaveInterpolate
        assert p[42] == 64  # Osc1_Semitones = center
        assert p[44] == 76  # Osc1_PitchBend = +12
        assert p[54] == 127  # Mixer_Osc1Level
        assert p[55] == 0  # Mixer_Osc2Level
        assert p[63] == 1  # Filter_Type = LP24
        assert p[64] == 127  # Filter_Frequency = fully open
        assert p[69] == 64  # Env1_Velocity = center
        assert p[70] == 2  # Env1_Attack
        assert p[71] == 90  # Env1_Decay
        assert p[72] == 127  # Env1_Sustain
        assert p[73] == 40  # Env1_Release
        assert p[89] == 68  # LFO1_Rate
        assert p[97] == 68  # LFO2_Rate
        assert p[105] == 64  # EQ_BassFrequency
        assert p[109] == 125  # EQ_TrebleFrequency
        assert p[117] == 100  # Distortion_Compensation
        assert p[118] == 1  # Chorus_Type = Chorus

    def test_init_mod_matrix_empty(self):
        """All mod matrix slots should have depth=64 and src/dest=0."""
        for slot in range(20):
            base = 124 + slot * 4
            assert _INIT_PATCH[base] == 0  # source1
            assert _INIT_PATCH[base + 1] == 0  # source2
            assert _INIT_PATCH[base + 2] == 64  # depth (center)
            assert _INIT_PATCH[base + 3] == 0  # destination

    def test_init_macros_empty(self):
        """All macro targets should have end=127, depth=64."""
        for knob in range(8):
            kb = 204 + knob * 17
            assert _INIT_PATCH[kb] == 0  # position
            for t in range(4):
                tb = kb + 1 + t * 4
                assert _INIT_PATCH[tb] == 0  # destination
                assert _INIT_PATCH[tb + 1] == 0  # start
                assert _INIT_PATCH[tb + 2] == 127  # end
                assert _INIT_PATCH[tb + 3] == 64  # depth


class TestPatchBuilder:
    def test_build_returns_340_bytes(self):
        patch = PatchBuilder().build()
        assert len(patch) == PATCH_SIZE
        assert isinstance(patch, bytes)

    def test_name(self):
        patch = PatchBuilder("TestPatch").build()
        name = patch[0:16].decode("ascii").strip()
        assert name == "TestPatch"

    def test_name_truncation(self):
        patch = PatchBuilder("This name is way too long for 16 chars").build()
        name = patch[0:16].decode("ascii").strip()
        assert len(name) <= 16

    def test_category_genre(self):
        patch = PatchBuilder().category(5).genre(3).build()
        assert patch[16] == 5
        assert patch[17] == 3

    def test_osc1(self):
        patch = PatchBuilder().osc1(wave=13, density=50, semitones=70).build()
        assert patch[36] == 13  # wave = square
        assert patch[40] == 50  # density
        assert patch[42] == 70  # semitones

    def test_osc2(self):
        patch = PatchBuilder().osc2(wave=0, cents=80).build()
        assert patch[45] == 0  # wave = sine
        assert patch[52] == 80  # cents

    def test_mixer(self):
        patch = PatchBuilder().mixer(osc1_level=100, osc2_level=80, noise=30).build()
        assert patch[54] == 100
        assert patch[55] == 80
        assert patch[57] == 30

    def test_filter(self):
        patch = PatchBuilder().filter(frequency=80, resonance=40, filter_type=3, drive=50).build()
        assert patch[64] == 80  # frequency
        assert patch[66] == 40  # resonance
        assert patch[63] == 3  # type (band pass 12/12)
        assert patch[61] == 50  # drive

    def test_env_amp(self):
        patch = PatchBuilder().env_amp(attack=10, decay=80, sustain=100, release=60).build()
        assert patch[70] == 10
        assert patch[71] == 80
        assert patch[72] == 100
        assert patch[73] == 60

    def test_env_filter(self):
        patch = PatchBuilder().env_filter(attack=5, decay=70, sustain=30, release=40).build()
        assert patch[75] == 5
        assert patch[76] == 70
        assert patch[77] == 30
        assert patch[78] == 40

    def test_lfo1(self):
        patch = PatchBuilder().lfo1(waveform=2, rate=100, phase_offset=60).build()
        assert patch[84] == 2  # waveform = sawtooth
        assert patch[89] == 100  # rate
        assert patch[85] == 60  # phase offset

    def test_lfo1_flags(self):
        patch = PatchBuilder().lfo1(one_shot=True, key_sync=True, fade_mode=2).build()
        flags = patch[91]
        assert flags & 0x01  # one_shot
        assert flags & 0x02  # key_sync
        assert not (flags & 0x04)  # common_sync off
        assert (flags >> 4) & 0x03 == 2  # fade_mode

    def test_lfo2(self):
        patch = PatchBuilder().lfo2(waveform=4, rate=50).build()
        assert patch[92] == 4  # waveform = random S/H
        assert patch[97] == 50  # rate

    def test_eq(self):
        patch = PatchBuilder().eq(bass_freq=30, treble_level=90).build()
        assert patch[105] == 30  # bass freq
        assert patch[110] == 90  # treble level

    def test_distortion(self):
        patch = PatchBuilder().distortion(level=80, type=2, compensation=60).build()
        assert patch[100] == 80  # level
        assert patch[116] == 2  # type = clipper
        assert patch[117] == 60  # compensation

    def test_chorus(self):
        patch = PatchBuilder().chorus(level=70, type=0, rate=40, feedback=90).build()
        assert patch[102] == 70  # level
        assert patch[118] == 0  # type = phaser
        assert patch[119] == 40  # rate
        assert patch[121] == 90  # feedback

    def test_voice(self):
        patch = PatchBuilder().voice(polyphony=0, portamento=50).build()
        assert patch[32] == 0  # mono
        assert patch[33] == 50  # portamento


class TestModMatrix:
    def test_add_mod_by_id(self):
        patch = PatchBuilder().add_mod(6, 12, depth=80).build()
        # Slot 1 at address 124
        assert patch[124] == 6  # source1 = LFO 1+
        assert patch[125] == 0  # source2 = direct
        assert patch[126] == 80  # depth
        assert patch[127] == 12  # destination = filter frequency

    def test_add_mod_by_name(self):
        patch = PatchBuilder().add_mod("LFO 1+", "filter frequency", depth=90).build()
        assert patch[124] == 6  # LFO 1+
        assert patch[127] == 12  # filter frequency

    def test_add_multiple_mods(self):
        patch = (
            PatchBuilder()
            .add_mod("LFO 1+", "filter frequency", depth=80)
            .add_mod("env amp", "osc 1 level", depth=70)
            .build()
        )
        # Slot 1
        assert patch[124] == 6
        assert patch[127] == 12
        # Slot 2 at address 128
        assert patch[128] == 10  # env amp
        assert patch[131] == 7  # osc 1 level

    def test_add_mod_with_source2(self):
        patch = PatchBuilder().add_mod("LFO 1+", "filter frequency", depth=80, source2="velocity").build()
        assert patch[124] == 6  # source1
        assert patch[125] == 4  # source2 = velocity

    def test_clear_mods(self):
        patch = PatchBuilder().add_mod("LFO 1+", "filter frequency", depth=80).clear_mods().build()
        # All slots should be empty
        for slot in range(20):
            base = 124 + slot * 4
            assert patch[base + 2] == 64  # depth = center

    def test_mod_slot_overflow(self):
        builder = PatchBuilder()
        for _i in range(20):
            builder.add_mod(6, 12, depth=80)
        with pytest.raises(ValueError, match="20 mod matrix slots are full"):
            builder.add_mod(6, 12, depth=80)

    def test_invalid_source_name(self):
        with pytest.raises(ValueError, match="Unknown mod source"):
            PatchBuilder().add_mod("nonexistent", "filter frequency", depth=80)

    def test_invalid_dest_name(self):
        with pytest.raises(ValueError, match="Unknown mod destination"):
            PatchBuilder().add_mod("LFO 1+", "nonexistent", depth=80)


class TestMacros:
    def test_set_macro_by_name(self):
        patch = PatchBuilder().set_macro(1, [{"dest": "filter_frequency", "start": 0, "end": 127, "depth": 64}]).build()
        # Macro 1 at address 204
        assert patch[204] == 0  # position
        assert patch[205] == MACRO_DEST_BY_NAME["filter_frequency"]  # destination
        assert patch[206] == 0  # start
        assert patch[207] == 127  # end
        assert patch[208] == 64  # depth

    def test_set_macro_by_index(self):
        patch = PatchBuilder().set_macro(1, [{"dest": 32, "start": 10, "end": 100}]).build()
        assert patch[205] == 32  # dest index for filter_frequency

    def test_set_macro_multiple_targets(self):
        patch = (
            PatchBuilder()
            .set_macro(
                1,
                [
                    {"dest": "filter_frequency", "start": 0, "end": 127},
                    {"dest": "filter_resonance", "start": 0, "end": 80},
                ],
            )
            .build()
        )
        # Target A
        assert patch[205] == MACRO_DEST_BY_NAME["filter_frequency"]
        # Target B at offset +4
        assert patch[209] == MACRO_DEST_BY_NAME["filter_resonance"]
        assert patch[211] == 80  # end

    def test_set_macro_position(self):
        patch = PatchBuilder().set_macro(1, [], position=100).build()
        assert patch[204] == 100

    def test_set_macro_invalid_number(self):
        with pytest.raises(ValueError, match="macro_num must be 1-8"):
            PatchBuilder().set_macro(0, [])

    def test_set_macro_too_many_targets(self):
        with pytest.raises(ValueError, match="Maximum 4 targets"):
            PatchBuilder().set_macro(1, [{"dest": 0}] * 5)

    def test_macro_8_address(self):
        """Verify macro 8 writes to the correct address (last 17 bytes)."""
        patch = PatchBuilder().set_macro(8, [{"dest": "chorus_level", "start": 0, "end": 127}], position=50).build()
        base = 204 + 7 * 17  # macro 8 base = 323
        assert patch[base] == 50  # position
        assert patch[base + 1] == MACRO_DEST_BY_NAME["chorus_level"]

    def test_empty_targets_are_sentinel(self):
        """Unused macro targets should have sentinel values."""
        patch = PatchBuilder().set_macro(1, [{"dest": "filter_frequency"}]).build()
        # Targets B, C, D should be empty sentinels
        for t in range(1, 4):
            tb = 204 + 1 + t * 4
            assert patch[tb] == 0  # dest
            assert patch[tb + 1] == 0  # start
            assert patch[tb + 2] == 127  # end
            assert patch[tb + 3] == 64  # depth


class TestBuildSyx:
    def test_syx_format(self):
        syx = PatchBuilder("Test").build_syx(synth=1)
        assert syx[0] == 0xF0  # SysEx start
        assert syx[-1] == 0xF7  # SysEx end
        assert syx[1:4] == bytes([0x00, 0x20, 0x29])  # Novation
        assert syx[4] == 0x01  # Product type (Synth)
        assert syx[5] == 0x64  # Product number (Circuit Tracks)
        assert syx[6] == 0x00  # Replace current patch command
        assert syx[7] == 0x00  # Synth 1 (0-indexed)
        assert len(syx) == 350  # F0 + 8-byte header + 340 patch + F7

    def test_syx_synth2(self):
        syx = PatchBuilder().build_syx(synth=2)
        assert syx[7] == 0x01  # Synth 2 (0-indexed)


class TestPresets:
    @pytest.mark.parametrize("preset_fn", [preset_pad, preset_bass, preset_lead, preset_pluck])
    def test_preset_returns_340_bytes(self, preset_fn):
        patch = preset_fn().build()
        assert len(patch) == PATCH_SIZE

    def test_preset_pad_has_chorus_on_macro(self):
        patch = preset_pad().build()
        # Chorus base is 0 (macro sweeps it), but chorus rate is set
        assert patch[119] > 0  # chorus_rate configured

    def test_preset_bass_is_mono(self):
        patch = preset_bass().build()
        assert patch[32] == 0  # polyphony = mono

    def test_preset_lead_has_portamento(self):
        patch = preset_lead().build()
        assert patch[33] > 0  # portamento > 0

    def test_preset_pluck_no_sustain(self):
        patch = preset_pluck().build()
        assert patch[72] == 0  # env1 sustain = 0

    def test_presets_have_different_names(self):
        names = set()
        for fn in [preset_pad, preset_bass, preset_lead, preset_pluck]:
            patch = fn().build()
            name = patch[0:16].decode("ascii").strip()
            names.add(name)
        assert len(names) == 4


class TestFluentChaining:
    def test_full_chain(self):
        """Test that the full fluent API chain produces valid output."""
        patch = (
            PatchBuilder("FullTest")
            .category(3)
            .genre(2)
            .voice(polyphony=2, portamento=0)
            .osc1(wave=2, density=20, density_detune=30)
            .osc2(wave=2, semitones=66, cents=70)
            .mixer(osc1_level=100, osc2_level=90)
            .filter(frequency=80, resonance=40, filter_type=1)
            .env_amp(attack=60, decay=90, sustain=127, release=80)
            .env_filter(attack=10, decay=75, sustain=35, release=45)
            .env3(delay=0, attack=10, decay=70)
            .lfo1(waveform=0, rate=40, key_sync=True)
            .lfo2(waveform=2, rate=60)
            .eq(bass_level=70, mid_level=64, treble_level=60)
            .distortion(level=20, type=1)
            .chorus(level=50, rate=30)
            .add_mod("LFO 1+", "filter frequency", depth=80)
            .add_mod("env filter", "osc 1 level", depth=70)
            .set_macro(1, [{"dest": "filter_frequency", "start": 0, "end": 127}])
            .set_macro(2, [{"dest": "filter_resonance", "start": 0, "end": 100}])
            .build()
        )
        assert len(patch) == PATCH_SIZE
        assert patch[0:16].decode("ascii").strip() == "FullTest"
        assert patch[16] == 3  # category
        assert patch[17] == 2  # genre


class TestClamping:
    def test_wave_clamped(self):
        patch = PatchBuilder().osc1(wave=50).build()
        assert patch[36] == 29  # max is 29

    def test_filter_type_clamped(self):
        patch = PatchBuilder().filter(filter_type=10).build()
        assert patch[63] == 5  # max is 5

    def test_negative_clamped(self):
        patch = PatchBuilder().filter(frequency=-10).build()
        assert patch[64] == 0  # min is 0
