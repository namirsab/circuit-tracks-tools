"""Tests for the song format module."""

from pathlib import Path

import pytest

from circuit_tracks.ncs_parser import (
    NCS_FILE_SIZE,
    NCSFile,
    get_drum_pattern,
    get_synth_pattern,
    parse_ncs,
)
from circuit_tracks.song import (
    SongData,
    _song_data_to_dict,
    ncs_to_song,
    parse_song,
    quantize_to_scale,
    song_to_ncs,
)

EXAMPLES_DIR = Path(__file__).parent.parent / "example-projects-ncs"
EMPTY_NCS = Path(__file__).parent.parent / "src" / "circuit_tracks" / "data" / "Empty.ncs"


# --- Minimal valid song ---

MINIMAL_SONG = {
    "patterns": {
        "intro": {
            "length": 16,
            "tracks": {
                "drum1": {"steps": {"0": {}, "4": {}, "8": {}, "12": {}}},
            },
        },
    },
}


FULL_SONG = {
    "name": "Test Techno",
    "bpm": 130,
    "swing": 55,
    "color": 3,
    "scale": {"root": "D", "type": "minor"},
    "sounds": {
        "synth1": {"preset": "pad", "name": "TestPad", "params": {"filter_frequency": 80}},
        "synth2": {"preset": "bass"},
        "drum1": {"sample": 0},
        "drum2": {"sample": 2},
    },
    "fx": {
        "reverb": {"type": 2, "decay": 80, "damping": 60},
        "delay": {"time": 64, "feedback": 70},
        "reverb_sends": {"synth1": 40, "drum2": 10},
        "delay_sends": {"synth1": 30},
        "sidechain": {"synth1": {"source": "drum1", "depth": 80}},
    },
    "mixer": {"synth1": {"level": 110, "pan": 50}},
    "patterns": {
        "intro": {
            "length": 16,
            "tracks": {
                "synth1": {
                    "steps": {
                        "0": {"note": 62, "velocity": 100, "gate": 0.8},
                        "8": {"notes": [62, 65, 69], "velocity": 90},
                    }
                },
                "drum1": {"steps": {"0": {}, "4": {}, "8": {}, "12": {}}},
                "drum2": {"steps": {"4": {"velocity": 80}, "12": {"velocity": 80}}},
            },
        },
        "drop": {
            "length": 32,
            "tracks": {
                "synth1": {
                    "steps": {
                        "0": {"note": 62, "velocity": 127, "gate": 0.9},
                    }
                },
                "drum1": {
                    "steps": {str(i): {} for i in range(0, 32, 4)},
                },
            },
        },
    },
    "song": ["intro", "intro", "drop", "intro", "drop"],
}


# --- parse_song tests ---


