"""Pattern-based step sequencer for the Circuit Tracks.

Mirrors the Circuit Tracks' native sequencer model: patterns contain tracks,
tracks contain steps. Runs in a background thread, sending MIDI notes and
clock with precise timing.
"""

import random
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum

from circuit_tracks.constants import DRUM_NOTES, MIDI1_CHANNEL, MIDI2_CHANNEL
from circuit_tracks.midi import MidiConnection


class TrackType(Enum):
    SYNTH1 = "synth1"
    SYNTH2 = "synth2"
    DRUM1 = "drum1"
    DRUM2 = "drum2"
    DRUM3 = "drum3"
    DRUM4 = "drum4"
    MIDI1 = "midi1"
    MIDI2 = "midi2"


TRACK_CHANNEL = {
    TrackType.SYNTH1: 0,
    TrackType.SYNTH2: 1,
    TrackType.DRUM1: 9,
    TrackType.DRUM2: 9,
    TrackType.DRUM3: 9,
    TrackType.DRUM4: 9,
    TrackType.MIDI1: MIDI1_CHANNEL,
    TrackType.MIDI2: MIDI2_CHANNEL,
}

DRUM_TRACK_NOTE = {
    TrackType.DRUM1: DRUM_NOTES[1],
    TrackType.DRUM2: DRUM_NOTES[2],
    TrackType.DRUM3: DRUM_NOTES[3],
    TrackType.DRUM4: DRUM_NOTES[4],
}

VALID_TRACK_NAMES = {t.value for t in TrackType}


@dataclass
class Step:
    notes: list[int] = field(default_factory=lambda: [60])
    velocity: int = 100
    gate: float = 0.5
    enabled: bool = True
    probability: float = 1.0
    sample: int | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Step":
        s = cls()
        if "note" in d:
            s.notes = [d["note"]]
        if "notes" in d:
            s.notes = list(d["notes"])
        if "velocity" in d:
            s.velocity = int(d["velocity"])
        if "gate" in d:
            s.gate = float(d["gate"])
        if "enabled" in d:
            s.enabled = bool(d["enabled"])
        if "probability" in d:
            s.probability = float(d["probability"])
        if "sample" in d:
            s.sample = int(d["sample"])
        return s

    def to_dict(self) -> dict:
        d: dict = {"notes": self.notes, "velocity": self.velocity, "gate": self.gate}
        if not self.enabled:
            d["enabled"] = False
        if self.probability < 1.0:
            d["probability"] = self.probability
        if self.sample is not None:
            d["sample"] = self.sample
        return d


@dataclass
class Track:
    track_type: TrackType
    steps: dict[int, Step] = field(default_factory=dict)
    muted: bool = False
    num_steps: int = 16

    def to_dict(self) -> dict:
        return {
            "steps": {str(k): v.to_dict() for k, v in sorted(self.steps.items())},
            "muted": self.muted,
            "num_steps": self.num_steps,
        }


@dataclass
class Pattern:
    tracks: dict[str, Track] = field(default_factory=dict)
    length: int = 16

    def __post_init__(self):
        for tt in TrackType:
            if tt.value not in self.tracks:
                self.tracks[tt.value] = Track(track_type=tt, num_steps=self.length)

    def to_dict(self) -> dict:
        return {
            "length": self.length,
            "tracks": {name: track.to_dict() for name, track in self.tracks.items()},
        }


