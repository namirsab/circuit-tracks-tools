"""Tests for NCS file parser and writer."""

import tempfile
from pathlib import Path

import pytest

from circuit_tracks.ncs_parser import (
    NCSFile,
    NCSNote,
    SynthStep,
    get_drum_pattern,
    get_synth_pattern,
    parse_ncs,
    serialize_ncs,
    write_ncs,
    set_pattern_chain,
    set_scene,
    set_scene_chain,
    NUM_TRACKS,
    NUM_SCENES,
    TRACK_S1,
    TRACK_S2,
    TRACK_D1,
    TRACK_D4,
)

EXAMPLES_DIR = Path(__file__).parent.parent / "example-projects-ncs"
EMPTY_NCS = EXAMPLES_DIR / "Empty.ncs"
TWO_NOTES_NCS = EXAMPLES_DIR / "2 notes.ncs"
WITH_DRUMS_NCS = EXAMPLES_DIR / "WithDrums.ncs"
SCALE_FX_NCS = EXAMPLES_DIR / "ScaleAndFX.ncs"
MIXER_NCS = EXAMPLES_DIR / "MixerAndSends.ncs"
SCENES_NCS = EXAMPLES_DIR / "SceneAndChains.ncs"
TIED_NCS = EXAMPLES_DIR / "TiedNote.ncs"
MIDI_NCS = EXAMPLES_DIR / "MIDISettings.ncs"


# --- Round-trip tests ---


ALL_FILES = [
    "Empty.ncs", "2 notes.ncs", "WithDrums.ncs",
    "ScaleAndFX.ncs", "MixerAndSends.ncs", "SceneAndChains.ncs",
    "TiedNote.ncs", "MIDISettings.ncs",
    "MuteAndLevels.ncs", "SceneTest2.ncs",
    "ChainD1Only.ncs", "FXBypass.ncs",
    "NightChain.ncs", "NightChainWithScenes.ncs",
    "Nightdrive3Fixed.ncs", "FXTestFixed.ncs",
    "DelaySendD1Only.ncs", "DelaySendS2Only.ncs",
    "FXFinal.ncs",
]


@pytest.mark.parametrize("filename", ALL_FILES)
def test_round_trip(filename: str) -> None:
    """Parse → serialize must produce identical bytes."""
    path = EXAMPLES_DIR / filename
    original = path.read_bytes()
    ncs = parse_ncs(path)
    result = serialize_ncs(ncs)
    assert result == original, f"Round-trip failed for {filename}"


@pytest.mark.parametrize("filename", ALL_FILES)
def test_round_trip_via_file(filename: str) -> None:
    """Parse → write → read must match original."""
    path = EXAMPLES_DIR / filename
    original = path.read_bytes()
    ncs = parse_ncs(path)

    with tempfile.NamedTemporaryFile(suffix=".ncs", delete=False) as f:
        write_ncs(ncs, f.name)
        result = Path(f.name).read_bytes()

    assert result == original


# --- Header tests ---


def test_header_empty() -> None:
    ncs = parse_ncs(EMPTY_NCS)
    assert ncs.header.signature == b"USER"
    assert ncs.header.total_session_size == 160780
    assert ncs.header.name == "Empty"


def test_header_name_with_drums() -> None:
    ncs = parse_ncs(WITH_DRUMS_NCS)
    assert ncs.header.name == "WithDrums"


# --- Timing tests ---


def test_timing_default() -> None:
    ncs = parse_ncs(EMPTY_NCS)
    assert ncs.timing.tempo == 120
    assert ncs.timing.swing == 50
    assert ncs.timing.swing_sync_rate == 3
    assert ncs.timing.spare1 == 0
    assert ncs.timing.spare2 == 0


def test_timing_with_drums_bpm() -> None:
    ncs = parse_ncs(WITH_DRUMS_NCS)
    assert ncs.timing.tempo == 122


