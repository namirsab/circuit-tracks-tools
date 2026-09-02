"""Tests for voice/melody transcription. All audio is synthesized; no microphone needed."""

import math

import pytest

np = pytest.importorskip("numpy")

from circuit_tracks.transcribe import (  # noqa: E402
    analyze,
    midi_to_name,
    quantize_notes,
    segment_notes,
    synthesize_melody,
    transcribe,
    yin_pitch,
)

SR = 22050


def _sine(freq: float, seconds: float = 0.1, sr: int = SR) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return np.sin(2 * np.pi * freq * t)


# ---------------------------------------------------------------------------
# YIN
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("freq", [80.0, 110.0, 261.63, 440.0, 880.0])
def test_yin_detects_sine_pitch(freq):
    f0, conf = yin_pitch(_sine(freq), SR, win=int(0.04 * SR))
    assert abs(f0 - freq) / freq < 0.005
    assert conf > 0.95


def test_yin_harmonic_tone_finds_fundamental():
    t = np.arange(int(0.1 * SR)) / SR
    x = sum(a * np.sin(2 * np.pi * 220 * h * t) for h, a in ((1, 1.0), (2, 0.6), (3, 0.4)))
    f0, _ = yin_pitch(x, SR, win=int(0.04 * SR))
    assert abs(f0 - 220) < 2


def test_yin_noise_has_low_confidence():
    rng = np.random.default_rng(1)
    _, conf = yin_pitch(rng.normal(0, 0.1, int(0.1 * SR)), SR, win=int(0.04 * SR))
    assert conf < 0.6


def test_yin_short_frame_returns_nan():
    f0, conf = yin_pitch(np.zeros(10), SR)
    assert math.isnan(f0) and conf == 0.0


# ---------------------------------------------------------------------------
# Analysis + segmentation
# ---------------------------------------------------------------------------


