"""What the agent knows about the instrument, kept in memory.

The ledger is the single source of spoken state: playback source, loaded song,
tempo, mutes, unsaved changes, and a short history of parameter changes with
their previous values so "Rückgängig" can revert the last one. It is rebuilt on
process start and survives Live reconnects. ``observe`` is called by the bridge
after every successful tool call; ``summary_de`` turns the state into one or
two German sentences, merging the internal sequencer's live status when given.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

TRACK_NAMES_DE = {
    "synth1": "Synth 1",
    "synth2": "Synth 2",
    "drum1": "Drum 1",
    "drum2": "Drum 2",
    "drum3": "Drum 3",
    "drum4": "Drum 4",
    "midi1": "MIDI 1",
    "midi2": "MIDI 2",
}


@dataclass(frozen=True)
class Change:
    tool: str
    args: dict[str, Any]  # arguments as sent to the backend
    revert: dict[str, Any] | None  # arguments that undo it, None when the previous value is unknown
    text_de: str  # what undo says: "Filter auf Synth 1 wieder auf 60"
    done_de: str = ""  # what happened: "Synth 1: filter_frequency auf 90 (vorher 60)"; empty falls back to text_de


@dataclass
class Ledger:
    source: str = "none"  # none | internal | device
    song_name: str | None = None
    song_bpm: float | None = None
    patterns: list[str] = field(default_factory=list)
    song_order: list[str] = field(default_factory=list)
    work_pattern: str | None = None  # the pattern edits go to; set by add_pattern or a named edit
    song_json: dict[str, Any] | None = None  # the loaded song as load_song took it
    unsaved: bool = False
    slot: int | None = None  # 1-based, last slot saved to
    device_project: int | None = None  # 1-based
    device_bpm: dict[int, float] = field(default_factory=dict)  # tempo per device project, once read
    saved_bpm: dict[int, float] = field(default_factory=dict)  # tempo stored in projects written from here
    pending_bpm: float | None = None  # tempo said while stopped; the next start from here uses it once
    device_clock: float | None = None  # tempo of the device's own clock when last looked at
    project_selected_at: float | None = None  # monotonic time of the last program change while stopped
    device_dirty: bool = False  # sounds changed since the project was selected
    selected_patch: dict[int, int] = field(default_factory=dict)  # synth -> 1-based patch number
    designed_patch: dict[int, dict[str, Any]] = field(default_factory=dict)  # synth -> what design_patch sent
    running: bool = False
    bpm: float | None = None
    mutes: dict[str, bool] = field(default_factory=dict)
    saved_levels: dict[str, int] = field(default_factory=dict)
    known: dict[str, dict[str, Any]] = field(default_factory=dict)  # scope -> param -> value
    history: deque[Change] = field(default_factory=lambda: deque(maxlen=20))
    last_result: Any = None
    composer_job: str | None = None
    last_load_ok: bool = True

    # --- events from the harness ---

    def song_loaded(self, name: str, bpm: float, patterns: list[str], song_order: list[str]) -> None:
        self.song_name, self.song_bpm = name, bpm
        self.patterns, self.song_order = list(patterns), list(song_order)
        self.bpm = bpm
        self.work_pattern = None
        self.designed_patch = {}
        self.unsaved = True
        self.mutes = {}

    def saved(self, slot: int) -> None:
        self.slot = slot
        self.unsaved = False

    def observe_device(self, state: dict[str, Any]) -> None:
        """What the device told us over MIDI (get_device_state): project pad, Play/Stop, its clock.

        The internal sequencer's own Start and clock come back from the device too, so while it
        plays, only the project is taken."""
        project = state.get("project")
        if project is not None and project + 1 != self.device_project:
            self.device_project = project + 1
            self.device_dirty = False
        self.device_clock = state.get("bpm")
        if self.source == "internal":
            return
        playing = state.get("playing")
        if playing is not None:
            self.running = bool(playing)
            if playing:
                self.source = "device"
        if self.device_clock and not state.get("clock_from_here"):
            self.bpm = self.device_clock

    def first_pattern(self) -> str | None:
        return self.song_order[0] if self.song_order else (self.patterns[0] if self.patterns else None)

    def edit_pattern(self) -> str | None:
        """Where an edit without a pattern name goes: the pattern last worked on, else the first."""
        return self.work_pattern if self.work_pattern in self.patterns else self.first_pattern()

    # --- observation of successful tool calls ---

    def observe(self, tool: str, args: dict[str, Any], result: Any) -> None:
        self.last_result = result
        if tool == "start_sequencer":
            self.source, self.running = "internal", True
            if args.get("bpm"):
                self.bpm = float(args["bpm"])
        elif tool == "stop_sequencer":
            self.running = False
        elif tool == "set_bpm":
            self._param_change("tempo", {"bpm": float(args["bpm"])}, tool, {}, "Tempo")
            self.bpm = float(args["bpm"])
            self.song_bpm = self.bpm  # set_bpm is the internal sequencer's tempo: it belongs to the song
            self.unsaved = True
        elif tool == "mute_track":
            track, muted = args["track"], bool(args.get("muted", True))
            self.mutes[track] = muted
            name = TRACK_NAMES_DE.get(track, track)
            undo_text = f"{name} wieder {'an' if muted else 'stumm'}"
            self.history.append(Change(tool, dict(args), {"track": track, "muted": not muted}, undo_text))
            self.unsaved = True
        elif tool in ("set_synth_params", "set_drum_params"):
            scope_key = "synth" if tool == "set_synth_params" else "drum"
            index = args[scope_key]
            label = f"{'Synth' if scope_key == 'synth' else 'Drum'} {index}"
            self._param_change(f"{scope_key}{index}", dict(args["params"]), tool, {scope_key: index}, label)
        elif tool == "set_project_params":
            self._param_change("project", dict(args["params"]), tool, {}, "Projekt")
        elif tool == "set_macro":
            key = f"macro{args['synth']}"
            params = {str(args["macro"]): args["value"]}
            self._param_change(key, params, tool, {"synth": args["synth"]}, f"Makro auf Synth {args['synth']}")
        elif tool == "select_patch":
            self.unsaved = True
            self.device_dirty = True

    def _param_change(self, scope: str, params: dict[str, Any], tool: str, base: dict[str, Any], text: str) -> None:
        known = self.known.setdefault(scope, {})
        previous = {k: known[k] for k in params if k in known}
        revert: dict[str, Any] | None = None
        if len(previous) == len(params):
            if tool == "set_bpm":
                revert = {"bpm": previous["bpm"]}
            elif tool == "set_macro":
                (macro, value), *_ = previous.items()
                revert = {**base, "macro": int(macro), "value": value}
            else:
                revert = {**base, "params": previous}
        known.update(params)
        shown = ", ".join(f"{k} wieder auf {v}" for k, v in previous.items()) or "vorheriger Wert unbekannt"
        done = ", ".join(
            f"{k} auf {v}" + (f" (vorher {previous[k]})" if k in previous else "") for k, v in params.items()
        )
        self.history.append(Change(tool, {**base, "params": params}, revert, f"{text}: {shown}", f"{text}: {done}"))
        if scope != "tempo":
            self.unsaved = True
            self.device_dirty = True

    def undo(self) -> Change | None:
        """Pop the last change; None when there is nothing to undo."""
        while self.history:
            change = self.history.pop()
            if change.revert is not None:
                self._apply_revert(change)
                return change
            return change  # known change without a known previous value: report it
        return None

    def _apply_revert(self, change: Change) -> None:
        if change.tool == "mute_track":
            self.mutes[change.revert["track"]] = change.revert["muted"]
        elif change.tool == "set_bpm":
            self.bpm = self.song_bpm = change.revert["bpm"]
            self.known.get("tempo", {})["bpm"] = change.revert["bpm"]
        elif "params" in change.revert:
            scope = self._scope_for(change)
            self.known.setdefault(scope, {}).update(change.revert["params"])
        elif change.tool == "set_macro":
            self.known.setdefault(f"macro{change.revert['synth']}", {})[str(change.revert["macro"])] = change.revert[
                "value"
            ]

    @staticmethod
    def _scope_for(change: Change) -> str:
        if change.tool == "set_synth_params":
            return f"synth{change.args['synth']}"
        if change.tool == "set_drum_params":
            return f"drum{change.args['drum']}"
        return "project"

    # --- spoken state ---

    def summary_de(self, status: dict[str, Any] | None = None) -> str:
        if self.song_name is None and self.source == "none" and self.device_project is None:
            return "Es ist noch kein Song geladen und nichts spielt."
        parts: list[str] = []
        if self.source != "internal" and self.device_project is not None:
            if self.running:
                parts.append(f"Das Gerät spielt Projekt {self.device_project}")
            else:
                parts.append(f"Projekt {self.device_project} ist gewählt, gestoppt")
            if self.bpm:
                parts.append(f"{int(round(self.bpm))} BPM")
        else:
            running = bool(status["running"]) if status else self.running
            pattern = (status or {}).get("current_pattern")
            bpm = (status or {}).get("bpm", self.bpm)
            name = self.song_name or "der Song"
            if running:
                where = f", Pattern {pattern}" if pattern else ""
                parts.append(f"{name} läuft{where}")
            else:
                parts.append(f"{name} ist geladen, gestoppt")
            if bpm:
                parts.append(f"{int(round(bpm))} BPM")
        mutes = (status or {}).get("track_mutes", self.mutes)
        muted = [TRACK_NAMES_DE.get(t, t) for t, m in mutes.items() if m]
        if muted:
            parts.append("stumm: " + ", ".join(muted))
        if self.unsaved:
            parts.append("nicht gespeichert")
        elif self.slot:
            parts.append(f"gespeichert in Slot {self.slot}")
        if self.history:
            last = self.history[-1]
            parts.append(f"letzte Änderung: {last.done_de or last.text_de}")
        return ". ".join(parts) + "."