# --- Synth note decode tests ---


def test_synth1_note_2notes() -> None:
    """2 notes.ncs: synth1 step 0 = C4, default gate, recorded velocity."""
    ncs = parse_ncs(TWO_NOTES_NCS)
    pat = get_synth_pattern(ncs, track=0, pattern=0)
    step = pat.steps[0]

    assert step.is_active
    assert step.assigned_note_mask == 0x01  # 1 note active
    assert step.probability == 7  # 100%

    note = step.notes[0]
    assert note.note_number == 60  # C4
    assert note.gate == 6  # 1 step
    assert note.delay == 0
    assert note.velocity == 98  # recorded velocity


def test_synth2_note_2notes() -> None:
    """2 notes.ncs: synth2 step 0 = C6."""
    ncs = parse_ncs(TWO_NOTES_NCS)
    pat = get_synth_pattern(ncs, track=1, pattern=0)
    step = pat.steps[0]

    assert step.is_active
    note = step.notes[0]
    assert note.note_number == 84  # C6
    assert note.gate == 6
    assert note.velocity == 62  # recorded velocity


def test_synth1_note_with_drums() -> None:
    """WithDrums.ncs: synth1 step 0 = C4, vel=64, gate=4 steps."""
    ncs = parse_ncs(WITH_DRUMS_NCS)
    pat = get_synth_pattern(ncs, track=0, pattern=0)
    step = pat.steps[0]

    assert step.is_active
    note = step.notes[0]
    assert note.note_number == 60  # C4
    assert note.gate == 24  # 4 steps × 6 micro-ticks
    assert note.velocity == 64  # half velocity


def test_empty_steps_have_default_values() -> None:
    """Empty steps should have mask=0, prob=7, notes with vel=96."""
    ncs = parse_ncs(EMPTY_NCS)
    pat = get_synth_pattern(ncs, track=0, pattern=0)

    for step in pat.steps:
        assert not step.is_active
        assert step.assigned_note_mask == 0
        assert step.probability == 7
        for note in step.notes:
            assert note.note_number == 0
            assert note.gate == 0
            assert note.delay == 0
            assert note.velocity == 96  # default


# --- Drum tests ---


def test_drums_with_drums() -> None:
    """WithDrums.ncs: steps 0 and 8 active on all 4 drum tracks."""
    ncs = parse_ncs(WITH_DRUMS_NCS)

    for track in range(4):
        pat = get_drum_pattern(ncs, track=track, pattern=0)
        # Step 0 (index 0) should be active
        assert pat.steps[0].active, f"Drum {track+1} step 0 should be active"
        assert pat.steps[0].velocity == 0x60  # default velocity
        # Step 8 (index 8) should be active
        assert pat.steps[8].active, f"Drum {track+1} step 8 should be active"
        # Other steps should be inactive
        for i in [1, 2, 3, 4, 5, 6, 7, 9, 10, 15]:
            assert not pat.steps[i].active, f"Drum {track+1} step {i} should be inactive"


def test_drums_empty() -> None:
    """Empty.ncs: no drum hits."""
    ncs = parse_ncs(EMPTY_NCS)

    for track in range(4):
        pat = get_drum_pattern(ncs, track=track, pattern=0)
        for i, step in enumerate(pat.steps):
            assert not step.active, f"Drum {track+1} step {i} should be inactive"


def test_drum_probability_default() -> None:
    """All drum steps should have probability 7 (100%) by default."""
    ncs = parse_ncs(EMPTY_NCS)
    pat = get_drum_pattern(ncs, track=0, pattern=0)
    for step in pat.steps:
        assert step.probability == 7


def test_drum_choice_default() -> None:
    """Default drum choice should be 0xFF (no sample flip)."""
    ncs = parse_ncs(EMPTY_NCS)
    pat = get_drum_pattern(ncs, track=0, pattern=0)
    for step in pat.steps:
        assert step.drum_choice == 0xFF