def test_analyze_marks_silence_unvoiced():
    audio = np.concatenate((np.zeros(SR // 2), synthesize_melody([(69, 0.5)], SR), np.zeros(SR // 2)))
    fa = analyze(audio, SR)
    assert not fa.voiced[: len(fa.voiced) // 4].any()
    assert fa.voiced[len(fa.voiced) // 2]
    assert abs(np.nanmedian(fa.midi) - 69) < 0.3


def test_segment_scale_notes():
    scale = [60, 62, 64, 65, 67, 69, 71, 72]
    events = segment_notes(analyze(synthesize_melody([(m, 0.4) for m in scale], SR), SR))
    assert [e.note for e in events] == scale
    for i, e in enumerate(events):
        assert abs(e.start_s - i * 0.4) < 0.03
        assert 0.3 < e.duration_s <= 0.4


def test_segment_repeated_pitch_split_by_articulation():
    events = segment_notes(analyze(synthesize_melody([(64, 0.25)] * 8, SR), SR))
    assert [e.note for e in events] == [64] * 8


def test_segment_legato_pitch_change_without_gap():
    # Two pitches with no silent gap between them: split on pitch drift alone.
    audio = synthesize_melody([(60, 0.5), (67, 0.5)], SR, gap_s=0.0)
    events = segment_notes(analyze(audio, SR))
    assert [e.note for e in events] == [60, 67]


def test_segment_rests_and_silence():
    audio = synthesize_melody([(57, 0.4), (None, 0.4), (60, 0.4)], SR)
    events = segment_notes(analyze(audio, SR))
    assert [e.note for e in events] == [57, 60]
    assert abs(events[1].start_s - 0.8) < 0.03
    assert segment_notes(analyze(np.zeros(SR), SR)) == []


def test_segment_ignores_blips():
    audio = synthesize_melody([(60, 0.02), (None, 0.3), (64, 0.4)], SR)
    events = segment_notes(analyze(audio, SR))
    assert [e.note for e in events] == [64]


# ---------------------------------------------------------------------------
# Quantization
# ---------------------------------------------------------------------------


def _events(audio):
    return segment_notes(analyze(audio, SR))


def test_quantize_to_steps_at_120bpm():
    scale = [60, 62, 64, 65, 67, 69, 71, 72]
    notes = quantize_notes(_events(synthesize_melody([(m, 0.5) for m in scale], SR)), bpm=120, bars=2)
    assert [n.step for n in notes] == [0, 4, 8, 12, 16, 20, 24, 28]
    assert [n.note for n in notes] == scale
    assert all(3.0 <= n.gate <= 4.0 for n in notes)
    assert all(40 <= n.velocity <= 127 for n in notes)


def test_quantize_latency_compensation():
    audio = np.concatenate((np.zeros(int(0.1 * SR)), synthesize_melody([(60, 0.5)], SR)))
    late = quantize_notes(_events(audio), bpm=120, bars=1)
    fixed = quantize_notes(_events(audio), bpm=120, bars=1, latency_s=0.1)
    assert late[0].step == 1
    assert fixed[0].step == 0


def test_quantize_drops_notes_outside_bars():
    audio = synthesize_melody([(60, 0.5)] * 6, SR)  # 3 s = 1.5 bars at 120
    notes = quantize_notes(_events(audio), bpm=120, bars=1)
    assert [n.step for n in notes] == [0, 4, 8, 12]


def test_quantize_transpose_and_scale_snap():
    audio = synthesize_melody([(61, 0.5), (66, 0.5)], SR)  # C#4, F#4
    notes = quantize_notes(_events(audio), bpm=120, bars=1, transpose=-12, scale_root="C", scale_type="major")
    assert [n.note for n in notes] == [50, 55]  # D3, G3 (ties round up)


def test_quantize_gate_clipped_to_next_onset():
    # Legato: notes run into each other; gate must not overlap the next step.
    audio = synthesize_melody([(60, 0.5), (62, 0.5)], SR, gap_s=0.0)
    notes = quantize_notes(_events(audio), bpm=120, bars=1)
    assert notes[0].gate <= notes[1].step - notes[0].step


def test_quantize_rejects_unknown_scale():
    with pytest.raises(ValueError):
        quantize_notes([], bpm=120, bars=1, scale_type="lydian")


# ---------------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------------


def test_transcribe_end_to_end_and_patterns():
    q = 0.6  # quarter note at 100 BPM
    twinkle = [60, 60, 67, 67, 69, 69, (67, 2), 65, 65, 64, 64, 62, 62, (60, 2)]
    notes = [(n, q) if isinstance(n, int) else (n[0], n[1] * q) for n in twinkle]
    tr = transcribe(synthesize_melody(notes, SR), SR, bpm=100)
    assert tr.bars == 4
    assert [n.name for n in tr.notes] == [midi_to_name(n if isinstance(n, int) else n[0]) for n in twinkle]
    assert tr.notes[6].gate == 8.0
    patterns = tr.patterns(32)
    assert len(patterns) == 2
    assert sorted(patterns[0], key=int) == ["0", "4", "8", "12", "16", "20", "24"]
    assert patterns[1]["0"]["note"] == 65
    assert tr.warnings == []
    d = tr.to_dict()
    assert d["steps"]["24"] == {"note": 67, "gate": 8.0, "velocity": tr.notes[6].velocity}
    assert d["summary"].startswith("14 notes over 4 bar(s)")


def test_transcribe_offset_skips_count_in():
    audio = np.concatenate((np.zeros(SR * 2), synthesize_melody([(64, 0.5)], SR)))
    tr = transcribe(audio, SR, bpm=120, bars=1, offset_s=2.0)
    assert [(n.step, n.note) for n in tr.notes] == [(0, 64)]


def test_transcribe_silence_warns():
    tr = transcribe(np.zeros(SR * 2, dtype=np.float32), SR, bpm=120, bars=1)
    assert tr.notes == []
    assert any("quiet" in w for w in tr.warnings)
    assert any("No pitched" in w for w in tr.warnings)


def test_transcribe_other_sample_rate():
    audio = synthesize_melody([(57, 0.4), (None, 0.4), (60, 0.4), (64, 0.4)], 48000)
    tr = transcribe(audio, 48000, bpm=150, bars=1)
    assert [(n.step, n.note) for n in tr.notes] == [(0, 57), (8, 60), (12, 64)]


def test_file_round_trip(tmp_path):
    sf = pytest.importorskip("soundfile")
    from circuit_tracks.audio_io import load_audio, save_audio

    path = tmp_path / "melody.wav"
    save_audio(str(path), synthesize_melody([(60, 0.5), (67, 0.5)], SR), SR)
    audio, sr = load_audio(str(path))
    assert sr == SR and audio.dtype == np.float32
    assert [n.note for n in transcribe(audio, sr, bpm=120, bars=1).notes] == [60, 67]
    assert sf.info(str(path)).channels == 1