class TestParseSong:
    def test_minimal_song(self):
        song = parse_song(MINIMAL_SONG)
        assert song.bpm == 120
        assert "intro" in song.patterns
        assert song.patterns["intro"].length == 16

    def test_full_song(self):
        song = parse_song(FULL_SONG)
        assert song.name == "Test Techno"
        assert song.bpm == 130
        assert song.swing == 55
        assert song.color == 3
        assert song.scale_root == "D"
        assert song.scale_type == "minor"
        assert len(song.patterns) == 2
        assert song.song == ["intro", "intro", "drop", "intro", "drop"]
        assert song.sounds["synth1"].preset == "pad"
        assert song.sounds["drum1"].sample == 0
        assert song.fx.reverb["decay"] == 80
        assert song.fx.reverb_sends["synth1"] == 40
        assert song.mixer["synth1"].level == 110

    def test_defaults(self):
        song = parse_song({"patterns": {"a": {"tracks": {}}}})
        assert song.bpm == 120
        assert song.swing == 50
        assert song.name == "Song"
        assert song.scale_root == "C"
        assert song.scale_type == "chromatic"

    def test_no_patterns_raises(self):
        with pytest.raises(ValueError, match="pattern"):
            parse_song({})

    def test_empty_patterns_raises(self):
        with pytest.raises(ValueError, match="pattern"):
            parse_song({"patterns": {}})

    def test_invalid_track_name_raises(self):
        with pytest.raises(ValueError):
            parse_song({"patterns": {"a": {"tracks": {"invalid": {"steps": {}}}}}})

    def test_invalid_sound_track_raises(self):
        with pytest.raises(ValueError):
            parse_song({"patterns": {"a": {"tracks": {}}}, "sounds": {"bad": {}}})

    def test_song_references_missing_pattern(self):
        with pytest.raises(ValueError, match="unknown pattern"):
            parse_song({"patterns": {"a": {"tracks": {}}}, "song": ["a", "b"]})

    def test_bpm_out_of_range(self):
        with pytest.raises(ValueError):
            parse_song({"bpm": 300, "patterns": {"a": {"tracks": {}}}})

    def test_invalid_scale_root(self):
        with pytest.raises(ValueError):
            parse_song({"scale": {"root": "X"}, "patterns": {"a": {"tracks": {}}}})

    def test_invalid_scale_type(self):
        with pytest.raises(ValueError):
            parse_song({"scale": {"type": "alien"}, "patterns": {"a": {"tracks": {}}}})

    def test_too_many_patterns(self):
        patterns = {f"p{i}": {"tracks": {}} for i in range(9)}
        song_list = [f"p{i}" for i in range(9)]
        with pytest.raises(ValueError, match="Too many unique patterns"):
            parse_song({"patterns": patterns, "song": song_list})

    def test_too_many_scenes(self):
        patterns = {"a": {"tracks": {}}}
        song_list = ["a"] * 17
        with pytest.raises(ValueError):
            parse_song({"patterns": patterns, "song": song_list})

    def test_step_index_out_of_range(self):
        # Steps beyond pattern length are now silently accepted by schema
        # but clamped at NCS export time. Schema validates type/range, not
        # cross-field step-index-vs-length constraint.
        pass

    def test_invalid_sidechain_track(self):
        with pytest.raises(ValueError):
            parse_song(
                {
                    "patterns": {"a": {"tracks": {}}},
                    "fx": {"sidechain": {"drum1": {}}},
                }
            )

    def test_invalid_send_track(self):
        with pytest.raises(ValueError):
            parse_song(
                {
                    "patterns": {"a": {"tracks": {}}},
                    "fx": {"reverb_sends": {"badtrack": 50}},
                }
            )


# --- song_to_ncs tests ---


