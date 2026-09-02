#!/usr/bin/env python
"""Generate synthetic "sung" melodies as WAV files for testing voice transcription.

Usage:
    venv/bin/python scripts/generate_voice_samples.py [output_dir]

Then feed a file to the MCP tool, e.g.
    transcribe_audio_file(file_path="example-audio/c_major_scale_120bpm.wav", bpm=120)

Requires the ``audio`` extra (numpy + soundfile).
"""

import sys
from pathlib import Path

from circuit_tracks.audio_io import save_audio
from circuit_tracks.transcribe import midi_to_name, synthesize_melody

SR = 22050

# name -> (bpm, notes as (midi or None, beats))
SAMPLES = {
    "c_major_scale_120bpm": (120, [(m, 1) for m in (60, 62, 64, 65, 67, 69, 71, 72)]),
    "repeated_e4_120bpm": (120, [(64, 0.5)] * 8),
    "twinkle_100bpm": (
        100,
        [(60, 1), (60, 1), (67, 1), (67, 1), (69, 1), (69, 1), (67, 2)]
        + [(65, 1), (65, 1), (64, 1), (64, 1), (62, 1), (62, 1), (60, 2)],
    ),
    "bassline_with_rests_90bpm": (
        90,
        [(57, 1), (None, 0.5), (57, 0.5), (60, 1), (None, 1), (55, 0.5), (57, 0.5), (60, 0.5), (64, 0.5)],
    ),
}


def main() -> None:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "example-audio")
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, (bpm, notes) in SAMPLES.items():
        beat_s = 60.0 / bpm
        audio = synthesize_melody([(m, beats * beat_s) for m, beats in notes], SR)
        path = out_dir / f"{name}.wav"
        save_audio(str(path), audio, SR)
        expected = " ".join(midi_to_name(m) if m is not None else "-" for m, _ in notes)
        print(f"{path}  bpm={bpm}  {len(audio) / SR:.1f}s  expected: {expected}")


if __name__ == "__main__":
    main()