class ClockGenerator:
    """Standalone MIDI clock generator that sends 24ppqn timing clock."""

    def __init__(self, midi: MidiConnection):
        self._midi = midi
        self._bpm: float = 120.0
        self._running: bool = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, bpm: float = 120.0) -> None:
        self.stop()
        with self._lock:
            self._bpm = bpm
            self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._stop_event.set()
            self._thread.join(timeout=3)
        self._thread = None
        with self._lock:
            self._running = False

    def set_bpm(self, bpm: float) -> None:
        with self._lock:
            self._bpm = bpm

    def _run(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                bpm = self._bpm
            # 24 pulses per quarter note
            interval = 60.0 / bpm / 24.0
            start = time.perf_counter()
            try:
                self._midi.send_clock()
            except Exception:
                pass
            elapsed = time.perf_counter() - start
            wait = interval - elapsed
            if wait > 0:
                self._stop_event.wait(wait)
        with self._lock:
            self._running = False


class SequencerEngine:
    def __init__(self, midi: MidiConnection):
        self._midi = midi
        self._patterns: dict[str, Pattern] = {}
        self._current_pattern: str = ""
        self._pattern_queue: list[str] = []
        self._bpm: float = 120.0
        self._running: bool = False
        self._send_clock: bool = True
        self._track_mutes: dict[str, bool] = {t.value: False for t in TrackType}
        self._current_step: int = 0
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    def get_status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "current_pattern": self._current_pattern,
                "current_step": self._current_step,
                "bpm": self._bpm,
                "send_clock": self._send_clock,
                "track_mutes": dict(self._track_mutes),
                "pattern_queue": list(self._pattern_queue),
                "patterns_defined": sorted(self._patterns.keys()),
            }

    def set_pattern(self, name: str, pattern: Pattern) -> None:
        with self._lock:
            self._patterns[name] = pattern

    def get_pattern(self, name: str) -> Pattern | None:
        with self._lock:
            p = self._patterns.get(name)
            return deepcopy(p) if p is not None else None

    def clear_pattern(self, name: str) -> None:
        with self._lock:
            self._patterns.pop(name, None)

    def list_patterns(self) -> list[str]:
        with self._lock:
            return sorted(self._patterns.keys())

    def set_track(self, pattern_name: str, track_name: str, steps: dict[int, Step], clear: bool = True) -> None:
        with self._lock:
            if pattern_name not in self._patterns:
                self._patterns[pattern_name] = Pattern()
            pattern = self._patterns[pattern_name]
            if track_name not in pattern.tracks:
                return
            track = pattern.tracks[track_name]
            if clear:
                track.steps = steps
            else:
                track.steps.update(steps)

    def set_bpm(self, bpm: float) -> None:
        with self._lock:
            self._bpm = bpm

    def queue_patterns(self, names: list[str]) -> None:
        """Queue one or more patterns to play after the current one finishes."""
        with self._lock:
            self._pattern_queue.extend(names)

    def clear_queue(self) -> None:
        with self._lock:
            self._pattern_queue.clear()

    def set_queue(self, names: list[str]) -> None:
        """Replace the entire pattern queue."""
        with self._lock:
            self._pattern_queue = list(names)

    def set_mute(self, track_name: str, muted: bool) -> None:
        with self._lock:
            self._track_mutes[track_name] = muted
        if muted:
            track_type = TrackType(track_name)
            channel = TRACK_CHANNEL[track_type]
            try:
                self._midi.all_notes_off(channel)
            except Exception:
                pass

    def set_mutes(self, mutes: dict[str, bool]) -> None:
        for name, muted in mutes.items():
            self.set_mute(name, muted)

    def start(self, pattern_name: str, bpm: float = 120.0, send_clock: bool = True) -> None:
        self.stop()
        with self._lock:
            self._current_pattern = pattern_name
            self._bpm = bpm
            self._send_clock = send_clock
            self._running = True
            self._current_step = 0
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._stop_event.set()
            self._thread.join(timeout=3)
        self._thread = None
        with self._lock:
            self._running = False
        # All notes off on all used channels
        for ch in (0, 1, 9):
            try:
                self._midi.all_notes_off(ch)
            except Exception:
                pass

    def _run(self) -> None:
        step_index = 0
        pending_offs: list[tuple[float, int, int]] = []  # (off_time, channel, note)

        while not self._stop_event.is_set():
            with self._lock:
                bpm = self._bpm
                send_clock = self._send_clock
                pattern = deepcopy(self._patterns.get(self._current_pattern, Pattern()))
                mutes = dict(self._track_mutes)
                queue = list(self._pattern_queue)

            # Handle pattern boundary
            if step_index >= pattern.length:
                step_index = 0
                if queue:
                    with self._lock:
                        if self._pattern_queue:
                            next_name = self._pattern_queue.pop(0)
                            self._current_pattern = next_name
                            pattern = deepcopy(self._patterns.get(next_name, Pattern()))

            with self._lock:
                self._current_step = step_index

            step_duration_s = 60.0 / bpm / 4.0  # 16th note
            step_start = time.perf_counter()

            # Process note-offs from previous step that are now due
            now = time.perf_counter()
            still_pending = []
            for off_time, off_ch, off_note in pending_offs:
                if now >= off_time:
                    try:
                        self._midi.note_off(off_ch, off_note)
                    except Exception:
                        pass
                else:
                    still_pending.append((off_time, off_ch, off_note))
            pending_offs = still_pending

            # Play notes for this step
            for track_name, track in pattern.tracks.items():
                if mutes.get(track_name, False) or track.muted:
                    continue
                step = track.steps.get(step_index)
                if step is None or not step.enabled:
                    continue
                if step.probability < 1.0 and random.random() > step.probability:
                    continue

                track_type = TrackType(track_name)
                channel = TRACK_CHANNEL[track_type]

                if track_type in DRUM_TRACK_NOTE:
                    notes_to_play = [DRUM_TRACK_NOTE[track_type]]
                else:
                    notes_to_play = step.notes

                gate_duration = step.gate * step_duration_s
                off_time = step_start + gate_duration

                for note in notes_to_play:
                    try:
                        self._midi.note_on(channel, note, step.velocity)
                    except Exception:
                        pass
                    pending_offs.append((off_time, channel, note))

            # Send 6 clock ticks across this step (24 ppqn)
            clock_interval = step_duration_s / 6.0
            for tick in range(6):
                if self._stop_event.is_set():
                    break

                if send_clock:
                    try:
                        self._midi.send_clock()
                    except Exception:
                        pass

                # Process pending note-offs
                now = time.perf_counter()
                still_pending = []
                for off_time, off_ch, off_note in pending_offs:
                    if now >= off_time:
                        try:
                            self._midi.note_off(off_ch, off_note)
                        except Exception:
                            pass
                    else:
                        still_pending.append((off_time, off_ch, off_note))
                pending_offs = still_pending

                # Sleep until next clock tick
                target = step_start + (tick + 1) * clock_interval
                wait = target - time.perf_counter()
                if wait > 0:
                    self._stop_event.wait(wait)

            step_index += 1

        # Drain any remaining note-offs
        for off_time, off_ch, off_note in pending_offs:
            try:
                self._midi.note_off(off_ch, off_note)
            except Exception:
                pass

        with self._lock:
            self._running = False