# --- Pattern settings tests ---


def test_pattern_settings_default() -> None:
    """Default pattern settings: end=15, start=0, syncRate=3, direction=0."""
    ncs = parse_ncs(EMPTY_NCS)
    pat = get_synth_pattern(ncs, track=0, pattern=0)
    assert pat.settings.playback_end == 15
    assert pat.settings.playback_start == 0
    assert pat.settings.sync_rate == 3
    assert pat.settings.playback_direction == 0


# --- Pattern count tests ---


def test_pattern_counts() -> None:
    ncs = parse_ncs(EMPTY_NCS)
    assert len(ncs.synth_patterns) == 16  # 2 tracks × 8 patterns
    assert len(ncs.drum_patterns) == 32  # 4 tracks × 8 patterns
    assert len(ncs.midi_patterns) == 16  # 2 tracks × 8 patterns


# --- Scale and FX preset tests ---


def test_scale_and_fx_defaults() -> None:
    """Empty project: default scale, root, and FX presets."""
    ncs = parse_ncs(EMPTY_NCS)
    assert ncs.project_settings.scale_type == 0  # Natural Minor
    assert ncs.project_settings.scale_root == 0  # C
    assert ncs.project_settings.delay_preset == 0
    assert ncs.project_settings.reverb_preset == 0


def test_scale_and_fx_changed() -> None:
    """ScaleAndFX: Major scale, root G, reverb 5, delay 8."""
    ncs = parse_ncs(SCALE_FX_NCS)
    assert ncs.project_settings.scale_type == 1  # Major
    assert ncs.project_settings.scale_root == 7  # G
    assert ncs.project_settings.delay_preset == 7  # delay 8 (0-indexed)
    assert ncs.project_settings.reverb_preset == 4  # reverb 5 (0-indexed)


# --- Tied note test ---


def test_tied_note_long_gate() -> None:
    """TiedNote: step 0 has gate extending beyond pattern = tied note."""
    ncs = parse_ncs(TIED_NCS)
    pat = get_synth_pattern(ncs, track=0, pattern=0)
    step = pat.steps[0]
    assert step.is_active
    note = step.notes[0]
    assert note.note_number == 60  # C4
    assert note.gate == 0xE0  # 224 ticks = ~37 steps, extends beyond pattern
    assert note.gate > 32 * 6  # gate > entire 32-step pattern = tied/drone


# --- Synth level test ---


def test_synth1_level_zero() -> None:
    """MuteAndLevels: synth1 level turned all the way down to 0."""
    ncs = parse_ncs(EXAMPLES_DIR / "MuteAndLevels.ncs")
    # Synth/MIDI levels at tail offset 0x2701C (relative to 0x26CFC = +0x320)
    # S1=byte 0, S2=byte 1, M1=byte 2, M2=byte 3
    tail = ncs.tail
    level_offset = 0x2701C - 0x26CFC
    assert tail[level_offset] == 0  # S1 level = 0
    assert tail[level_offset + 1] == 100  # S2 level unchanged
    assert tail[level_offset + 2] == 100  # M1 level unchanged
    assert tail[level_offset + 3] == 100  # M2 level unchanged


# --- FX bypass test ---


def test_fx_bypass() -> None:
    """FXBypass.ncs: FX turned off."""
    ncs = parse_ncs(EXAMPLES_DIR / "FXBypass.ncs")
    fx_bypass_offset = 0x27007 - 0x26CFC
    assert ncs.tail[fx_bypass_offset] == 1  # FX bypassed


def test_fx_bypass_default_off() -> None:
    """Empty.ncs: FX enabled by default."""
    ncs = parse_ncs(EMPTY_NCS)
    fx_bypass_offset = 0x27007 - 0x26CFC
    assert ncs.tail[fx_bypass_offset] == 0  # FX active


# --- Scene and chain tests ---


