"""Microphone capture and audio file I/O for voice transcription.

Requires the optional ``audio`` extra (``pip install 'circuit-tracks-tools[audio]'``).
Dependencies are imported lazily so the rest of the library works without them.
"""

from __future__ import annotations

import importlib

import numpy as np

_INSTALL_HINT = "Audio support requires the optional 'audio' extra: pip install 'circuit-tracks-tools[audio]'"


def _need(module: str):
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise RuntimeError(f"{_INSTALL_HINT} ({exc})") from exc


def load_audio(path: str) -> tuple[np.ndarray, int]:
    """Read an audio file (wav/flac/aiff/ogg) as mono float32. Returns ``(audio, samplerate)``."""
    sf = _need("soundfile")
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    return data.mean(axis=1).astype(np.float32), int(sr)


def save_audio(path: str, audio: np.ndarray, sr: int) -> None:
    """Write mono float audio as 16-bit PCM."""
    sf = _need("soundfile")
    sf.write(path, np.asarray(audio, dtype=np.float32), sr, subtype="PCM_16")


def list_input_devices() -> list[dict]:
    """List audio input devices. Each entry has index, name, channels, samplerate, default."""
    sd = _need("sounddevice")
    default_in = sd.default.device[0]
    out = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) <= 0:
            continue
        out.append(
            {
                "index": idx,
                "name": dev["name"],
                "channels": dev["max_input_channels"],
                "samplerate": int(dev["default_samplerate"]),
                "default": idx == default_in,
            }
        )
    return out


def record(seconds: float, samplerate: int | None = None, device: int | None = None) -> tuple[np.ndarray, int]:
    """Record mono audio from the microphone (blocking). Returns ``(audio, samplerate)``.

    When ``samplerate`` is omitted the device's native rate is used, which every
    device supports; the transcriber is sample-rate agnostic.
    """
    sd = _need("sounddevice")
    if samplerate is None:
        samplerate = int(sd.query_devices(device, "input")["default_samplerate"])
    frames = int(seconds * samplerate)
    data = sd.rec(frames, samplerate=samplerate, channels=1, dtype="float32", device=device)
    sd.wait()
    return data[:, 0].copy(), samplerate
