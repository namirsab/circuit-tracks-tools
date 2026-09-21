"""The MIDI clock must run at the requested tempo, not slower.

Each tick used to wait ``interval - send_time`` from a fresh timestamp, so the
scheduling overshoot of every wait (1-3 ms on macOS) accumulated: 120 BPM came
out near 100 on the device. Ticks are now scheduled at absolute times.
"""

import time

import pytest

from circuit_tracks.sequencer import ClockGenerator


class TickCounter:
    def __init__(self):
        self.ticks = 0

    def send_clock(self):
        self.ticks += 1


def measured_bpm(bpm: float, seconds: float) -> float:
    port = TickCounter()
    clock = ClockGenerator(port)
    clock.start(bpm)
    time.sleep(seconds)
    clock.stop()
    return port.ticks / 24 / seconds * 60


@pytest.mark.parametrize("bpm", [90.0, 120.0, 174.0])
def test_clock_tick_rate_matches_the_tempo(bpm):
    got = measured_bpm(bpm, 2.0)
    assert abs(got - bpm) / bpm < 0.02, f"asked {bpm}, sent {got:.1f}"


def test_set_bpm_while_running_changes_the_rate():
    port = TickCounter()
    clock = ClockGenerator(port)
    clock.start(60.0)
    time.sleep(1.0)
    before = port.ticks
    clock.set_bpm(120.0)
    time.sleep(1.0)
    clock.stop()
    later = port.ticks - before
    assert 40 <= later <= 56, later  # 48 ticks/s at 120 BPM


def test_stop_returns_promptly():
    clock = ClockGenerator(TickCounter())
    clock.start(30.0)  # 83 ms between ticks
    time.sleep(0.05)
    t0 = time.perf_counter()
    clock.stop()
    assert time.perf_counter() - t0 < 0.5
    assert not clock.is_running
