"""Shared fixtures: an in-process hardware MCP server on a mock MIDI connection."""

from __future__ import annotations

import time

import pytest


class MockMidi:
    """Records MIDI messages instead of sending them."""

    def __init__(self):
        self.messages: list[tuple] = []
        self.clock_times: list[float] = []
        self.is_connected = False
        self.has_input = True
        self.port_name = None
        self._input_port_name = None

    def connect(self, port_name):
        self.port_name = port_name
        self.is_connected = True

    def disconnect(self):
        self.is_connected = False
        self.port_name = None

    def note_on(self, channel, note, velocity=100):
        self.messages.append(("note_on", channel, note, velocity))

    def note_off(self, channel, note):
        self.messages.append(("note_off", channel, note))

    def control_change(self, channel, control, value):
        self.messages.append(("cc", channel, control, value))

    def nrpn(self, channel, msb, lsb, value):
        self.messages.append(("nrpn", channel, msb, lsb, value))

    def program_change(self, channel, program):
        self.messages.append(("pc", channel, program))

    def send_sysex(self, data):
        self.messages.append(("sysex", bytes(data)))

    def send_realtime(self, msg_type):
        self.messages.append(("realtime", msg_type))

    def send_clock(self):
        self.messages.append(("clock",))
        self.clock_times.append(time.perf_counter())

    def all_notes_off(self, channel):
        self.messages.append(("all_notes_off", channel))

    def receive_sysex(self, timeout=2.0):
        return None

    @staticmethod
    def list_output_ports():
        return ["Circuit Tracks MIDI", "IAC Driver Bus 1"]

    @staticmethod
    def list_input_ports():
        return ["Circuit Tracks MIDI"]


@pytest.fixture
def server(monkeypatch):
    """The real circuit_mcp server module with its globals swapped for a MockMidi."""
    import circuit_mcp.server as srv
    from circuit_tracks.morph import MorphEngine
    from circuit_tracks.sequencer import ClockGenerator, SequencerEngine

    midi = MockMidi()
    monkeypatch.setattr(srv, "_midi", midi)
    monkeypatch.setattr(srv, "_engine", SequencerEngine(midi))
    monkeypatch.setattr(srv, "_morph", MorphEngine(midi))
    monkeypatch.setattr(srv, "_clock", ClockGenerator(midi))
    monkeypatch.setattr(srv, "_current_song", None, raising=False)
    monkeypatch.setattr(srv, "_current_project_slot", None, raising=False)
    monkeypatch.setattr("circuit_tracks.midi.MidiConnection.list_output_ports", MockMidi.list_output_ports)
    monkeypatch.setattr("circuit_tracks.midi.MidiConnection.list_input_ports", MockMidi.list_input_ports)
    yield srv, midi
    srv._engine.stop()
    srv._clock.stop()


class FakeLiveSession:
    """Scripted stand-in for google-genai's AsyncSession."""

    def __init__(self, script, gap=0.02, tail=0.15):
        self.script = list(script)
        self.gap = gap
        self.tail = tail
        self.tool_responses = []
        self.realtime = []
        self.client_content = []
        self.closed = False

    async def _sleep(self, seconds):
        import asyncio

        end = asyncio.get_running_loop().time() + seconds
        while not self.closed and asyncio.get_running_loop().time() < end:
            await asyncio.sleep(0.01)

    async def receive(self):
        for msg in self.script:
            if self.closed:
                return
            yield msg
            await self._sleep(self.gap)
        await self._sleep(self.tail)

    async def send_tool_response(self, *, function_responses):
        self.tool_responses.extend(function_responses)

    async def send_realtime_input(self, **kw):
        self.realtime.append(kw)

    async def send_client_content(self, **kw):
        self.client_content.append(kw)

    async def close(self):
        self.closed = True


class FakeConnector:
    """connect(config=...) -> async context manager yielding the next scripted session."""

    def __init__(self, sessions):
        self.sessions = list(sessions)
        self.configs = []

    def __call__(self, *, config):
        from contextlib import asynccontextmanager

        self.configs.append(config)
        session = self.sessions.pop(0)

        @asynccontextmanager
        async def cm():
            yield session

        return cm()