class TestSongToNcs:
    def test_output_size(self):
        song = parse_song(MINIMAL_SONG)
        ncs_bytes = song_to_ncs(song, template_path=EMPTY_NCS)
        assert len(ncs_bytes) == NCS_FILE_SIZE

    def test_header_fields(self):
        song = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        assert ncs.header.name.startswith("Test Techno")
        assert ncs.header.color == 3
        assert ncs.timing.tempo == 130
        assert ncs.timing.swing == 55

    def test_scale_settings(self):
        song = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        assert ncs.project_settings.scale_root == 2  # D
        assert ncs.project_settings.scale_type == 0  # minor = natural minor

    def test_synth_pattern_written(self):
        song = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)

        # "intro" is pattern slot 0
        pat = get_synth_pattern(ncs, 0, 0)  # synth1, pattern 0
        step0 = pat.steps[0]
        assert step0.assigned_note_mask == 0x01  # one note
        assert step0.notes[0].note_number == 72  # MIDI 62 - root 2 + 12
        assert step0.notes[0].velocity == 100

        # Step 8 has a chord (3 notes)
        step8 = pat.steps[8]
        assert step8.assigned_note_mask == 0x07  # bits 0,1,2
        assert step8.notes[0].note_number == 72  # MIDI 62 - root 2 + 12
        assert step8.notes[1].note_number == 75  # MIDI 65 - root 2 + 12
        assert step8.notes[2].note_number == 79  # MIDI 69 - root 2 + 12

    def test_drum_pattern_written(self):
        song = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)

        # "intro" drum1 at slot 0
        pat = get_drum_pattern(ncs, 0, 0)  # drum1, pattern 0
        assert pat.steps[0].active
        assert pat.steps[4].active
        assert not pat.steps[1].active

        # drum2 at slot 0
        pat2 = get_drum_pattern(ncs, 1, 0)  # drum2, pattern 0
        assert pat2.steps[4].active
        assert pat2.steps[4].velocity == 80

    def test_drum_sample_in_ncs(self):
        song = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)

        pat = get_drum_pattern(ncs, 0, 0)  # drum1
        assert pat.steps[0].drum_choice == 0  # sample 0

    def test_fx_settings(self):
        song = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)

        assert ncs.fx.reverb_sends[0] == 40  # synth1
        assert ncs.fx.delay_sends[0] == 30  # synth1
        assert ncs.fx.reverb_type == 2
        assert ncs.fx.reverb_decay == 80
        assert ncs.fx.delay_time == 64
        assert ncs.fx.delay_feedback == 70

    def test_sidechain(self):
        song = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)

        assert ncs.fx.sidechain_s1.source == 0  # drum1
        assert ncs.fx.sidechain_s1.depth == 80

    def test_mixer(self):
        song = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)

        assert ncs.fx.mixer_levels[0] == 110  # synth1
        assert ncs.fx.mixer_pans[0] == 50  # synth1

    def test_pattern_length_set(self):
        song = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)

        # "intro" = slot 0 (length 16), "drop" = slot 1 (length 32)
        intro_pat = get_synth_pattern(ncs, 0, 0)
        assert intro_pat.settings.playback_end == 15  # 16-1

        drop_pat = get_synth_pattern(ncs, 0, 1)
        assert drop_pat.settings.playback_end == 31  # 32-1

    def test_scenes_for_song_order(self):
        song = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)

        # Song: ["intro", "intro", "drop", "intro", "drop"]
        # intro=slot0, drop=slot1
        # 5 scenes, scene chain 0-4
        assert ncs.scene_chain.start == 0
        assert ncs.scene_chain.end == 4

        # Scene 0 -> pattern 0 (intro): start=0, end=0
        for tc in ncs.scenes[0].track_chains:
            assert tc.start == 0
            assert tc.end == 0

        # Scene 2 -> pattern 1 (drop): start=1, end=1
        for tc in ncs.scenes[2].track_chains:
            assert tc.start == 1
            assert tc.end == 1
            # Verify byte layout: [end, 0, 0, start]
            assert tc.to_bytes() == bytes([1, 0, 0, 1])

    def test_gate_and_probability_mapping(self):
        song_dict = {
            "patterns": {
                "a": {
                    "length": 16,
                    "tracks": {
                        "synth1": {
                            "steps": {
                                "0": {"note": 60, "gate": 1.0, "probability": 0.5},
                                "1": {"note": 60, "gate": 0.0},
                            }
                        }
                    },
                }
            }
        }
        song = parse_song(song_dict)
        ncs_bytes = song_to_ncs(song, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)

        pat = get_synth_pattern(ncs, 0, 0)
        # gate 1.0 -> 6 micro-ticks
        assert pat.steps[0].notes[0].gate == 6
        # probability 0.5 -> round(0.5*7) = 4
        assert pat.steps[0].probability == 4
        # gate 0.0 -> min 1 (we clamp to 1)
        assert pat.steps[1].notes[0].gate == 1

    def test_synth_patches_embedded(self):
        song = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)

        # Synth1 patch should be "TestPad" (from preset=pad, name=TestPad)
        s1_name = ncs.synth1_patch[:16].decode("ascii", errors="replace").rstrip()
        assert s1_name == "TestPad"

        # Synth2 patch should be "bass" (from preset=bass, no custom name)
        s2_name = ncs.synth2_patch[:16].decode("ascii", errors="replace").rstrip()
        assert s2_name == "bass"

    def test_drum_configs_embedded(self):
        song = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)

        assert ncs.drum_configs[0].patch_select == 0  # drum1 sample 0
        assert ncs.drum_configs[1].patch_select == 2  # drum2 sample 2

    def test_no_song_order_uses_all_patterns(self):
        """When no song list, all patterns still get written to NCS."""
        song_dict = {
            "patterns": {
                "a": {"length": 16, "tracks": {"drum1": {"steps": {"0": {}}}}},
                "b": {"length": 16, "tracks": {"drum1": {"steps": {"4": {}}}}},
            }
        }
        song = parse_song(song_dict)
        ncs_bytes = song_to_ncs(song, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)

        pat_a = get_drum_pattern(ncs, 0, 0)
        assert pat_a.steps[0].active

        pat_b = get_drum_pattern(ncs, 0, 1)
        assert pat_b.steps[4].active


# --- ncs_to_song tests ---


