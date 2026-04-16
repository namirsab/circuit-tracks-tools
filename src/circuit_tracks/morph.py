"""Parameter morphing engine for smooth CC/NRPN transitions."""

import threading
import time

from circuit_tracks.midi import MidiConnection


def _resolve_param(
    param_name: str, cc_maps: list[dict], nrpn_maps: list[dict]
) -> tuple[str, int | tuple[int, int]] | None:
    """Find a param in the given CC/NRPN lookup dicts.

    Returns ('cc', cc_num) or ('nrpn', (msb, lsb)) or None.
    """
    for cc_map in cc_maps:
        if param_name in cc_map:
            return ("cc", cc_map[param_name])
    for nrpn_map in nrpn_maps:
        if param_name in nrpn_map:
            return ("nrpn", nrpn_map[param_name])
    return None


def _send_params_at_t(
    midi: MidiConnection,
    channel: int,
    start_values: dict[str, int],
    target: dict[str, int],
    t: float,
    cc_maps: list[dict],
    nrpn_maps: list[dict],
):
    """Send interpolated param values at position t (0.0 to 1.0)."""
    for param_name, target_val in target.items():
        start_val = start_values[param_name]
        current = round(start_val + (target_val - start_val) * t)
        current = max(0, min(127, current))
        resolved = _resolve_param(param_name, cc_maps, nrpn_maps)
        if resolved is None:
            continue
        kind, addr = resolved
        if kind == "cc":
            midi.control_change(channel, addr, current)
        else:
            msb, lsb = addr
            midi.nrpn(channel, msb, lsb, current)


class MorphEngine:
    """Manages concurrent parameter morph threads.

    Smoothly interpolates MIDI CC/NRPN parameters over time in background
    threads. Supports one-shot and ping-pong (LFO-like) modes.
    """

    def __init__(self, midi: MidiConnection):
        self._midi = midi
        self._threads: dict[str, threading.Event] = {}  # morph_id -> stop_event
        self._counter = 0

    @property
    def active_morphs(self) -> list[str]:
        """List of currently running morph IDs."""
        return list(self._threads.keys())

    def next_id(self) -> str:
        """Generate an auto-incremented morph name."""
        self._counter += 1
        return f"morph_{self._counter}"

    def start(
        self,
        morph_id: str,
        channel: int,
        start: dict[str, int],
        target: dict[str, int],
        duration_seconds: float,
        ping_pong: bool,
        cc_maps: list[dict],
        nrpn_maps: list[dict],
    ) -> str | None:
        """Start a parameter morph.

        Args:
            morph_id: Unique identifier for this morph.
            channel: MIDI channel (0-indexed).
            start: Starting param values {name: value}.
            target: Target param values {name: value}.
            duration_seconds: Duration for one sweep direction.
            ping_pong: If True, sweep back and forth continuously.
            cc_maps: List of CC lookup dicts to resolve param names.
            nrpn_maps: List of NRPN lookup dicts to resolve param names.

        Returns:
            Error string if validation fails, None on success.
        """
        # Validate all params exist
        errors = []
        for param_name in set(list(start.keys()) + list(target.keys())):
            if _resolve_param(param_name, cc_maps, nrpn_maps) is None:
                errors.append(param_name)
        if errors:
            return f"Unknown params: {', '.join(sorted(errors))}"

        if set(start.keys()) != set(target.keys()):
            return "start and target must have the same parameter names."

        # If a morph with this exact ID exists, stop it
        if morph_id in self._threads:
            self._threads[morph_id].set()

        # ~20 updates per second for smooth morphing
        total_steps = max(1, int(duration_seconds * 20))

        # Set start values immediately
        for param_name, value in start.items():
            resolved = _resolve_param(param_name, cc_maps, nrpn_maps)
            if resolved is None:
                continue
            kind, addr = resolved
            if kind == "cc":
                self._midi.control_change(channel, addr, value)
            else:
                msb, lsb = addr
                self._midi.nrpn(channel, msb, lsb, value)

        stop_event = threading.Event()
        self._threads[morph_id] = stop_event

        thread = threading.Thread(
            target=self._run,
            args=(
                morph_id,
                channel,
                start,
                target,
                duration_seconds,
                total_steps,
                stop_event,
                ping_pong,
                cc_maps,
                nrpn_maps,
            ),
            daemon=True,
        )
        thread.start()
        return None

    def stop(self, morph_id: str) -> bool:
        """Stop a specific morph by ID. Returns True if found."""
        if morph_id in self._threads:
            self._threads[morph_id].set()
            del self._threads[morph_id]
            return True
        return False

    def stop_by_prefix(self, prefix: str) -> list[str]:
        """Stop all morphs whose ID starts with prefix. Returns stopped IDs."""
        stopped = []
        for morph_id in list(self._threads.keys()):
            if morph_id.startswith(prefix):
                self._threads[morph_id].set()
                del self._threads[morph_id]
                stopped.append(morph_id)
        return stopped

    def stop_by_name(self, name: str) -> list[str]:
        """Stop all morphs whose ID ends with _name. Returns stopped IDs."""
        stopped = []
        for morph_id in list(self._threads.keys()):
            if morph_id.endswith(f"_{name}"):
                self._threads[morph_id].set()
                del self._threads[morph_id]
                stopped.append(morph_id)
        return stopped

    def stop_all(self) -> int:
        """Stop all running morphs. Returns count of stopped morphs."""
        count = len(self._threads)
        for stop_event in self._threads.values():
            stop_event.set()
        self._threads.clear()
        return count

    def _run(
        self,
        morph_id: str,
        channel: int,
        start_values: dict[str, int],
        target: dict[str, int],
        duration_seconds: float,
        steps: int,
        stop_event: threading.Event,
        ping_pong: bool,
        cc_maps: list[dict],
        nrpn_maps: list[dict],
    ):
        """Background thread that interpolates params over time."""
        interval = duration_seconds / steps

        while True:
            # Forward: start -> target
            for i in range(1, steps + 1):
                if stop_event.is_set():
                    if self._threads.get(morph_id) is stop_event:
                        self._threads.pop(morph_id, None)
                    return
                t = i / steps
                _send_params_at_t(
                    self._midi,
                    channel,
                    start_values,
                    target,
                    t,
                    cc_maps,
                    nrpn_maps,
                )
                time.sleep(interval)

            if not ping_pong:
                break

            # Backward: target -> start
            for i in range(1, steps + 1):
                if stop_event.is_set():
                    if self._threads.get(morph_id) is stop_event:
                        self._threads.pop(morph_id, None)
                    return
                t = 1.0 - (i / steps)
                _send_params_at_t(
                    self._midi,
                    channel,
                    start_values,
                    target,
                    t,
                    cc_maps,
                    nrpn_maps,
                )
                time.sleep(interval)

        if self._threads.get(morph_id) is stop_event:
            self._threads.pop(morph_id, None)
