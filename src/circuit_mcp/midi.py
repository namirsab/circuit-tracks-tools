"""MIDI connection management for Novation Circuit Tracks."""

import time

import mido


class MidiConnection:
    """Manages a MIDI output connection to the Circuit Tracks."""

    def __init__(self) -> None:
        self._port: mido.ports.BaseOutput | None = None
        self._port_name: str | None = None

    @property
    def is_connected(self) -> bool:
        return self._port is not None and not self._port.closed

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

    def disconnect(self) -> None:
        if self._port is not None:
            self._port.close()
            self._port = None
            self._port_name = None

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
        self.control_change(channel, 99, msb)   # NRPN MSB
        self.control_change(channel, 98, lsb)   # NRPN LSB
        self.control_change(channel, 6, value)   # Data Entry MSB
        self.control_change(channel, 38, 0)      # Data Entry LSB

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
