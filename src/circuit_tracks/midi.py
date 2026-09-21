"""MIDI connection management for Novation Circuit Tracks."""

import queue
import threading
import time
from collections import deque
from collections.abc import Callable

import mido

# Project select is Program Change on MIDI channel 16 (0-based: 15), sent by the
# device when a project pad is pressed and received to select one.
PROJECT_CHANNEL = 15
# The device sends MIDI clock at the project tempo all the time (Clock Tx on);
# the tempo is read from ticks within this window and forgotten after a gap.
_CLOCK_WINDOW_S = 1.0
_CLOCK_GAP_S = 0.5


class MidiConnection:
    """Manages MIDI input/output connections to the Circuit Tracks.

    When an input port is open, a reader thread owns it: SysEx messages are
    queued for ``poll_sysex`` (used by the transfer code), everything else
    updates ``device_state()`` — the project the device has loaded, whether
    it is playing and the tempo of its clock.
    """

    def __init__(self) -> None:
        self._port: mido.ports.BaseOutput | None = None
        self._input_port: mido.ports.BaseInput | None = None
        self._port_name: str | None = None
        self._input_port_name: str | None = None
        self._sysex: queue.Queue[list[int]] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._reader_stop = threading.Event()
        self._state_lock = threading.Lock()
        self._project: int | None = None
        self._playing: bool | None = None
        self._clock_ticks: deque[float] = deque(maxlen=200)
        self._last_seen: float | None = None

    @property
    def is_connected(self) -> bool:
        return self._port is not None and not self._port.closed

    @property
    def has_input(self) -> bool:
        return self._input_port is not None and not self._input_port.closed

    @property
    def port_name(self) -> str | None:
        return self._port_name

    @staticmethod
    def list_output_ports() -> list[str]:
        return mido.get_output_names()

    @staticmethod
    def list_input_ports() -> list[str]:
        return mido.get_input_names()

    def connect(self, port_name: str) -> None:
        if self.is_connected:
            self.disconnect()
        self._port = mido.open_output(port_name)
        self._port_name = port_name
        # Auto-open matching input port for bidirectional communication
        input_ports = mido.get_input_names()
        for ip in input_ports:
            if port_name in ip or ip in port_name:
                try:
                    self._input_port = mido.open_input(ip)
                    self._input_port_name = ip
                    self._start_reader()
                except Exception:
                    pass  # Input port not available, not critical
                break

    def disconnect(self) -> None:
        self._stop_reader()
        if self._input_port is not None:
            self._input_port.close()
            self._input_port = None
            self._input_port_name = None
        if self._port is not None:
            self._port.close()
            self._port = None
            self._port_name = None

    # --- input reader -------------------------------------------------------

    def _start_reader(self) -> None:
        self._stop_reader()
        self._reader_stop.clear()
        self._reader = threading.Thread(target=self._read_loop, name="circuit-midi-in", daemon=True)
        self._reader.start()

    def _stop_reader(self) -> None:
        if self._reader is not None and self._reader.is_alive():
            self._reader_stop.set()
            self._reader.join(timeout=2)
        self._reader = None

    def _read_loop(self) -> None:
        port = self._input_port
        while not self._reader_stop.is_set():
            try:
                msg = port.poll()
            except Exception:
                break  # port closed underneath us
            if msg is None:
                time.sleep(0.002)
                continue
            self._handle(msg)

    def _handle(self, msg: mido.Message) -> None:
        now = time.monotonic()
        if msg.type == "sysex":
            self._sysex.put(list(msg.data))
            return
        with self._state_lock:
            self._last_seen = now
            if msg.type == "clock":
                self._clock_ticks.append(now)
            elif msg.type in ("start", "continue"):
                self._playing = True
            elif msg.type == "stop":
                self._playing = False
            elif msg.type == "program_change" and msg.channel == PROJECT_CHANNEL:
                self._project = msg.program % 64  # 64-127 would be a queued select

    def device_state(self) -> dict:
        """What the device last told us: project (0-63), playing, bpm from its clock."""
        now = time.monotonic()
        with self._state_lock:
            ticks = [t for t in self._clock_ticks if now - t <= _CLOCK_WINDOW_S]
            bpm = None
            if len(ticks) >= 12 and now - ticks[-1] <= _CLOCK_GAP_S:
                bpm = (len(ticks) - 1) / (ticks[-1] - ticks[0]) / 24 * 60
            return {
                "project": self._project,
                "playing": self._playing,
                "bpm": round(bpm, 1) if bpm else None,
                "last_seen_s": round(now - self._last_seen, 1) if self._last_seen else None,
            }

    def poll_sysex(self, timeout_s: float) -> list[int] | None:
        """Next SysEx message from the device (without F0/F7), or None after the timeout."""
        try:
            return self._sysex.get(timeout=max(timeout_s, 0.0))
        except queue.Empty:
            return None

    def drain_sysex(self) -> None:
        while True:
            try:
                self._sysex.get_nowait()
            except queue.Empty:
                return

    def _ensure_connected(self) -> None:
        if not self.is_connected:
            raise RuntimeError("Not connected to any MIDI device. Call connect() first.")

    def send(self, msg: mido.Message) -> None:
        self._ensure_connected()
        self._port.send(msg)

    def note_on(self, channel: int, note: int, velocity: int = 100) -> None:
        self.send(mido.Message("note_on", channel=channel, note=note, velocity=velocity))

    def note_off(self, channel: int, note: int) -> None:
        self.send(mido.Message("note_off", channel=channel, note=note, velocity=0))

    def play_note(self, channel: int, note: int, velocity: int = 100, duration_s: float = 0.5) -> None:
        self.note_on(channel, note, velocity)
        time.sleep(duration_s)
        self.note_off(channel, note)

    def control_change(self, channel: int, control: int, value: int) -> None:
        self.send(mido.Message("control_change", channel=channel, control=control, value=value))

    def nrpn(self, channel: int, msb: int, lsb: int, value: int) -> None:
        """Send an NRPN message (4 CC messages)."""
        self.control_change(channel, 99, msb)  # NRPN MSB
        self.control_change(channel, 98, lsb)  # NRPN LSB
        self.control_change(channel, 6, value)  # Data Entry MSB
        self.control_change(channel, 38, 0)  # Data Entry LSB

    def program_change(self, channel: int, program: int) -> None:
        self.send(mido.Message("program_change", channel=channel, program=program))

    def send_sysex(self, data: list[int]) -> None:
        self.send(mido.Message("sysex", data=data))

    def send_clock(self) -> None:
        """Send a single MIDI timing clock pulse."""
        self.send(mido.Message("clock"))

    def all_notes_off(self, channel: int) -> None:
        """Send All Notes Off (CC 123) on a channel."""
        self.control_change(channel, 123, 0)

    def send_realtime(self, msg_type: str) -> None:
        """Send a MIDI realtime message (start, stop, continue)."""
        if msg_type not in ("start", "stop", "continue"):
            raise ValueError(f"Invalid realtime message type: {msg_type}")
        self.send(mido.Message(msg_type))

    def sysex_request(
        self,
        request_data: list[int],
        timeout_s: float = 3.0,
        match_fn: Callable[[list[int]], bool] | None = None,
    ) -> list[int] | None:
        """Send a SysEx message and wait for a matching response.

        Args:
            request_data: SysEx data to send (without F0/F7).
            timeout_s: Max seconds to wait for response.
            match_fn: Optional function(data) -> bool to match the response.
                      If None, returns the first SysEx message received.

        Returns:
            The response SysEx data (without F0/F7), or None on timeout.
        """
        if not self.has_input:
            raise RuntimeError("No MIDI input port available. Reconnect to a port that has both input and output.")

        self.drain_sysex()
        self.send_sysex(request_data)

        # Wait for matching response
        deadline = time.monotonic() + timeout_s
        while (remaining := deadline - time.monotonic()) > 0:
            data = self.poll_sysex(remaining)
            if data is not None and (match_fn is None or match_fn(data)):
                return data
        return None
