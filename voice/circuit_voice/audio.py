"""Microphone capture, speaker playback and earcons.

Gemini Live takes 16 kHz mono 16-bit PCM and returns 24 kHz. ``MicStream``
yields 20 ms frames at 16 kHz (resampling when the device refuses that rate),
``Player`` plays what the model says and can ``flush`` on an interruption,
``earcon`` makes the short cues that tell a blind user the agent is listening.
``sounddevice`` is imported lazily so this module loads on machines without
PortAudio (CI) and the pure helpers stay testable.
"""

from __future__ import annotations

import asyncio
import collections
import math
import sys
from collections.abc import AsyncIterator, Callable
from typing import Any

import numpy as np

IN_RATE = 16000
OUT_RATE = 24000
FRAME_MS = 20
IN_FRAME_BYTES = IN_RATE * FRAME_MS // 1000 * 2  # 640 bytes of int16 mono


def _sd():
    import sounddevice as sd  # noqa: PLC0415 - lazy on purpose

    return sd


def resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear-interpolation resample of int16 mono audio (good enough for speech)."""
    if src_rate == dst_rate:
        return samples
    n_out = int(round(len(samples) * dst_rate / src_rate))
    x_old = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, samples.astype(np.float32)).astype(np.int16)


class Framer:
    """Cuts a byte stream into fixed-size frames, keeping the remainder."""

    def __init__(self, frame_bytes: int = IN_FRAME_BYTES):
        self.frame_bytes = frame_bytes
        self.buffer = b""

    def push(self, data: bytes):
        self.buffer += data
        while len(self.buffer) >= self.frame_bytes:
            yield self.buffer[: self.frame_bytes]
            self.buffer = self.buffer[self.frame_bytes :]


def _tone(freq: float, seconds: float, volume: float = 0.3, rate: int = OUT_RATE) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    env = np.minimum(1.0, np.minimum(t, seconds - t) / 0.01)  # 10 ms fade in/out against clicks
    return (np.sin(2 * math.pi * freq * t) * env * volume * 32767).astype(np.int16)


def earcon(kind: str, rate: int = OUT_RATE) -> np.ndarray:
    """Short audio cues: listen_start (rising), listen_stop (falling), success (tick), error (buzz)."""
    if kind == "listen_start":
        return np.concatenate([_tone(660, 0.07, rate=rate), _tone(990, 0.09, rate=rate)])
    if kind == "listen_stop":
        return np.concatenate([_tone(990, 0.07, rate=rate), _tone(660, 0.09, rate=rate)])
    if kind == "success":
        return _tone(1320, 0.05, volume=0.2, rate=rate)
    if kind == "error":
        t = np.arange(int(rate * 0.2)) / rate
        square = np.sign(np.sin(2 * math.pi * 180 * t))
        return (square * 0.15 * 32767).astype(np.int16)
    raise ValueError(f"unknown earcon {kind!r}")


class Player:
    """Queue of int16 samples drained by the output stream callback."""

    def __init__(self, rate: int = OUT_RATE, device: Any = None, stream_factory: Callable[..., Any] | None = None):
        self.rate = rate
        self.device = device
        self._queue: collections.deque[np.ndarray] = collections.deque()
        self._stream_factory = stream_factory
        self._stream: Any = None

    def start(self) -> None:
        factory = self._stream_factory or self._default_stream
        self._stream = factory(samplerate=self.rate, channels=1, dtype="int16", device=self.device, callback=self._cb)
        if self._stream is not None:
            self._stream.start()

    def _default_stream(self, **kw):
        return _sd().OutputStream(**kw)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def play(self, pcm: bytes | np.ndarray) -> None:
        samples = np.frombuffer(pcm, dtype=np.int16) if isinstance(pcm, bytes | bytearray) else pcm
        if len(samples):
            self._queue.append(samples)

    def play_earcon(self, kind: str) -> None:
        self.play(earcon(kind, self.rate))

    def flush(self) -> None:
        """Drop everything queued (the user interrupted the model)."""
        self._queue.clear()

    def queued_samples(self) -> int:
        return sum(len(a) for a in self._queue)

    def pull(self, n: int) -> np.ndarray:
        """Take n samples from the queue, zero-padded when it runs dry."""
        out = np.zeros(n, dtype=np.int16)
        pos = 0
        while pos < n and self._queue:
            head = self._queue[0]
            take = min(n - pos, len(head))
            out[pos : pos + take] = head[:take]
            pos += take
            if take == len(head):
                self._queue.popleft()
            else:
                self._queue[0] = head[take:]
        return out

    def _cb(self, outdata, frames, time_info, status):  # noqa: ARG002 - PortAudio signature
        outdata[:, 0] = self.pull(frames)


class MicStream:
    """Async iterator of 20 ms int16 frames at 16 kHz from the default or given input device."""

    def __init__(self, device: Any = None, loop: asyncio.AbstractEventLoop | None = None):
        self.device = device
        self._loop = loop
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
        self._stream: Any = None
        self._rate = IN_RATE
        self._framer = Framer()

    def start(self) -> None:
        sd = _sd()
        self._loop = self._loop or asyncio.get_running_loop()
        try:
            self._stream = sd.InputStream(
                samplerate=IN_RATE, channels=1, dtype="int16", device=self.device, callback=self._cb
            )
            self._stream.start()
            self._rate = IN_RATE
        except Exception:  # noqa: BLE001 - fall back to the device's native rate
            info = sd.query_devices(self.device, "input")
            self._rate = int(info["default_samplerate"])
            self._stream = sd.InputStream(
                samplerate=self._rate, channels=1, dtype="int16", device=self.device, callback=self._cb
            )
            self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _cb(self, indata, frames, time_info, status):  # noqa: ARG002 - PortAudio signature
        samples = indata[:, 0].copy()
        if self._rate != IN_RATE:
            samples = resample(samples, self._rate, IN_RATE)
        for frame in self._framer.push(samples.tobytes()):
            try:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, frame)
            except RuntimeError:
                return

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self

    async def __anext__(self) -> bytes:
        return await self._queue.get()


def list_devices() -> str:
    return str(_sd().query_devices())


def _loopback(seconds: float) -> None:
    """Manual check: record, then play back through the Player."""
    sd = _sd()
    print(f"recording {seconds}s ...")
    rec = sd.rec(int(seconds * IN_RATE), samplerate=IN_RATE, channels=1, dtype="int16")
    sd.wait()
    player = Player()
    player.start()
    player.play_earcon("listen_stop")
    player.play(resample(rec[:, 0], IN_RATE, OUT_RATE))
    player.play_earcon("success")
    while player.queued_samples():
        sd.sleep(50)
    sd.sleep(100)
    player.stop()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--loopback":
        _loopback(float(sys.argv[2]))
    else:
        print(list_devices())
