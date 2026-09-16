"""Audio helpers: resampling, framing, player queue, earcons. No device is opened."""

from __future__ import annotations

import sys

import numpy as np

from circuit_voice import audio
from circuit_voice.audio import IN_RATE, OUT_RATE, Framer, Player, earcon, resample


def test_resample_ratio_and_dtype():
    src = (np.sin(np.linspace(0, 200 * np.pi, 48000)) * 10000).astype(np.int16)
    out = resample(src, 48000, IN_RATE)
    assert out.dtype == np.int16
    assert abs(len(out) - 16000) <= 1
    same = resample(src, 48000, 48000)
    assert same is src


def test_framer_yields_20ms_frames_and_keeps_remainder():
    f = Framer(frame_bytes=640)
    frames = list(f.push(b"\x00" * 1000))
    assert [len(x) for x in frames] == [640]
    frames = list(f.push(b"\x00" * 300))
    assert [len(x) for x in frames] == [640]
    assert len(f.buffer) == 20


def test_player_queue_and_flush():
    p = Player(rate=OUT_RATE, stream_factory=lambda **kw: None)
    p.play(np.ones(100, dtype=np.int16).tobytes())
    p.play(np.ones(100, dtype=np.int16).tobytes())
    out = p.pull(150)
    assert len(out) == 150 and out[0] == 1 and out[-1] == 1
    p.flush()
    out = p.pull(100)
    assert len(out) == 100 and not out.any()
    assert p.queued_samples() == 0


def test_earcons_are_short_and_distinct():
    kinds = ["listen_start", "listen_stop", "success", "error"]
    lengths = {k: len(earcon(k)) / OUT_RATE for k in kinds}
    for k, seconds in lengths.items():
        assert 0.03 <= seconds <= 0.25, k
    assert not np.array_equal(earcon("listen_start"), earcon("listen_stop"))
    assert earcon("listen_start").dtype == np.int16


def test_module_imports_without_sounddevice(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    import importlib

    importlib.reload(audio)
    assert audio.IN_RATE == 16000
    importlib.reload(audio)
