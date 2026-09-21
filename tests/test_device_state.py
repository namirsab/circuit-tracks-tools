"""The connection listens to the device: project pads, Play/Stop, tempo from its clock.

Hardware findings (2026-09-21): the Circuit Tracks sends program_change on
channel 16 when a project pad is pressed, start/stop for its Play button and
MIDI clock at the project tempo all the time, even while stopped. SysEx
replies must still reach the transfer code, so the reader thread owns the
input port and hands SysEx on through a queue.
"""

import threading
import time

import mido

from circuit_tracks.midi import MidiConnection
from circuit_tracks.ncs_transfer import _SUBCMD_FILE_ENTRY, _SYSEX_HEADER, list_directory


class FakePort:
    """An input port that hands out queued messages, optionally on a schedule."""

    def __init__(self, messages=(), interval_s: float | None = None):
        self.messages = list(messages)
        self.interval_s = interval_s
        self.closed = False
        self._next = time.monotonic()

    def poll(self):
        if not self.messages:
            return None
        if self.interval_s is not None:
            now = time.monotonic()
            if now < self._next:
                return None
            self._next += self.interval_s
        return self.messages.pop(0)

    def close(self):
        self.closed = True


class ReplyingOut:
    """An output port whose device answers a request: replies land on the input port after the send."""

    def __init__(self, inp: FakePort, replies, when=lambda data: True):
        self.inp, self.replies, self.when, self.closed = inp, list(replies), when, False

    def send(self, msg):
        if msg.type == "sysex" and self.when(list(msg.data)) and self.replies:
            self.inp.messages.extend(self.replies)
            self.replies = []


def listening(port: FakePort) -> MidiConnection:
    midi = MidiConnection()
    midi._input_port = port
    midi._start_reader()
    return midi


def wait_until(cond, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def test_project_pads_and_play_button_update_the_state():
    port = FakePort(
        [
            mido.Message("program_change", channel=15, program=44),
            mido.Message("start"),
            mido.Message("stop"),
            mido.Message("program_change", channel=0, program=3),  # synth patch, not a project
        ]
    )
    midi = listening(port)
    try:
        assert wait_until(lambda: not port.messages)
        state = midi.device_state()
        assert state["project"] == 44
        assert state["playing"] is False
        assert state["bpm"] is None
    finally:
        midi._stop_reader()


def test_tempo_is_read_from_the_device_clock():
    port = FakePort([mido.Message("clock")] * 72, interval_s=60 / 120 / 24)  # 1.5 s at 120 BPM
    midi = listening(port)
    try:
        assert wait_until(lambda: not port.messages, timeout=3.0)
        bpm = midi.device_state()["bpm"]
        assert bpm is not None and abs(bpm - 120) < 4, bpm
        time.sleep(0.6)  # the device stopped sending clock (reboot, unplugged)
        assert midi.device_state()["bpm"] is None
    finally:
        midi._stop_reader()


def test_sysex_reaches_the_transfer_code_and_nothing_else_does():
    reply = [0, 32, 41, 1, 100, 3, 4, 0, 0]
    port = FakePort([mido.Message("clock"), mido.Message("sysex", data=reply), mido.Message("start")])
    midi = listening(port)
    try:
        assert midi.poll_sysex(1.0) == reply
        assert midi.poll_sysex(0.05) is None
        assert midi.device_state()["playing"] is True
    finally:
        midi._stop_reader()


def test_sysex_request_matches_a_reply_through_the_reader():
    reply = [0, 32, 41, 1, 100, 0, 7]
    port = FakePort([mido.Message("sysex", data=[0, 32, 41, 8])])  # stale reply, drained before the request
    midi = listening(port)
    midi._port = ReplyingOut(port, [mido.Message("sysex", data=[0, 32, 41, 9]), mido.Message("sysex", data=reply)])
    try:
        got = midi.sysex_request([0, 32, 41, 1, 100, 0x40], timeout_s=1.0, match_fn=lambda d: d[-1] == 7)
        assert got == reply
    finally:
        midi._stop_reader()


def test_list_directory_reads_entries_from_the_queue():
    entries = []
    for slot, name in ((0, "Bassline"), (5, "Drums")):
        entries.append(
            mido.Message(
                "sysex",
                data=_SYSEX_HEADER + [_SUBCMD_FILE_ENTRY, 3, slot >> 7, slot & 0x7F] + [ord(c) for c in name],
            )
        )
    port = FakePort()
    midi = listening(port)
    # The entries answer the listing request (DIR_CONTROL 0x0B with the file type), not the handshake
    midi._port = ReplyingOut(port, entries, when=lambda d: d[-3:] == [0x0B, 3, 0])
    try:
        got = list_directory(midi, file_type=3, timeout_s=2.0)
        assert [(e["slot"], e["filename"]) for e in got] == [(0, "Bassline"), (5, "Drums")]
    finally:
        midi._stop_reader()


def test_disconnect_stops_the_reader():
    port = FakePort([])
    midi = listening(port)
    thread = midi._reader
    assert isinstance(thread, threading.Thread) and thread.is_alive()
    midi.disconnect()
    assert not thread.is_alive()