class TestNcsToSong:
    def test_roundtrip_header(self):
        song1 = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song1, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        song2 = ncs_to_song(ncs)

        assert song2.name.strip() == "Test Techno"
        assert song2.bpm == 130
        assert song2.swing == 55
        assert song2.color == 3

    def test_roundtrip_scale(self):
        song1 = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song1, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        song2 = ncs_to_song(ncs)

        assert song2.scale_root == "D"
        assert song2.scale_type == "natural minor"

    def test_synth_note_offset(self):
        """NCS notes are stored +12 vs external MIDI; ncs_to_song subtracts 12."""
        song1 = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song1, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        song2 = ncs_to_song(ncs)

        # intro synth1 step 0 was note=62 (single note → "note" key)
        step0 = song2.patterns["pattern_0"].tracks["synth1"]["steps"]["0"]
        assert step0["note"] == 62

    def test_chord_notes(self):
        """Multi-note steps round-trip correctly."""
        song1 = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song1, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        song2 = ncs_to_song(ncs)

        step8 = song2.patterns["pattern_0"].tracks["synth1"]["steps"]["8"]
        assert sorted(step8["notes"]) == [62, 65, 69]

    def test_gate_probability_conversion(self):
        song_dict = {
            "patterns": {
                "a": {
                    "length": 16,
                    "tracks": {
                        "synth1": {
                            "steps": {
                                "0": {"note": 60, "gate": 1.0, "probability": 1.0},
                            }
                        }
                    },
                }
            }
        }
        song1 = parse_song(song_dict)
        ncs_bytes = song_to_ncs(song1, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        song2 = ncs_to_song(ncs)

        step0 = song2.patterns["pattern_0"].tracks["synth1"]["steps"]["0"]
        assert step0["gate"] == 1.0
        assert step0["probability"] == 1.0

    def test_empty_ncs_no_patterns(self):
        ncs = parse_ncs(EMPTY_NCS)
        song = ncs_to_song(ncs)
        assert len(song.patterns) == 0
        assert len(song.song) == 0

    def test_drum_steps_roundtrip(self):
        song1 = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song1, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        song2 = ncs_to_song(ncs)

        drum1_steps = song2.patterns["pattern_0"].tracks["drum1"]["steps"]
        assert "0" in drum1_steps
        assert "4" in drum1_steps
        assert "1" not in drum1_steps

    def test_drum_velocity(self):
        song1 = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song1, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        song2 = ncs_to_song(ncs)

        drum2_steps = song2.patterns["pattern_0"].tracks["drum2"]["steps"]
        assert drum2_steps["4"]["velocity"] == 80

    def test_drum_configs_roundtrip(self):
        song1 = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song1, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        song2 = ncs_to_song(ncs)

        assert song2.sounds["drum1"].sample == 0
        assert song2.sounds["drum2"].sample == 2

    def test_synth_patch_name(self):
        song1 = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song1, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        song2 = ncs_to_song(ncs)

        assert song2.sounds["synth1"].name == "TestPad"

    def test_fx_roundtrip(self):
        song1 = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song1, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        song2 = ncs_to_song(ncs)

        assert song2.fx.reverb["type"] == 2
        assert song2.fx.reverb["decay"] == 80
        assert song2.fx.delay["time"] == 64
        assert song2.fx.delay["feedback"] == 70
        assert song2.fx.reverb_sends["synth1"] == 40
        assert song2.fx.delay_sends["synth1"] == 30

    def test_sidechain_roundtrip(self):
        song1 = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song1, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        song2 = ncs_to_song(ncs)

        assert "synth1" in song2.fx.sidechain
        assert song2.fx.sidechain["synth1"]["source"] == "drum1"
        assert song2.fx.sidechain["synth1"]["depth"] == 80

    def test_mixer_roundtrip(self):
        song1 = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song1, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        song2 = ncs_to_song(ncs)

        assert song2.mixer["synth1"].level == 110
        assert song2.mixer["synth1"].pan == 50

    def test_song_order_roundtrip(self):
        song1 = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song1, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        song2 = ncs_to_song(ncs)

        # Original: ["intro", "intro", "drop", "intro", "drop"]
        # intro=slot0=pattern_0, drop=slot1=pattern_1
        assert len(song2.song) == 5
        assert song2.song == ["pattern_0", "pattern_0", "pattern_1", "pattern_0", "pattern_1"]

    def test_pattern_length_preserved(self):
        song1 = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song1, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        song2 = ncs_to_song(ncs)

        # "intro" (pattern_0) had length 16, "drop" (pattern_1) had length 32
        assert song2.patterns["pattern_0"].length == 16
        assert song2.patterns["pattern_1"].length == 32

    def test_song_data_to_dict(self):
        song1 = parse_song(FULL_SONG)
        ncs_bytes = song_to_ncs(song1, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        song2 = ncs_to_song(ncs)
        d = _song_data_to_dict(song2)

        assert d["name"].strip() == "Test Techno"
        assert d["bpm"] == 130
        assert "patterns" in d
        assert "sounds" in d
        assert "fx" in d

    def test_real_ncs_files(self):
        """Load real NCS files without crashing."""
        if not EXAMPLES_DIR.exists():
            pytest.skip("No example-projects-ncs directory")
        for ncs_path in EXAMPLES_DIR.glob("*.ncs"):
            ncs = parse_ncs(ncs_path)
            song = ncs_to_song(ncs)
            assert isinstance(song, SongData)
            assert 40 <= song.bpm <= 240


# --- Scale quantization tests (DEV-0017) ---


class TestQuantizeToScale:
    def test_chromatic_is_noop(self):
        assert quantize_to_scale(61, 0, 15) == 61

    def test_note_in_scale_unchanged(self):
        # D (62) is in D minor (root=2, type=0)
        assert quantize_to_scale(62, 2, 0) == 62

    def test_snap_up_on_tie(self):
        # C# (61) in C major (root=0, type=1): C=60 (dist 1), D=62 (dist 1) -> up = 62
        assert quantize_to_scale(61, 0, 1) == 62

    def test_snap_up_on_tie_2(self):
        # Eb (63) in C major: D=62 (dist 1), E=64 (dist 1) -> tie -> up = 64
        assert quantize_to_scale(63, 0, 1) == 64

    def test_snap_up_on_tie_3(self):
        # A (69) in C minor (root=0, type=0): Ab=68 (dist 1), Bb=70 (dist 1) -> up = 70
        assert quantize_to_scale(69, 0, 0) == 70

    def test_root_note_always_in_scale(self):
        for scale_type in range(16):
            for root in range(12):
                note = 60 + root
                assert quantize_to_scale(note, root, scale_type) == note

    def test_boundary_note_0(self):
        # C (0) is in C major
        assert quantize_to_scale(0, 0, 1) == 0

    def test_boundary_note_127(self):
        result = quantize_to_scale(127, 0, 1)
        assert 0 <= result <= 127

    def test_blues_scale(self):
        # Blues from C: [0,3,5,6,7,10] = C,Eb,F,F#,G,Bb
        # D (62): Eb=63 dist 1, C=60 dist 2 -> snap to Eb
        assert quantize_to_scale(62, 0, 8) == 63

    def test_pentatonic(self):
        # Minor pentatonic from A: root=9, [0,3,5,7,10] -> A,C,D,E,G
        # B (71): C=72 dist 1, A=69 dist 2 -> snap to C
        assert quantize_to_scale(71, 9, 9) == 72

    def test_g_minor_all_degrees(self):
        # G minor (root=7, type=0): G,A,Bb,C,D,Eb,F
        # G=67, A=69, Bb=70, C=72, D=74, Eb=75, F=77
        for note in [67, 69, 70, 72, 74, 75, 77]:
            assert quantize_to_scale(note, 7, 0) == note


class TestParseSongQuantization:
    def test_out_of_scale_note_quantized(self):
        song = parse_song(
            {
                "scale": {"root": "C", "type": "major"},
                "patterns": {
                    "a": {
                        "length": 16,
                        "tracks": {
                            "synth1": {"steps": {"0": {"note": 61}}},  # C# -> D (rounds up on tie)
                        },
                    }
                },
            }
        )
        assert song.patterns["a"].tracks["synth1"]["steps"]["0"]["note"] == 62

    def test_chord_notes_quantized(self):
        song = parse_song(
            {
                "scale": {"root": "C", "type": "major"},
                "patterns": {
                    "a": {
                        "length": 16,
                        "tracks": {
                            "synth1": {"steps": {"0": {"notes": [60, 61, 63]}}},
                        },
                    }
                },
            }
        )
        # 60=C (in scale), 61=C# (-> 62=D, rounds up), 63=Eb (-> 64=E, rounds up)
        assert song.patterns["a"].tracks["synth1"]["steps"]["0"]["notes"] == [60, 62, 64]

    def test_chromatic_no_quantization(self):
        song = parse_song(
            {
                "scale": {"root": "C", "type": "chromatic"},
                "patterns": {
                    "a": {
                        "length": 16,
                        "tracks": {
                            "synth1": {"steps": {"0": {"note": 61}}},
                        },
                    }
                },
            }
        )
        assert song.patterns["a"].tracks["synth1"]["steps"]["0"]["note"] == 61

    def test_drum_tracks_not_quantized(self):
        song = parse_song(
            {
                "scale": {"root": "C", "type": "major"},
                "patterns": {
                    "a": {
                        "length": 16,
                        "tracks": {
                            "drum1": {"steps": {"0": {"velocity": 100}}},
                        },
                    }
                },
            }
        )
        assert song.patterns["a"].tracks["drum1"]["steps"]["0"] == {"velocity": 100}

    def test_in_scale_notes_unchanged(self):
        # D minor: D(62), F(65), A(69) are all in scale
        song = parse_song(
            {
                "scale": {"root": "D", "type": "minor"},
                "patterns": {
                    "a": {
                        "length": 16,
                        "tracks": {
                            "synth1": {
                                "steps": {
                                    "0": {"note": 62},
                                    "1": {"notes": [62, 65, 69]},
                                }
                            },
                        },
                    }
                },
            }
        )
        assert song.patterns["a"].tracks["synth1"]["steps"]["0"]["note"] == 62
        assert song.patterns["a"].tracks["synth1"]["steps"]["1"]["notes"] == [62, 65, 69]

    def test_midi_tracks_quantized(self):
        song = parse_song(
            {
                "scale": {"root": "C", "type": "major"},
                "patterns": {
                    "a": {
                        "length": 16,
                        "tracks": {
                            "midi1": {"steps": {"0": {"note": 61}}},  # C# -> D (rounds up)
                        },
                    }
                },
            }
        )
        assert song.patterns["a"].tracks["midi1"]["steps"]["0"]["note"] == 62

    def test_songdata_scale_preserved(self):
        """parse_song preserves scale metadata even after quantization."""
        song = parse_song(
            {
                "scale": {"root": "G", "type": "minor"},
                "patterns": {
                    "a": {
                        "length": 16,
                        "tracks": {
                            "synth1": {"steps": {"0": {"note": 66}}},  # F# -> F (65) or G (67)
                        },
                    }
                },
            }
        )
        assert song.scale_root == "G"
        assert song.scale_type == "minor"


class TestNcsScaleExport:
    def test_scale_preserved_in_ncs(self):
        song = parse_song(
            {
                "scale": {"root": "D", "type": "minor"},
                "patterns": {
                    "a": {
                        "length": 16,
                        "tracks": {
                            "synth1": {"steps": {"0": {"note": 62}}},
                        },
                    }
                },
            }
        )
        ncs_bytes = song_to_ncs(song, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        assert ncs.project_settings.scale_root == 2  # D
        assert ncs.project_settings.scale_type == 0  # natural minor

    def test_quantized_note_stored_in_ncs(self):
        song = parse_song(
            {
                "scale": {"root": "C", "type": "major"},
                "patterns": {
                    "a": {
                        "length": 16,
                        "tracks": {
                            "synth1": {"steps": {"0": {"note": 61}}},  # C#
                        },
                    }
                },
            }
        )
        ncs_bytes = song_to_ncs(song, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        pat = get_synth_pattern(ncs, 0, 0)
        # C# (61) quantized to D (62, rounds up), stored as (62-0)+12=74
        assert pat.steps[0].notes[0].note_number == 74

    def test_roundtrip_preserves_in_scale_notes(self):
        """Read project -> re-export should not change in-scale notes."""
        song = parse_song(
            {
                "scale": {"root": "D", "type": "minor"},
                "patterns": {
                    "a": {
                        "length": 16,
                        "tracks": {
                            "synth1": {"steps": {"0": {"note": 62}}},  # D, in scale
                        },
                    }
                },
            }
        )
        ncs_bytes = song_to_ncs(song, template_path=EMPTY_NCS)
        ncs = parse_ncs_from_bytes(ncs_bytes)
        song2 = ncs_to_song(ncs)
        ncs_bytes2 = song_to_ncs(song2, template_path=EMPTY_NCS)
        ncs2 = parse_ncs_from_bytes(ncs_bytes2)
        pat = get_synth_pattern(ncs2, 0, 0)
        # D (62) stored as (62 - root 2) + 12 = 72
        assert pat.steps[0].notes[0].note_number == 72


# --- Helpers ---


def parse_ncs_from_bytes(data: bytes) -> "NCSFile":
    """Parse NCS data from bytes (writes to temp file)."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".ncs", delete=False) as f:
        f.write(data)
        f.flush()
        return parse_ncs(f.name)