def test_pattern_chains_default() -> None:
    """Empty project: no pattern chains."""
    ncs = parse_ncs(EMPTY_NCS)
    assert len(ncs.pattern_chains) == NUM_TRACKS
    for chain in ncs.pattern_chains:
        assert chain.end == 0
        assert chain.start == 0


def test_pattern_chains_nightchain() -> None:
    """NightChain: all tracks chained patterns 1-5."""
    ncs = parse_ncs(EXAMPLES_DIR / "NightChain.ncs")
    for i, chain in enumerate(ncs.pattern_chains):
        assert chain.end == 4, f"Track {i} chain end should be 4"


def test_scenes_default() -> None:
    """Empty project: 16 scenes, all default."""
    ncs = parse_ncs(EMPTY_NCS)
    assert len(ncs.scenes) == NUM_SCENES
    for scene in ncs.scenes:
        for chain in scene.track_chains:
            assert chain.end == 0
            assert chain.start == 0


def test_scenes_nightchain_with_scenes() -> None:
    """NightChainWithScenes: 2 scenes with all tracks chained to pattern 5."""
    ncs = parse_ncs(EXAMPLES_DIR / "NightChainWithScenes.ncs")
    # Scene 0: all tracks end=4
    for chain in ncs.scenes[0].track_chains:
        assert chain.end == 4
    # Scene 1: all tracks end=4
    for chain in ncs.scenes[1].track_chains:
        assert chain.end == 4
    # Scene 2+: untouched
    for chain in ncs.scenes[2].track_chains:
        assert chain.end == 0


def test_scene_chain() -> None:
    """NightChainWithScenes: scene chain end=1 (2 scenes)."""
    ncs = parse_ncs(EXAMPLES_DIR / "NightChainWithScenes.ncs")
    assert ncs.scene_chain.end == 1


def test_scene_chain_default() -> None:
    ncs = parse_ncs(EMPTY_NCS)
    assert ncs.scene_chain.end == 0
    assert ncs.scene_chain.start == 0


def test_set_pattern_chain_helper() -> None:
    """Test the set_pattern_chain convenience function."""
    ncs = parse_ncs(EMPTY_NCS)
    set_pattern_chain(ncs, track=0, end=3)  # S1: patterns 1-4
    set_pattern_chain(ncs, track=4, end=1)  # D1: patterns 1-2
    assert ncs.pattern_chains[0].end == 3
    assert ncs.pattern_chains[4].end == 1


def test_set_scene_helper() -> None:
    """Test the set_scene convenience function."""
    ncs = parse_ncs(EMPTY_NCS)
    set_scene(ncs, 0, {0: (1, 3), 4: (0, 1)})  # S1=patterns 2-4, D1=patterns 1-2
    assert ncs.scenes[0].track_chains[0].start == 1
    assert ncs.scenes[0].track_chains[0].end == 3
    assert ncs.scenes[0].track_chains[4].start == 0
    assert ncs.scenes[0].track_chains[4].end == 1
    # Verify byte layout: [end, 0, 0, start]
    assert ncs.scenes[0].track_chains[0].to_bytes() == bytes([3, 0, 0, 1])
    assert ncs.scenes[0].track_chains[4].to_bytes() == bytes([1, 0, 0, 0])


# --- FX tests ---


def test_fx_defaults() -> None:
    """Empty project: default FX settings."""
    ncs = parse_ncs(EMPTY_NCS)
    assert ncs.fx.reverb_sends == [0] * 8
    assert ncs.fx.delay_sends == [0] * 8
    assert ncs.fx.reverb_type == 2  # default Small Room
    assert ncs.fx.reverb_decay == 64
    assert ncs.fx.reverb_damping == 64
    assert ncs.fx.delay_time == 64
    assert ncs.fx.delay_sync == 20
    assert not ncs.fx.fx_bypass
    assert ncs.fx.sidechain_s1.hold == 50
    assert ncs.fx.sidechain_s1.decay == 70
    assert ncs.fx.sidechain_s1.depth == 0
    assert ncs.fx.mixer_levels == [100, 100, 100, 100]
    assert ncs.fx.mixer_pans == [64, 64, 64, 64]


