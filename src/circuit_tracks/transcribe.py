"""Monophonic audio -> note transcription (singing, whistling, single-line instruments).

Pure numpy. The pipeline is:

    audio -> framewise YIN pitch + RMS -> voicing -> note segmentation
          -> step quantization (16 steps per bar) -> optional scale snap

Everything here is deterministic and testable without a microphone; see
``synthesize_melody`` for generating test audio and ``audio_io`` for capture.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from circuit_tracks.song import _SCALE_ROOT, _SCALE_TYPE, quantize_to_scale

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Voice-friendly defaults: ~C2 to ~D6.
DEFAULT_FMIN = 60.0
DEFAULT_FMAX = 1200.0


def midi_to_name(note: int) -> str:
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 1}"


def hz_to_midi(freq: float) -> float:
    return 69.0 + 12.0 * math.log2(freq / 440.0)


def midi_to_hz(midi: float) -> float:
    return 440.0 * 2.0 ** ((midi - 69.0) / 12.0)


# ---------------------------------------------------------------------------
# YIN pitch detection
# ---------------------------------------------------------------------------


def _difference_function(frame: np.ndarray, win: int, tau_max: int) -> np.ndarray:
    """YIN difference function d(tau) for tau in 0..tau_max, via FFT cross-correlation."""
    x = frame[: win + tau_max].astype(np.float64)
    n = win + tau_max
    size = 1
    while size < 2 * n:
        size <<= 1
    fa = np.fft.rfft(x[:win], size)
    fb = np.fft.rfft(x, size)
    # corr[tau] = sum_j x[j] * x[j + tau] for j < win
    corr = np.fft.irfft(np.conj(fa) * fb, size)[: tau_max + 1]
    sq = np.concatenate(([0.0], np.cumsum(x * x)))
    e0 = sq[win]
    e_tau = sq[win : win + tau_max + 1] - sq[: tau_max + 1]
    return np.maximum(e0 + e_tau - 2.0 * corr, 0.0)


def _cmndf(d: np.ndarray) -> np.ndarray:
    """Cumulative mean normalized difference function."""
    out = np.ones_like(d)
    running = np.cumsum(d[1:])
    tau = np.arange(1, len(d), dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[1:] = np.where(running > 0, d[1:] * tau / running, 1.0)
    return out


def _parabolic_min(y: np.ndarray, i: int) -> float:
    if 0 < i < len(y) - 1:
        a, b, c = y[i - 1], y[i], y[i + 1]
        denom = a - 2.0 * b + c
        if denom != 0:
            return i + 0.5 * (a - c) / denom
    return float(i)


def yin_pitch(
    frame: np.ndarray,
    sr: int,
    fmin: float = DEFAULT_FMIN,
    fmax: float = DEFAULT_FMAX,
    threshold: float = 0.15,
    win: int | None = None,
) -> tuple[float, float]:
    """Estimate the fundamental frequency of one frame with the YIN algorithm.

    Returns ``(f0_hz, confidence)``. ``f0_hz`` is NaN if the frame is too short.
    ``confidence`` is ``1 - cmndf`` at the chosen lag (1.0 = perfectly periodic).
    """
    tau_max = int(sr / fmin)
    tau_min = max(2, int(sr / fmax))
    if win is None:
        win = len(frame) - tau_max
    if win < tau_min or len(frame) < win + tau_max:
        return math.nan, 0.0

    cm = _cmndf(_difference_function(frame, win, tau_max))
    search = cm[tau_min:-1]
    below = np.flatnonzero(search < threshold)
    if len(below):
        tau = int(below[0]) + tau_min
        while tau + 1 < len(cm) - 1 and cm[tau + 1] < cm[tau]:
            tau += 1
    else:
        tau = int(np.argmin(search)) + tau_min

    tau_f = _parabolic_min(cm, tau)
    confidence = float(np.clip(1.0 - cm[tau], 0.0, 1.0))
    return sr / tau_f, confidence


# ---------------------------------------------------------------------------
# Framewise analysis
# ---------------------------------------------------------------------------


@dataclass
class FrameAnalysis:
    """Per-frame pitch and level. Arrays share one length; NaN means unvoiced."""

    sr: int
    hop: int
    times: np.ndarray
    f0: np.ndarray
    midi: np.ndarray
    confidence: np.ndarray
    rms_db: np.ndarray
    voiced: np.ndarray

    @property
    def peak_db(self) -> float:
        return float(np.max(self.rms_db)) if len(self.rms_db) else -120.0


def _nan_median_filter(x: np.ndarray, width: int) -> np.ndarray:
    half = width // 2
    out = np.full_like(x, np.nan)
    for i in range(len(x)):
        window = x[max(0, i - half) : i + half + 1]
        window = window[~np.isnan(window)]
        if len(window):
            out[i] = np.median(window)
    return out


def analyze(
    audio: np.ndarray,
    sr: int,
    fmin: float = DEFAULT_FMIN,
    fmax: float = DEFAULT_FMAX,
    hop_s: float = 0.010,
    win_s: float = 0.040,
    threshold: float = 0.15,
    silence_db: float = -30.0,
    floor_db: float = -60.0,
    min_confidence: float = 0.6,
) -> FrameAnalysis:
    """Run YIN + RMS over ``audio`` in frames and decide which frames are voiced.

    A frame is voiced when it is louder than ``peak + silence_db`` (and above
    ``floor_db`` absolute), its YIN confidence is at least ``min_confidence``,
    and its pitch lies within ``fmin..fmax``.
    """
    audio = np.asarray(audio, dtype=np.float64)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    hop = max(1, int(round(sr * hop_s)))
    win = max(64, int(round(sr * win_s)))
    tau_max = int(sr / fmin)
    frame_len = win + tau_max

    n_frames = max(0, (len(audio) - win) // hop + 1)
    padded = np.concatenate((audio, np.zeros(frame_len)))

    times = np.empty(n_frames)
    f0 = np.full(n_frames, np.nan)
    conf = np.zeros(n_frames)
    rms = np.zeros(n_frames)

    for i in range(n_frames):
        start = i * hop
        frame = padded[start : start + frame_len]
        rms[i] = math.sqrt(float(np.mean(frame[:win] ** 2)))
        times[i] = (start + win / 2) / sr
        if rms[i] < 1e-5:
            continue
        f0[i], conf[i] = yin_pitch(frame, sr, fmin, fmax, threshold, win=win)

    rms_db = 20.0 * np.log10(rms + 1e-9)
    peak_db = float(np.max(rms_db)) if n_frames else -120.0
    in_range = (f0 >= fmin) & (f0 <= fmax)
    voiced = (rms_db > max(peak_db + silence_db, floor_db)) & (conf >= min_confidence) & in_range

    midi = np.full(n_frames, np.nan)
    midi[voiced] = 69.0 + 12.0 * np.log2(f0[voiced] / 440.0)
    midi = _nan_median_filter(midi, 5)
    midi[~voiced] = np.nan

    return FrameAnalysis(sr, hop, times, f0, midi, conf, rms_db, voiced)


# ---------------------------------------------------------------------------
# Note segmentation
# ---------------------------------------------------------------------------


@dataclass
class NoteEvent:
    start_s: float
    end_s: float
    midi: float
    confidence: float
    level_db: float

    @property
    def note(self) -> int:
        return int(round(self.midi))

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def segment_notes(
    fa: FrameAnalysis,
    min_note_s: float = 0.06,
    pitch_tolerance: float = 0.75,
    split_frames: int = 3,
    onset_db: float = 6.0,
    onset_lag: int = 3,
) -> list[NoteEvent]:
    """Group voiced frames into notes.

    A note ends when voicing stops, when the pitch drifts by more than
    ``pitch_tolerance`` semitones for ``split_frames`` consecutive frames, or when
    the level rises by more than ``onset_db`` within ``onset_lag`` frames (a new
    attack on the same pitch, e.g. "da da da").
    """
    hop_s = fa.hop / fa.sr
    min_frames = max(1, int(round(min_note_s / hop_s)))
    notes: list[NoteEvent] = []

    start: int | None = None
    pitches: list[float] = []
    drift = 0

    def close(end_idx: int) -> None:
        nonlocal start, pitches, drift
        if start is not None and end_idx - start >= min_frames:
            sl = slice(start, end_idx)
            notes.append(
                NoteEvent(
                    start_s=float(fa.times[start] - fa.hop / fa.sr / 2),
                    end_s=float(fa.times[end_idx - 1] + hop_s / 2),
                    midi=float(np.median(pitches)),
                    confidence=float(np.mean(fa.confidence[sl])),
                    level_db=float(np.max(fa.rms_db[sl])),
                )
            )
        start, pitches, drift = None, [], 0

    for i in range(len(fa.times)):
        if not fa.voiced[i]:
            close(i)
            continue
        p = float(fa.midi[i])
        if start is None:
            start, pitches, drift = i, [p], 0
            continue

        # New attack on a (possibly) same pitch: level jumped up recently.
        if i - start >= max(min_frames, onset_lag) and fa.rms_db[i] - fa.rms_db[i - onset_lag] > onset_db:
            close(i - onset_lag + 1)
            start, pitches, drift = i - onset_lag + 1, [float(m) for m in fa.midi[i - onset_lag + 1 : i + 1]], 0
            continue

        # Pitch moved to a different note.
        if abs(p - float(np.median(pitches))) > pitch_tolerance:
            drift += 1
            if drift >= split_frames:
                split_at = i - split_frames + 1
                close(split_at)
                start = split_at
                pitches = [float(m) for m in fa.midi[split_at : i + 1]]
                drift = 0
            else:
                pitches.append(p)
        else:
            drift = 0
            pitches.append(p)

    close(len(fa.times))
    return notes


# ---------------------------------------------------------------------------
# Quantization to sequencer steps
# ---------------------------------------------------------------------------


@dataclass
class QuantizedNote:
    step: int
    note: int
    gate: float
    velocity: int
    confidence: float
    start_s: float
    duration_s: float

    @property
    def name(self) -> str:
        return midi_to_name(self.note)

    def to_step(self) -> dict:
        return {"note": self.note, "gate": self.gate, "velocity": self.velocity}


def _scale_indices(scale_root: str | None, scale_type: str | None) -> tuple[int, int] | None:
    if not scale_type:
        return None
    type_i = _SCALE_TYPE.get(scale_type.lower())
    if type_i is None:
        raise ValueError(f"Unknown scale_type '{scale_type}'. Valid: {sorted(_SCALE_TYPE)}")
    root_i = _SCALE_ROOT.get(scale_root or "C")
    if root_i is None:
        raise ValueError(f"Unknown scale_root '{scale_root}'. Valid: {sorted(_SCALE_ROOT)}")
    return root_i, type_i


def quantize_notes(
    events: list[NoteEvent],
    bpm: float,
    bars: int,
    steps_per_bar: int = 16,
    latency_s: float = 0.0,
    transpose: int = 0,
    scale_root: str | None = None,
    scale_type: str | None = None,
    gate_resolution: float = 0.5,
) -> list[QuantizedNote]:
    """Snap note events to a 16th-note grid and (optionally) to a scale.

    ``latency_s`` is subtracted from every onset to compensate capture delay.
    Notes outside ``bars`` are dropped; two notes landing on the same step keep
    the longer one; gates are clipped so notes never overlap the next onset.
    """
    step_s = 60.0 / bpm / (steps_per_bar / 4)
    total = bars * steps_per_bar
    scale = _scale_indices(scale_root, scale_type)
    max_level = max((e.level_db for e in events), default=0.0)

    by_step: dict[int, QuantizedNote] = {}
    for ev in events:
        step = int(round((ev.start_s - latency_s) / step_s))
        if step < 0 or step >= total:
            continue
        gate = round(ev.duration_s / step_s / gate_resolution) * gate_resolution
        gate = float(min(16.0, max(gate_resolution, gate)))
        note = ev.note + transpose
        if scale is not None:
            note = quantize_to_scale(note, *scale)
        note = int(min(127, max(0, note)))
        velocity = int(round(127 + 4 * (ev.level_db - max_level)))  # -1 dB -> -4 velocity
        velocity = min(127, max(40, velocity))
        q = QuantizedNote(
            step, note, gate, velocity, round(ev.confidence, 3), round(ev.start_s, 3), round(ev.duration_s, 3)
        )
        prev = by_step.get(step)
        if prev is None or q.duration_s > prev.duration_s:
            by_step[step] = q

    ordered = [by_step[s] for s in sorted(by_step)]
    for cur, nxt in zip(ordered, ordered[1:], strict=False):
        cur.gate = min(cur.gate, float(nxt.step - cur.step))
    return ordered


# ---------------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------------


@dataclass
class Transcription:
    bpm: float
    bars: int
    steps_per_bar: int
    notes: list[QuantizedNote]
    events: list[NoteEvent]
    peak_db: float
    scale: str
    warnings: list[str] = field(default_factory=list)

    def steps(self) -> dict[str, dict]:
        return {str(n.step): n.to_step() for n in self.notes}

    def patterns(self, pattern_length: int = 32) -> list[dict[str, dict]]:
        """Split steps into consecutive patterns of ``pattern_length`` steps (local indices)."""
        count = max(1, math.ceil(self.bars * self.steps_per_bar / pattern_length))
        out: list[dict[str, dict]] = [{} for _ in range(count)]
        for n in self.notes:
            out[n.step // pattern_length][str(n.step % pattern_length)] = n.to_step()
        return out

    def summary(self) -> str:
        names = " ".join(n.name for n in self.notes) or "(none)"
        return f"{len(self.notes)} notes over {self.bars} bar(s) at {self.bpm:g} BPM: {names}"

    def to_dict(self, pattern_length: int = 32) -> dict:
        return {
            "summary": self.summary(),
            "bpm": self.bpm,
            "bars": self.bars,
            "steps_per_bar": self.steps_per_bar,
            "scale": self.scale,
            "peak_db": round(self.peak_db, 1),
            "notes": [
                {
                    "step": n.step,
                    "note": n.note,
                    "name": n.name,
                    "gate": n.gate,
                    "velocity": n.velocity,
                    "confidence": n.confidence,
                    "start_s": n.start_s,
                    "duration_s": n.duration_s,
                }
                for n in self.notes
            ],
            "steps": self.steps(),
            "patterns": self.patterns(pattern_length),
            "warnings": self.warnings,
        }


def transcribe(
    audio: np.ndarray,
    sr: int,
    bpm: float,
    bars: int | None = None,
    steps_per_bar: int = 16,
    offset_s: float = 0.0,
    latency_s: float = 0.0,
    transpose: int = 0,
    scale_root: str | None = None,
    scale_type: str | None = None,
) -> Transcription:
    """Transcribe monophonic ``audio`` into quantized sequencer notes.

    ``offset_s`` skips the start of the audio (e.g. a count-in). When ``bars`` is
    omitted it is derived from the audio length.
    """
    if offset_s > 0:
        audio = np.asarray(audio)[int(offset_s * sr) :]
    bar_s = 60.0 / bpm * 4
    duration_s = len(audio) / sr
    if bars is None:
        bars = max(1, math.ceil(duration_s / bar_s - 0.05))

    fa = analyze(audio, sr)
    events = segment_notes(fa)
    notes = quantize_notes(events, bpm, bars, steps_per_bar, latency_s, transpose, scale_root, scale_type)

    warnings: list[str] = []
    if fa.peak_db < -40:
        warnings.append(
            f"Audio is very quiet (peak {fa.peak_db:.0f} dBFS). Check the input device and microphone permission."
        )
    if not events:
        warnings.append("No pitched notes detected.")
    elif not notes:
        warnings.append("Notes were detected but none fell inside the requested bars.")
    dropped = len(events) - len(notes)
    if events and notes and dropped > 0:
        warnings.append(f"{dropped} note(s) dropped (outside the bar range or merged onto the same step).")

    scale = f"{scale_root or 'C'} {scale_type}" if scale_type else "chromatic"
    return Transcription(bpm, bars, steps_per_bar, notes, events, fa.peak_db, scale, warnings)


# ---------------------------------------------------------------------------
# Synthetic test audio
# ---------------------------------------------------------------------------


def synthesize_melody(
    notes: list[tuple[int | None, float]],
    sr: int = 22050,
    vibrato_hz: float = 5.5,
    vibrato_semitones: float = 0.25,
    gap_s: float = 0.04,
    noise: float = 0.002,
    seed: int = 0,
) -> np.ndarray:
    """Render a "sung" monophonic melody for testing.

    ``notes`` is a list of ``(midi_note_or_None, duration_s)``; ``None`` is a rest.
    Each note is a harmonic tone with vibrato, an attack/release envelope and a
    short silent gap (the consonant) before the next note.
    """
    rng = np.random.default_rng(seed)
    chunks: list[np.ndarray] = []
    for midi, dur in notes:
        n = int(round(dur * sr))
        if midi is None:
            chunks.append(np.zeros(n))
            continue
        gap = min(int(gap_s * sr), n // 4)
        length = n - gap
        t = np.arange(length) / sr
        detune = rng.uniform(-0.1, 0.1)
        inst = midi_to_hz(midi + detune + vibrato_semitones * np.sin(2 * np.pi * vibrato_hz * t))
        phase = 2 * np.pi * np.cumsum(inst) / sr
        tone = sum(a * np.sin(h * phase) for h, a in ((1, 1.0), (2, 0.5), (3, 0.3), (4, 0.15)))
        attack = min(int(0.02 * sr), length // 2)
        release = min(int(0.04 * sr), length - attack)
        env = np.ones(length)
        env[:attack] = np.linspace(0, 1, attack)
        if release:
            env[-release:] *= np.linspace(1, 0, release)
        env *= rng.uniform(0.7, 1.0)
        chunks.append(np.concatenate((tone * env, np.zeros(gap))))
    audio = np.concatenate(chunks) if chunks else np.zeros(0)
    audio += rng.normal(0, noise, len(audio))
    peak = np.max(np.abs(audio)) if len(audio) else 1.0
    return (audio / max(peak, 1e-9) * 0.8).astype(np.float32)