def test_fx_reverb_sends() -> None:
    """FXTestFixed: reverb on S2 and D4."""
    ncs = parse_ncs(EXAMPLES_DIR / "FXTestFixed.ncs")
    assert ncs.fx.reverb_sends[TRACK_S2] == 110
    assert ncs.fx.reverb_sends[TRACK_D4] == 40
    assert ncs.fx.reverb_sends[TRACK_S1] == 0
    assert ncs.fx.reverb_sends[TRACK_D1] == 0


def test_fx_delay_sends_d1() -> None:
    """DelaySendD1Only: delay send on D1 only."""
    ncs = parse_ncs(EXAMPLES_DIR / "DelaySendD1Only.ncs")
    assert ncs.fx.delay_sends[TRACK_D1] == 127
    assert ncs.fx.delay_sends[TRACK_S1] == 0
    assert ncs.fx.delay_sends[TRACK_S2] == 0


def test_fx_delay_sends_s2() -> None:
    """DelaySendS2Only: delay send on S2 only."""
    ncs = parse_ncs(EXAMPLES_DIR / "DelaySendS2Only.ncs")
    assert ncs.fx.delay_sends[TRACK_S2] == 127
    assert ncs.fx.delay_sends[TRACK_S1] == 0
    assert ncs.fx.delay_sends[TRACK_D1] == 0


def test_fx_bypass_on() -> None:
    """FXBypass: FX bypassed."""
    ncs = parse_ncs(EXAMPLES_DIR / "FXBypass.ncs")
    assert ncs.fx.fx_bypass is True


def test_fx_sidechain() -> None:
    """FXTestFixed: sidechain on S1 and S2, trigger=D1."""
    ncs = parse_ncs(EXAMPLES_DIR / "FXTestFixed.ncs")
    assert ncs.fx.sidechain_s1.source == 0  # D1
    assert ncs.fx.sidechain_s1.attack == 5
    assert ncs.fx.sidechain_s1.depth == 123
    assert ncs.fx.sidechain_s2.source == 0  # D1
    assert ncs.fx.sidechain_s2.attack == 5
    assert ncs.fx.sidechain_s2.depth == 115


def test_fx_mixer_level_zero() -> None:
    """MuteAndLevels: S1 level set to 0."""
    ncs = parse_ncs(EXAMPLES_DIR / "MuteAndLevels.ncs")
    assert ncs.fx.mixer_levels[0] == 0   # S1 = 0
    assert ncs.fx.mixer_levels[1] == 100  # S2 unchanged


def test_fx_roundtrip_write() -> None:
    """Modify FX settings and verify they persist through write/read."""
    ncs = parse_ncs(EMPTY_NCS)
    ncs.fx.reverb_sends[TRACK_S2] = 80
    ncs.fx.delay_sends[TRACK_D1] = 60
    ncs.fx.sidechain_s1.depth = 100
    ncs.fx.fx_bypass = True
    ncs.fx.mixer_levels[0] = 50

    data = serialize_ncs(ncs)
    ncs2 = parse_ncs.__wrapped__(data) if hasattr(parse_ncs, '__wrapped__') else None
    # Re-parse from bytes
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".ncs", delete=False) as f:
        f.write(data)
        f.flush()
        ncs2 = parse_ncs(f.name)

    assert ncs2.fx.reverb_sends[TRACK_S2] == 80
    assert ncs2.fx.delay_sends[TRACK_D1] == 60
    assert ncs2.fx.sidechain_s1.depth == 100
    assert ncs2.fx.fx_bypass is True
    assert ncs2.fx.mixer_levels[0] == 50
