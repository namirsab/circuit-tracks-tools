"""MCP server for controlling Novation Circuit Tracks via MIDI."""

import asyncio
import os
import threading
import time

from mcp.server.fastmcp import FastMCP

from circuit_mcp.constants import (
    AUDIO_CC,
    DRUMS_CHANNEL,
    DRUM_CC,
    DRUM_NOTES,
    PROJECT_CHANNEL,
    PROJECT_CC,
    PROJECT_NRPN,
    SYNTH1_CHANNEL,
    SYNTH2_CHANNEL,
    SYNTH_CC,
    SYNTH_NRPN,
    load_drum_sample_names,
    save_drum_sample_names,
)
from circuit_mcp.macros import DEFAULT_MACROS, MacroTarget, apply_macro
from circuit_mcp.midi import MidiConnection
from circuit_mcp.patch import _PARAM_OFFSETS
from circuit_mcp.patch import (
    parse_patch_data,
    parse_patch_file,
    read_and_modify_patch,
    request_current_patch,
    send_current_patch,
)
from circuit_mcp.ncs_transfer import send_ncs_project, send_patch_to_slot
from circuit_mcp.sequencer import (
    VALID_TRACK_NAMES,
    Pattern,
    SequencerEngine,
    Step,
    Track,
    TrackType,
)


_midi = MidiConnection()
_engine = SequencerEngine(_midi)
_morph_threads: dict[str, threading.Event] = {}  # morph_id -> stop_event
_morph_counter = 0

mcp = FastMCP(
    "Circuit Tracks",
    instructions="Control a Novation Circuit Tracks synthesizer via MIDI",
)


# --- Connection Tools ---


@mcp.tool()
def list_midi_ports() -> dict:
    """List available MIDI input and output ports.

    Use this to find the Circuit Tracks port name before connecting.
    The Circuit Tracks typically appears as 'Circuit Tracks' or similar.
    """
    return {
        "output_ports": MidiConnection.list_output_ports(),
        "input_ports": MidiConnection.list_input_ports(),
    }


@mcp.tool()
def connect(port_name: str) -> dict:
    """Connect to the Circuit Tracks MIDI output port.

    Args:
        port_name: The exact MIDI port name from list_midi_ports output.
    """
    midi = _midi
    midi.connect(port_name)

    result: dict = {"result": f"Connected to {port_name}"}

    # Auto-scan drum sample names from the device
    if midi.has_input:
        try:
            from circuit_mcp.ncs_transfer import list_directory
            entries = list_directory(midi, file_type=5)
            if entries:
                samples = {e["slot"]: e["filename"] for e in entries}
                save_drum_sample_names(samples)
                result["drum_samples_scanned"] = len(entries)
        except Exception:
            pass  # Non-critical — fall back to config/factory defaults

    return result


@mcp.tool()
def disconnect() -> str:
    """Disconnect from the Circuit Tracks."""
    midi = _midi
    midi.disconnect()
    return "Disconnected"


@mcp.tool()
def connection_status() -> dict:
    """Check the current MIDI connection status."""
    midi = _midi
    return {
        "connected": midi.is_connected,
        "port_name": midi.port_name,
        "has_input": midi.has_input,
        "input_port_name": midi._input_port_name,
    }


# --- Note Tools ---


@mcp.tool()
async def play_notes(
    channel: int,
    notes: list[int],
    velocity: int = 100,
    duration_ms: int = 500,
) -> str:
    """Play one or more MIDI notes on the Circuit Tracks.

    For a single note, pass a one-element list: [60].
    For a chord, pass multiple notes: [60, 64, 67].

    Channels: 0=Synth 1, 1=Synth 2, 9=Drums (notes 60,62,64,65 for drums 1-4).

    Args:
        channel: MIDI channel (0-indexed). 0=Synth1, 1=Synth2, 9=Drums.
        notes: List of MIDI note numbers (0-127). Middle C = 60.
        velocity: Note velocity (0-127).
        duration_ms: How long to hold the notes in milliseconds.
    """
    midi = _midi
    for note in notes:
        midi.note_on(channel, note, velocity)
    await asyncio.sleep(duration_ms / 1000.0)
    for note in notes:
        midi.note_off(channel, note)
    if len(notes) == 1:
        return f"Played note {notes[0]} on channel {channel} (vel={velocity}, dur={duration_ms}ms)"
    return f"Played chord {notes} on channel {channel}"


@mcp.tool()
def play_drum(
    drum: int,
    velocity: int = 100,
) -> str:
    """Trigger a drum hit on the Circuit Tracks.

    Args:
        drum: Drum number (1-4).
        velocity: Hit velocity (0-127).
    """
    if drum not in DRUM_NOTES:
        return f"Invalid drum number {drum}. Must be 1-4."
    midi = _midi
    note = DRUM_NOTES[drum]
    midi.note_on(DRUMS_CHANNEL, note, velocity)
    return f"Triggered drum {drum} (note {note}, vel={velocity})"


# --- Pattern Sequencer Tools ---


def _parse_track_data(track_name: str, track_data: dict) -> tuple[dict[int, Step], int]:
    """Parse track data from tool input into Step objects."""
    steps_raw = track_data.get("steps", {})
    steps: dict[int, Step] = {}
    for idx_str, step_data in steps_raw.items():
        steps[int(idx_str)] = Step.from_dict(step_data)
    num_steps = track_data.get("num_steps", 16)
    return steps, num_steps


@mcp.tool()
def set_pattern(
    name: str,
    tracks: dict[str, dict],
    length: int = 16,
) -> str:
    """Define a named pattern with tracks and steps. This is the primary tool for creating music.

    The sequencer mirrors the Circuit Tracks: 6 tracks (synth1, synth2, drum1-4),
    each with up to 32 steps. Steps are 16th notes at the given BPM.

    Only specify steps that have notes — absent step indices are rests.
    Drum tracks ignore the note field (each drum has a fixed trigger note).

    Args:
        name: Pattern name (e.g., "intro", "verse", "drop", "breakdown").
        tracks: Dict of track data keyed by track name. Track names:
            "synth1", "synth2", "drum1", "drum2", "drum3", "drum4".
            Each track value is a dict with:
              "steps": dict of step_index (as string) -> step data.
              Step data fields (all optional, have defaults):
                - note (int): MIDI note, default 60. Ignored for drum tracks.
                - notes (list[int]): Multiple notes for chords.
                - velocity (int): 0-127, default 100.
                - gate (float): 0.0-1.0, fraction of step duration, default 0.5.
                - probability (float): 0.0-1.0, chance of playing, default 1.0.
                - enabled (bool): default True.
        length: Pattern length in steps (default 16, any multiple of 16).
    """
    pattern = Pattern(length=length)
    for track_name, track_data in tracks.items():
        if track_name not in VALID_TRACK_NAMES:
            return f"Invalid track '{track_name}'. Must be one of: {', '.join(sorted(VALID_TRACK_NAMES))}"
        steps, num_steps = _parse_track_data(track_name, track_data)
        track = pattern.tracks[track_name]
        track.steps = steps
        track.num_steps = num_steps

    _engine.set_pattern(name, pattern)
    total_steps = sum(len(t.steps) for t in pattern.tracks.values())
    return f"Pattern '{name}' set: {total_steps} steps across {len(tracks)} tracks, length={length}"


@mcp.tool()
def set_track(
    pattern_name: str,
    track: str,
    steps: dict[str, dict],
    clear_existing: bool = True,
) -> str:
    """Update a single track within a pattern. Use this to add/modify parts while the sequencer runs.

    Args:
        pattern_name: Name of the pattern to modify.
        track: Track name: "synth1", "synth2", "drum1", "drum2", "drum3", "drum4".
        steps: Dict of step_index (as string) -> step data (same format as set_pattern).
        clear_existing: If True (default), replace all steps. If False, merge with existing.
    """
    if track not in VALID_TRACK_NAMES:
        return f"Invalid track '{track}'. Must be one of: {', '.join(sorted(VALID_TRACK_NAMES))}"

    parsed_steps: dict[int, Step] = {}
    for idx_str, step_data in steps.items():
        parsed_steps[int(idx_str)] = Step.from_dict(step_data)

    _engine.set_track(pattern_name, track, parsed_steps, clear=clear_existing)
    mode = "replaced" if clear_existing else "merged"
    return f"Track '{track}' in pattern '{pattern_name}': {mode} {len(parsed_steps)} steps"


@mcp.tool()
def clear_pattern(name: str) -> str:
    """Clear a pattern, removing all steps from all tracks.

    Args:
        name: Name of the pattern to clear.
    """
    _engine.clear_pattern(name)
    return f"Pattern '{name}' cleared"


@mcp.tool()
def get_pattern(name: str) -> dict:
    """Get the current data for a pattern. Useful for inspecting what's programmed.

    Args:
        name: Name of the pattern.
    """
    pattern = _engine.get_pattern(name)
    if pattern is None:
        return {"error": f"Pattern '{name}' not found."}
    return pattern.to_dict()


@mcp.tool()
def list_patterns() -> dict:
    """List all defined pattern names."""
    return {"patterns": _engine.list_patterns()}


@mcp.tool()
def start_sequencer(
    pattern: str,
    bpm: float = 120.0,
    send_clock: bool = True,
) -> str:
    """Start the pattern sequencer. Loops the specified pattern until stopped or a queue is set.

    Sends MIDI clock to keep the Circuit Tracks display in sync.
    Use set_pattern first to define the pattern, then start_sequencer to play it.

    Args:
        pattern: Name of the pattern to play.
        bpm: Tempo in beats per minute.
        send_clock: Send MIDI clock (24ppqn) to sync the Circuit Tracks display.
    """
    _engine.start(pattern_name=pattern, bpm=bpm, send_clock=send_clock)
    return f"Sequencer started: pattern '{pattern}' at {bpm} BPM"


@mcp.tool()
def stop_sequencer() -> str:
    """Stop the sequencer. Sends All Notes Off to prevent stuck notes."""
    _engine.stop()
    return "Sequencer stopped"


@mcp.tool()
def set_bpm(bpm: float) -> str:
    """Change the sequencer tempo while it's running.

    Args:
        bpm: New tempo in beats per minute.
    """
    _engine.set_bpm(bpm)
    return f"BPM set to {bpm}"


@mcp.tool()
def queue_patterns(patterns: list[str]) -> str:
    """Queue one or more patterns to play in sequence after the current one finishes.

    Each pattern plays once, then the next in the queue starts. When the queue
    is empty, the last pattern loops. Use this to compose song structures.

    Example: queue_patterns(["verse", "verse", "chorus", "verse", "chorus", "outro"])

    Args:
        patterns: List of pattern names to play in order.
    """
    _engine.queue_patterns(patterns)
    return f"Queued {len(patterns)} patterns: {', '.join(patterns)}"


@mcp.tool()
def set_song(patterns: list[str]) -> str:
    """Replace the entire pattern queue (the song). Clears any previously queued patterns.

    Use this to define a full song structure. The current pattern finishes,
    then the song plays through. When the queue is empty, the last pattern loops.

    Args:
        patterns: List of pattern names in playback order.
    """
    _engine.set_queue(patterns)
    return f"Song set: {len(patterns)} patterns — {', '.join(patterns)}"


@mcp.tool()
def clear_queue() -> str:
    """Clear the pattern queue. The current pattern will loop indefinitely."""
    _engine.clear_queue()
    return "Pattern queue cleared (current pattern will loop)"


@mcp.tool()
def mute_track(track: str, muted: bool = True) -> str:
    """Mute or unmute a track. Takes effect immediately.

    Args:
        track: Track name: "synth1", "synth2", "drum1", "drum2", "drum3", "drum4".
        muted: True to mute, False to unmute.
    """
    if track not in VALID_TRACK_NAMES:
        return f"Invalid track '{track}'. Must be one of: {', '.join(sorted(VALID_TRACK_NAMES))}"
    _engine.set_mute(track, muted)
    state = "muted" if muted else "unmuted"
    return f"Track '{track}' {state}"


@mcp.tool()
def get_sequencer_status() -> dict:
    """Get the current sequencer state: running, pattern, step, BPM, mutes, queue."""
    return _engine.get_status()


# --- CC / NRPN Tools (debug only, set CIRCUIT_DEBUG=1 to enable) ---


if os.environ.get("CIRCUIT_DEBUG"):

    @mcp.tool()
    def send_cc(channel: int, control: int, value: int) -> str:
        """Send a raw MIDI Control Change message.

        Args:
            channel: MIDI channel (0-indexed).
            control: CC number (0-127).
            value: CC value (0-127).
        """
        midi = _midi
        midi.control_change(channel, control, value)
        return f"Sent CC {control}={value} on channel {channel}"

    @mcp.tool()
    def send_nrpn(channel: int, nrpn_msb: int, nrpn_lsb: int, value: int) -> str:
        """Send an NRPN (Non-Registered Parameter Number) message.

        Used for synth parameters not accessible via standard CC.

        Args:
            channel: MIDI channel (0-indexed).
            nrpn_msb: NRPN parameter MSB.
            nrpn_lsb: NRPN parameter LSB.
            value: Parameter value (0-127).
        """
        midi = _midi
        midi.nrpn(channel, nrpn_msb, nrpn_lsb, value)
        return f"Sent NRPN ({nrpn_msb}:{nrpn_lsb})={value} on channel {channel}"


@mcp.tool()
def set_synth_params(synth: int, params: dict[str, int]) -> str:
    """Set one or more synth parameters on Synth 1 or 2.

    Pass a dict of parameter names to values. For a single param, pass a
    one-entry dict: {"filter_frequency": 80}.

    Use get_parameter_reference for the full list of exact parameter names.

    Args:
        synth: Synth number (1 or 2).
        params: Dict of param_name -> value. e.g. {"filter_frequency": 80, "osc1_wave": 2}
    """
    if synth not in (1, 2):
        return f"Invalid synth number {synth}. Must be 1 or 2."

    channel = SYNTH1_CHANNEL if synth == 1 else SYNTH2_CHANNEL
    midi = _midi
    set_params = []
    errors = []

    for param_name, value in params.items():
        if param_name in SYNTH_CC:
            cc = SYNTH_CC[param_name]
            midi.control_change(channel, cc, value)
            set_params.append(f"{param_name}={value}")
        elif param_name in SYNTH_NRPN:
            msb, lsb = SYNTH_NRPN[param_name]
            midi.nrpn(channel, msb, lsb, value)
            set_params.append(f"{param_name}={value}")
        else:
            errors.append(param_name)

    result = f"Synth {synth}: set {len(set_params)} params — {', '.join(set_params)}"
    if errors:
        result += f". Unknown params: {', '.join(errors)}"
    return result


@mcp.tool()
def set_drum_params(drum: int, params: dict[str, int]) -> str:
    """Set one or more drum parameters on drums 1-4.

    Available params: level, pitch, decay, distortion, eq, pan.
    For a single param, pass a one-entry dict: {"pitch": 80}.
    Note: patch_select is not available via CC (firmware bug). Use the
    song format with NCS export to set drum samples reliably.

    Args:
        drum: Drum number (1-4).
        params: Dict of param_name -> value. e.g. {"pitch": 80, "decay": 60, "level": 100}
    """
    if drum not in DRUM_CC:
        return f"Invalid drum number {drum}. Must be 1-4."

    drum_params = DRUM_CC[drum]
    midi = _midi
    set_params = []
    errors = []

    for param_name, value in params.items():
        if param_name == "patch_select":
            errors.append("patch_select (use NCS export instead)")
            continue
        if param_name not in drum_params:
            errors.append(param_name)
            continue
        cc = drum_params[param_name]
        midi.control_change(DRUMS_CHANNEL, cc, value)
        set_params.append(f"{param_name}={value}")

    result = f"Drum {drum}: set {len(set_params)} params — {', '.join(set_params)}"
    if errors:
        result += f". Unknown params: {', '.join(errors)}"
    return result




@mcp.tool()
def list_drum_samples(page: int | None = None) -> dict:
    """List available drum samples with their index numbers and names.

    Returns sample names from user config (~/.config/circuit-mcp/drum_samples.json)
    if available, otherwise falls back to factory defaults. Use set_drum_sample_names
    to customize the sample map, or scan_drum_samples to read names from the device.

    To assign samples to drum tracks, use the song format with the "sample" field
    in sounds config (e.g. "drum1": {"sample": 2}) and export via NCS.

    The Circuit Tracks has 64 samples per drum track, organized in 4 pages of 16.
    Each page follows a kit structure:
      Slots 1-2: Kicks, 3-4: Snares, 5-6: Closed Hi-Hats,
      7-8: Open Hi-Hats, 9-12: Percussion, 13-16: Melodic sounds.

    Args:
        page: Optional page number (1-4) to show only that page. None shows all.
    """
    all_samples = load_drum_sample_names()

    if page is not None:
        if not 1 <= page <= 4:
            return {"error": f"Invalid page {page}. Must be 1-4."}
        start = (page - 1) * 16
        end = start + 16
        samples = {i: all_samples.get(i, f"Sample {i}") for i in range(start, end)}
        return {"page": page, "samples": samples}

    return {
        "total_samples": len(all_samples),
        "pages": 4,
        "samples_per_page": 16,
        "samples": all_samples,
    }


@mcp.tool()
def set_drum_sample_names(samples: dict[str, str]) -> dict:
    """Set custom names for drum samples, persisted to config file.

    Use this when the user tells you what samples they have loaded on their
    Circuit Tracks (e.g. custom samples loaded via Novation Components).
    Names are saved to ~/.config/circuit-mcp/drum_samples.json and will be
    used by list_drum_samples and select_drum_sample.

    Entries are merged with existing config — only specified indices are updated.

    Args:
        samples: Dict mapping sample index (as string) to name.
                 Example: {"0": "808 Kick", "1": "909 Kick", "2": "Vinyl Snare"}
    """
    parsed = {}
    for k, v in samples.items():
        idx = int(k)
        if not 0 <= idx <= 63:
            return {"error": f"Invalid sample index {k}. Must be 0-63."}
        parsed[idx] = str(v)

    path = save_drum_sample_names(parsed)
    return {
        "status": "ok",
        "samples_updated": len(parsed),
        "config_path": str(path),
    }


@mcp.tool()
def select_patch(synth: int, patch_number: int) -> str:
    """Select a synth patch by number.

    Args:
        synth: Synth number (1 or 2).
        patch_number: Patch number (0-63).
    """
    if synth not in (1, 2):
        return f"Invalid synth number {synth}. Must be 1 or 2."
    if not 0 <= patch_number <= 63:
        return f"Invalid patch number {patch_number}. Must be 0-63."

    channel = SYNTH1_CHANNEL if synth == 1 else SYNTH2_CHANNEL
    midi = _midi
    midi.program_change(channel, patch_number)
    return f"Selected patch {patch_number} on synth {synth}"


@mcp.tool()
def select_project(project_number: int, queued: bool = False) -> str:
    """Select a project on the Circuit Tracks.

    Args:
        project_number: Project number (0-63).
        queued: If True, queue the change; if False, change instantly.
    """
    global _current_project_slot
    if not 0 <= project_number <= 63:
        return f"Invalid project number {project_number}. Must be 0-63."

    midi = _midi
    program = project_number + (64 if queued else 0)
    midi.program_change(PROJECT_CHANNEL, program)
    _current_project_slot = project_number
    mode = "queued" if queued else "instant"
    return f"Selected project {project_number} ({mode})"


@mcp.tool()
def set_project_params(params: dict[str, int]) -> str:
    """Set one or more project-level parameters (reverb, delay, mixer, sidechain, master filter).

    For a single param, pass a one-entry dict: {"reverb_type": 3}.

    Available CC params: reverb_synth1_send, reverb_synth2_send, reverb_drum1-4_send,
    delay_synth1_send, delay_synth2_send, delay_drum1-4_send,
    synth1_level, synth2_level, synth1_pan, synth2_pan,
    master_filter_frequency, master_filter_resonance.

    Available NRPN params: reverb_type, reverb_decay, reverb_damping, fx_bypass,
    delay_time, delay_time_sync, delay_feedback, delay_width, delay_lr_ratio,
    delay_slew_rate, sidechain_synth1_source/attack/hold/decay/depth,
    sidechain_synth2_source/attack/hold/decay/depth.

    Args:
        params: Dict of param_name -> value. e.g. {"reverb_type": 3, "delay_feedback": 80}
    """
    midi = _midi
    set_params = []
    errors = []

    for param_name, value in params.items():
        if param_name in PROJECT_CC:
            cc = PROJECT_CC[param_name]
            midi.control_change(PROJECT_CHANNEL, cc, value)
            set_params.append(f"{param_name}={value}")
        elif param_name in PROJECT_NRPN:
            msb, lsb = PROJECT_NRPN[param_name]
            midi.nrpn(PROJECT_CHANNEL, msb, lsb, value)
            set_params.append(f"{param_name}={value}")
        else:
            errors.append(param_name)

    result = f"Project: set {len(set_params)} params — {', '.join(set_params)}"
    if errors:
        result += f". Unknown params: {', '.join(errors)}"
    return result



# --- Macros (Live Performance) ---


# Mutable macro state — can be customized at runtime
_macros = dict(DEFAULT_MACROS)


@mcp.tool()
def set_macro(synth: int, macro: int, value: int) -> str:
    """Set a macro knob value, which adjusts the mapped synth parameters.

    Macros provide a consistent live performance interface. Each macro
    controls one or more synth parameters with configurable ranges.

    Args:
        synth: Synth number (1 or 2).
        macro: Macro number (1-8).
        value: Knob position (0-127).
    """
    if synth not in (1, 2):
        return f"Invalid synth number {synth}. Must be 1 or 2."
    if macro not in _macros:
        return f"Invalid macro {macro}. Must be 1-8."

    params = apply_macro(macro, value, _macros)
    if not params:
        return f"Macro {macro} has no targets configured."

    channel = SYNTH1_CHANNEL if synth == 1 else SYNTH2_CHANNEL
    midi = _midi
    sent = []

    for param_name, param_value in params.items():
        if param_name in SYNTH_CC:
            midi.control_change(channel, SYNTH_CC[param_name], param_value)
            sent.append(f"{param_name}={param_value}")
        elif param_name in SYNTH_NRPN:
            msb, lsb = SYNTH_NRPN[param_name]
            midi.nrpn(channel, msb, lsb, param_value)
            sent.append(f"{param_name}={param_value}")

    macro_name = _macros[macro]["name"]
    return f"Macro {macro} ({macro_name}) = {value} on synth {synth}: {', '.join(sent)}"


@mcp.tool()
def get_macros() -> dict:
    """Get the current macro layout showing what each macro controls."""
    result = {}
    for num, macro in _macros.items():
        result[str(num)] = {
            "name": macro["name"],
            "targets": [
                {"param": t.param, "min": t.min_val, "max": t.max_val}
                for t in macro["targets"]
            ],
        }
    return result


@mcp.tool()
def configure_macro(
    macro: int,
    name: str,
    targets: list[dict],
) -> str:
    """Configure what a macro knob controls. Changes take effect immediately.

    Args:
        macro: Macro number (1-8).
        name: Display name for this macro (e.g., "Filter Sweep").
        targets: List of parameter targets, each a dict with:
          - param (str): Parameter name (e.g., "filter_frequency").
          - min (int): Value when knob is at 0. Default 0.
          - max (int): Value when knob is at 127. Default 127.
          Use min > max to invert the control direction.
    """
    if not 1 <= macro <= 8:
        return f"Invalid macro {macro}. Must be 1-8."

    parsed_targets = []
    for t in targets:
        param = t.get("param", "")
        if param not in SYNTH_CC and param not in SYNTH_NRPN:
            return f"Unknown param '{param}'. Must be a valid synth parameter name."
        parsed_targets.append(MacroTarget(
            param=param,
            min_val=t.get("min", 0),
            max_val=t.get("max", 127),
        ))

    _macros[macro] = {"name": name, "targets": parsed_targets}
    target_desc = ", ".join(f"{t.param} ({t.min_val}→{t.max_val})" for t in parsed_targets)
    return f"Macro {macro} configured: '{name}' → {target_desc}"


# --- Transport ---


@mcp.tool()
def transport(action: str) -> str:
    """Control the Circuit Tracks transport (sequencer start/stop).

    Args:
        action: One of "start", "stop", or "continue".
    """
    midi = _midi
    midi.send_realtime(action)
    return f"Sent transport: {action}"


# --- Info / Reference ---




@mcp.tool()
def get_synth_patch(synth: int) -> dict:
    """Read the current synth patch from the Circuit Tracks via SysEx.

    Requests a patch dump from the device and returns the patch name and
    all readable parameter values. Requires a bidirectional MIDI connection
    (both input and output ports).

    Args:
        synth: Synth number (1 or 2).
    """
    if synth not in (1, 2):
        return {"error": f"Invalid synth number {synth}. Must be 1 or 2."}

    midi = _midi
    if not midi.has_input:
        return {
            "error": "No MIDI input port available. "
            "The input port is needed to receive the patch dump from the device. "
            "Reconnect and ensure the Circuit Tracks input port is accessible."
        }

    sysex_data = request_current_patch(midi, synth)
    if sysex_data is None:
        return {
            "error": "No response from Circuit Tracks. "
            "Make sure it is connected and not in a special mode (bootloader, etc)."
        }

    result = parse_patch_data(sysex_data)
    result["synth"] = synth
    result["raw_sysex_length"] = len(sysex_data)
    return result


@mcp.tool()
def edit_synth_patch(synth: int, params: dict[str, int | str]) -> dict:
    """Edit the current synth patch on the Circuit Tracks via SysEx read-modify-write.

    This gives access to ALL patch parameters, including ones not reachable via CC/NRPN:
    mod matrix, LFO details, EQ, chorus settings, macro routing, patch name, etc.

    Reads the current patch, modifies the specified parameters, and sends it back.
    Requires a bidirectional MIDI connection.

    Use get_parameter_reference for the full list of exact parameter names.
    For unmapped bytes, use raw_<offset> (e.g., "raw_95": 64) to set byte
    at that offset directly in the parameter region.

    Args:
        synth: Synth number (1 or 2).
        params: Dict of param_name -> value. e.g. {"name": "MyPatch", "lfo1_rate": 80}
    """
    if synth not in (1, 2):
        return {"error": f"Invalid synth number {synth}. Must be 1 or 2."}

    midi = _midi
    if not midi.has_input:
        return {
            "error": "No MIDI input port. Needed for read-modify-write. "
            "Reconnect and ensure input port is accessible."
        }

    return read_and_modify_patch(midi, synth, params)


@mcp.tool()
def load_patch_file(synth: int, file_path: str) -> dict:
    """Load a .syx patch file and send it to a synth on the Circuit Tracks.

    Args:
        synth: Synth number (1 or 2).
        file_path: Path to the .syx patch file.
    """
    if synth not in (1, 2):
        return {"error": f"Invalid synth number {synth}. Must be 1 or 2."}

    parsed = parse_patch_file(file_path)
    if "error" in parsed:
        return parsed

    # Read the raw bytes from the file
    with open(file_path, "rb") as f:
        raw = f.read()

    patch_bytes = list(raw[9:-1])  # Skip F0 + 8-byte header, strip F7
    if len(patch_bytes) != 340:
        return {"error": f"Invalid patch size: {len(patch_bytes)} bytes, expected 340"}

    midi = _midi
    send_current_patch(midi, synth, patch_bytes)

    return {
        "loaded": parsed.get("name", "Unknown"),
        "synth": synth,
        "file": file_path,
    }


@mcp.tool()
def save_synth_patch(synth: int, slot: int) -> dict:
    """Save the current synth patch to a numbered slot in flash memory.

    Uses the Novation Components file management protocol to write the patch
    directly to flash storage.

    Args:
        synth: Synth number (1 or 2).
        slot: Patch slot number (0-63).
    """
    if synth not in (1, 2):
        return {"error": f"Invalid synth number {synth}. Must be 1 or 2."}
    if not 0 <= slot <= 63:
        return {"error": f"Invalid slot {slot}. Must be 0-63."}

    midi = _midi
    if not midi.has_input:
        return {
            "error": "No MIDI input port available. "
            "The input port is needed to read the current patch before saving."
        }

    # Read current patch from device
    sysex_data = request_current_patch(midi, synth)
    if sysex_data is None:
        return {"error": "No response from Circuit Tracks. Is it connected?"}

    patch_start = 8
    patch_bytes = bytes(sysex_data[patch_start:patch_start + 340])
    if len(patch_bytes) < 340:
        return {"error": f"Patch data too short: {len(patch_bytes)} bytes"}

    # Wait for device to finish processing the dump response
    import time
    time.sleep(0.1)

    # Save to flash slot via Components protocol
    return send_patch_to_slot(midi, patch_bytes, synth, slot)


@mcp.tool()
def create_synth_patch(
    synth: int,
    name: str,
    params: dict[str, int] | None = None,
    mod_matrix: list[dict] | None = None,
    macros: dict[str, dict] | None = None,
    preset: str | None = None,
) -> dict:
    """Create a synth patch from scratch and send it to the Circuit Tracks.

    Unlike edit_synth_patch (which reads then modifies the current patch), this
    builds a complete patch from an init template. Use for full sound design.

    Preset names: "pad", "bass", "lead", "pluck" (or None for init patch).

    IMPORTANT: Use get_parameter_reference to get the exact parameter names.
    Unrecognised param names are silently ignored.

    Mod matrix entries: {"source": str/int, "dest": str/int, "depth": int, "source2": str/int}
    Depth is SIGNED: -64 to +63 (0 = no modulation, positive = positive mod).
      e.g. depth=5 means subtle positive modulation, depth=-20 means moderate negative.
    Sources: direct, velocity, keyboard, LFO 1+, LFO 1+/-, LFO 2+, LFO 2+/-,
             env amp, env filter, env 3
    Destinations: osc 1 & 2 pitch, osc 1/2 pitch, osc 1/2 v-sync,
                  osc 1/2 pulse width / index, osc 1/2 level, noise level,
                  ring modulation 1*2 level, filter drive amount,
                  filter frequency, filter resonance, LFO 1/2 rate,
                  amp/filter envelope decay

    Macro entries: {macro_num: {"targets": [{"dest": str/int, "start": int, "end": int, "depth": int}]}}
    Macro dest uses parameter names (filter_frequency, env1_attack, etc.) or indices 0-70.

    Standard macro layout (follow this order, be creative but use the names as an orientation)
        1. Oscillator 
        2. Oscillator Mod
        3. Amp Envelope
        4. Filter Envelope
        5. Filter Frequency
        6. Resonance
        7. Modulation
        8. FX

    Args:
        synth: Synth number (1 or 2).
        name: Patch name (up to 16 chars).
        params: Dict of parameter_name -> value.
        mod_matrix: List of mod routing dicts.
        macros: Dict of macro_num (str) -> config dict with "targets" list.
        preset: Optional preset name as starting point.
    """
    if synth not in (1, 2):
        return {"error": f"Invalid synth number {synth}. Must be 1 or 2."}

    from circuit_mcp.patch_builder import (
        PatchBuilder, preset_pad, preset_bass, preset_lead, preset_pluck,
    )

    # Start from preset or init
    presets = {"pad": preset_pad, "bass": preset_bass, "lead": preset_lead, "pluck": preset_pluck}
    if preset and preset.lower() in presets:
        builder = presets[preset.lower()](name)
    else:
        builder = PatchBuilder(name)

    # Apply params
    if params:
        for param_name, value in params.items():
            if param_name in _PARAM_OFFSETS:
                builder._bytes[_PARAM_OFFSETS[param_name]] = max(0, min(127, int(value)))

    # Apply mod matrix
    if mod_matrix:
        builder.clear_mods()
        for entry in mod_matrix:
            # Depth is accepted as signed (-64 to +63) and converted to
            # raw 0-127 (where 64 = no modulation).
            raw_depth = entry.get("depth", 16)
            if -64 <= raw_depth <= 63:
                raw_depth = raw_depth + 64
            # else assume caller already passed raw 0-127
            builder.add_mod(
                source=entry.get("source", 0),
                destination=entry.get("dest", 0),
                depth=raw_depth,
                source2=entry.get("source2", 0),
            )

    # Apply macros
    if macros:
        for macro_num_str, config in macros.items():
            macro_num = int(macro_num_str)
            targets = config.get("targets", [])
            position = config.get("position", 0)
            builder.set_macro(macro_num, targets, position=position)

    patch_bytes = list(builder.build())
    midi = _midi
    send_current_patch(midi, synth, patch_bytes)

    return {
        "synth": synth,
        "name": name,
        "preset": preset,
        "params_set": len(params) if params else 0,
        "mod_slots": len(mod_matrix) if mod_matrix else 0,
        "macros_set": len(macros) if macros else 0,
    }


@mcp.tool()
def get_parameter_reference() -> dict:
    """Get a complete reference of all parameter names for synths, drums, and project.

    Returns parameter names for set_synth_params, set_drum_params, set_project_params,
    and create_synth_patch. Includes defaults, ranges, lookup tables for waveforms,
    filter types, mod matrix sources/destinations, and macro destinations.
    """
    from circuit_mcp.constants import (
        OSC_WAVEFORMS, FILTER_TYPES, DISTORTION_TYPES, LFO_WAVEFORMS,
        MOD_MATRIX_SOURCES, MOD_MATRIX_DESTINATIONS, MACRO_DESTINATIONS,
    )

    return {
        "synth_cc_params": sorted(SYNTH_CC.keys()),
        "synth_nrpn_params": sorted(SYNTH_NRPN.keys()),
        "drum_params": ["level", "pitch", "decay", "distortion", "eq", "pan"],
        "drum_numbers": [1, 2, 3, 4],
        "project_cc_params": sorted(PROJECT_CC.keys()),
        "project_nrpn_params": sorted(PROJECT_NRPN.keys()),
        "channels": {
            "synth1": 0,
            "synth2": 1,
            "drums": 9,
            "project": 15,
        },
        "patch_parameters": {
            "voice": {
                "polyphony_mode": {"default": 2, "range": "0-2", "notes": "0=Mono, 1=Mono AG, 2=Poly"},
                "portamento_rate": {"default": 0, "range": "0-127"},
                "pre_glide": {"default": 64, "range": "52-76", "notes": "center=64"},
                "keyboard_octave": {"default": 64, "range": "58-69", "notes": "center=64"},
            },
            "osc1": {
                "osc1_wave": {"default": 2, "range": "0-29", "notes": "See waveforms table"},
                "osc1_wave_interpolate": {"default": 127, "range": "0-127"},
                "osc1_pulse_width_index": {"default": 64, "range": "0-127"},
                "osc1_virtual_sync_depth": {"default": 0, "range": "0-127"},
                "osc1_density": {"default": 0, "range": "0-127"},
                "osc1_density_detune": {"default": 0, "range": "0-127"},
                "osc1_semitones": {"default": 64, "range": "0-127", "notes": "center=64"},
                "osc1_cents": {"default": 64, "range": "0-127", "notes": "center=64"},
                "osc1_pitchbend": {"default": 76, "range": "52-76"},
            },
            "osc2": "Same layout as osc1 (prefix osc2_)",
            "mixer": {
                "osc1_level": {"default": 127, "range": "0-127"},
                "osc2_level": {"default": 0, "range": "0-127"},
                "ring_mod_level": {"default": 0, "range": "0-127"},
                "noise_level": {"default": 0, "range": "0-127"},
                "pre_fx_level": {"default": 64, "range": "52-82"},
                "post_fx_level": {"default": 64, "range": "52-82"},
            },
            "filter": {
                "routing": {"default": 0, "range": "0-2", "notes": "0=Normal, 1=Osc1 bypass, 2=Both bypass"},
                "drive": {"default": 0, "range": "0-127"},
                "drive_type": {"default": 0, "range": "0-6", "notes": "See distortion types"},
                "filter_type": {"default": 1, "range": "0-5", "notes": "See filter types"},
                "filter_frequency": {"default": 127, "range": "0-127"},
                "filter_tracking": {"default": 0, "range": "0-127"},
                "filter_resonance": {"default": 0, "range": "0-127"},
                "filter_q_normalize": {"default": 64, "range": "0-127"},
                "env2_to_filter_freq": {"default": 64, "range": "0-127", "notes": "center=64"},
            },
            "env_amp": {
                "env1_velocity": {"default": 64, "range": "0-127", "notes": "center=64"},
                "env1_attack": {"default": 2, "range": "0-127"},
                "env1_decay": {"default": 90, "range": "0-127"},
                "env1_sustain": {"default": 127, "range": "0-127"},
                "env1_release": {"default": 40, "range": "0-127"},
            },
            "env_filter": {
                "env2_velocity": {"default": 64, "range": "0-127"},
                "env2_attack": {"default": 2, "range": "0-127"},
                "env2_decay": {"default": 75, "range": "0-127"},
                "env2_sustain": {"default": 35, "range": "0-127"},
                "env2_release": {"default": 45, "range": "0-127"},
            },
            "env3": {
                "env3_delay": {"default": 0, "range": "0-127"},
                "env3_attack": {"default": 10, "range": "0-127"},
                "env3_decay": {"default": 70, "range": "0-127"},
                "env3_sustain": {"default": 64, "range": "0-127"},
                "env3_release": {"default": 40, "range": "0-127"},
            },
            "lfo1": "waveform(0-37), phase_offset(0-119), slew_rate, delay, delay_sync(0-35), rate(def=68), rate_sync(0-35), flags(bitfield)",
            "lfo2": "Same layout as lfo1 (prefix lfo2_)",
            "effects": {
                "distortion_level": {"default": 0, "range": "0-127"},
                "distortion_type": {"default": 0, "range": "0-6"},
                "distortion_compensation": {"default": 100, "range": "0-127"},
                "chorus_level": {"default": 0, "range": "0-127"},
                "chorus_type": {"default": 1, "range": "0-1", "notes": "0=Phaser, 1=Chorus"},
                "chorus_rate": {"default": 20, "range": "0-127"},
                "chorus_feedback": {"default": 74, "range": "0-127"},
                "chorus_mod_depth": {"default": 64, "range": "0-127"},
                "chorus_delay": {"default": 64, "range": "0-127"},
            },
            "eq": {
                "eq_bass_frequency": {"default": 64, "range": "0-127"},
                "eq_bass_level": {"default": 64, "range": "0-127", "notes": "center=64"},
                "eq_mid_frequency": {"default": 64, "range": "0-127"},
                "eq_mid_level": {"default": 64, "range": "0-127", "notes": "center=64"},
                "eq_treble_frequency": {"default": 125, "range": "0-127"},
                "eq_treble_level": {"default": 64, "range": "0-127", "notes": "center=64"},
            },
        },
        "lookup_tables": {
            "osc_waveforms": OSC_WAVEFORMS,
            "filter_types": FILTER_TYPES,
            "distortion_types": DISTORTION_TYPES,
            "lfo_waveforms": LFO_WAVEFORMS,
            "mod_matrix_sources": MOD_MATRIX_SOURCES,
            "mod_matrix_destinations": MOD_MATRIX_DESTINATIONS,
            "macro_destinations": MACRO_DESTINATIONS,
        },
        "presets": ["pad", "bass", "lead", "pluck"],
    }


def _resolve_param(param_name: str, cc_maps: list[dict], nrpn_maps: list[dict]):
    """Find a param in the given CC/NRPN lookup dicts. Returns ('cc', cc_num) or ('nrpn', (msb, lsb)) or None."""
    for cc_map in cc_maps:
        if param_name in cc_map:
            return ("cc", cc_map[param_name])
    for nrpn_map in nrpn_maps:
        if param_name in nrpn_map:
            return ("nrpn", nrpn_map[param_name])
    return None


def _send_params_at_t(
    channel: int,
    start_values: dict[str, int],
    target: dict[str, int],
    t: float,
    cc_maps: list[dict],
    nrpn_maps: list[dict],
):
    """Send interpolated param values at position t (0.0 to 1.0)."""
    midi = _midi
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


def _run_morph(
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
        # Forward: start → target
        for i in range(1, steps + 1):
            if stop_event.is_set():
                # Only remove if we're still the current morph (avoid race with restart)
                if _morph_threads.get(morph_id) is stop_event:
                    _morph_threads.pop(morph_id, None)
                return
            t = i / steps
            _send_params_at_t(channel, start_values, target, t, cc_maps, nrpn_maps)
            time.sleep(interval)

        if not ping_pong:
            break

        # Backward: target → start
        for i in range(1, steps + 1):
            if stop_event.is_set():
                # Only remove if we're still the current morph (avoid race with restart)
                if _morph_threads.get(morph_id) is stop_event:
                    _morph_threads.pop(morph_id, None)
                return
            t = 1.0 - (i / steps)
            _send_params_at_t(channel, start_values, target, t, cc_maps, nrpn_maps)
            time.sleep(interval)

    if _morph_threads.get(morph_id) is stop_event:
        _morph_threads.pop(morph_id, None)


def _start_morph(
    morph_id: str,
    channel: int,
    start: dict[str, int],
    target: dict[str, int],
    duration_bars: float,
    ping_pong: bool,
    cc_maps: list[dict],
    nrpn_maps: list[dict],
) -> str:
    """Shared logic: validate, launch morph thread, return status string."""
    global _morph_counter

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
    if morph_id in _morph_threads:
        _morph_threads[morph_id].set()

    # Calculate duration from BPM (1 bar = 4 beats)
    bpm = _engine._bpm if _engine._bpm else 78
    seconds_per_bar = 4 * 60.0 / bpm
    duration_seconds = duration_bars * seconds_per_bar

    # ~20 updates per second for smooth morphing
    total_steps = max(1, int(duration_seconds * 20))

    # Set start values immediately
    midi = _midi
    for param_name, value in start.items():
        resolved = _resolve_param(param_name, cc_maps, nrpn_maps)
        if resolved is None:
            continue
        kind, addr = resolved
        if kind == "cc":
            midi.control_change(channel, addr, value)
        else:
            msb, lsb = addr
            midi.nrpn(channel, msb, lsb, value)

    stop_event = threading.Event()
    _morph_threads[morph_id] = stop_event

    thread = threading.Thread(
        target=_run_morph,
        args=(morph_id, channel, start, target, duration_seconds, total_steps,
              stop_event, ping_pong, cc_maps, nrpn_maps),
        daemon=True,
    )
    thread.start()

    param_list = ", ".join(f"{k}: {start[k]}→{target[k]}" for k in target)
    mode = "ping-pong" if ping_pong else "one-shot"
    cycle_info = f" (full cycle: {duration_bars * 2} bars)" if ping_pong else ""
    return (
        f"Morph '{morph_id}' [{mode}] over {duration_bars} bars "
        f"({duration_seconds:.1f}s){cycle_info}: {param_list}"
    )


@mcp.tool()
def morph_synth_params(
    synth: int,
    start: dict[str, int],
    target: dict[str, int],
    duration_bars: float = 4,
    ping_pong: bool = False,
    name: str = "",
) -> str:
    """Smoothly morph synth parameters from start values to target values over time.

    Runs in the background while the sequencer plays. Interpolates all given
    parameters linearly over the specified number of bars at the current BPM.

    Multiple morphs can run concurrently on the same synth — each controls
    different params (e.g. a slow filter sweep + a fast chorus wobble).
    Give each morph a name to manage them individually with stop_morph.

    When ping_pong is True, the morph continuously sweeps back and forth between
    start and target values (like an LFO). duration_bars is the time for one
    sweep direction, so a full cycle is 2x duration_bars.

    Use stop_morph to cancel morphs by name, or stop_all_morphs to cancel all.

    Args:
        synth: Synth number (1 or 2).
        start: Starting param values. e.g. {"filter_frequency": 30, "post_fx_level": 40}
        target: Target param values. e.g. {"filter_frequency": 80, "post_fx_level": 90}
        duration_bars: Duration in bars for one sweep direction. Default 4 bars.
        ping_pong: If True, continuously sweep back and forth. Default False.
        name: Optional name for this morph (e.g. "filter_sweep", "chorus_wobble").
            Auto-generated if empty. Used to stop specific morphs.
    """
    global _morph_counter

    if synth not in (1, 2):
        return f"Invalid synth number {synth}. Must be 1 or 2."

    if not name:
        _morph_counter += 1
        name = f"morph_{_morph_counter}"
    morph_id = f"s{synth}_{name}"

    channel = SYNTH1_CHANNEL if synth == 1 else SYNTH2_CHANNEL
    return _start_morph(
        morph_id, channel, start, target, duration_bars, ping_pong,
        cc_maps=[SYNTH_CC], nrpn_maps=[SYNTH_NRPN],
    )


@mcp.tool()
def morph_project_params(
    start: dict[str, int],
    target: dict[str, int],
    duration_bars: float = 4,
    ping_pong: bool = False,
    name: str = "",
) -> str:
    """Smoothly morph project-level parameters: reverb, delay, master filter, mixer, sidechain.

    Same behavior as morph_synth_params but for project parameters on MIDI channel 16.
    Multiple morphs can run concurrently with different names.

    Available CC params: reverb_synth1_send, reverb_synth2_send, reverb_drum1-4_send,
    delay_synth1_send, delay_synth2_send, delay_drum1-4_send,
    synth1_level, synth2_level, synth1_pan, synth2_pan,
    master_filter_frequency (0-63=LP, 64=OFF, 65-127=HP), master_filter_resonance.

    Available NRPN params: reverb_type, reverb_decay, reverb_damping,
    delay_time, delay_time_sync, delay_feedback, delay_width, delay_lr_ratio, delay_slew_rate,
    fx_bypass, sidechain_synth1/2_source/attack/hold/decay/depth.

    Args:
        start: Starting param values. e.g. {"master_filter_frequency": 64, "reverb_decay": 30}
        target: Target param values. e.g. {"master_filter_frequency": 20, "reverb_decay": 100}
        duration_bars: Duration in bars for one sweep direction. Default 4 bars.
        ping_pong: If True, continuously sweep back and forth. Default False.
        name: Optional name for this morph. Auto-generated if empty.
    """
    global _morph_counter

    if not name:
        _morph_counter += 1
        name = f"morph_{_morph_counter}"
    morph_id = f"proj_{name}"

    return _start_morph(
        morph_id, PROJECT_CHANNEL, start, target, duration_bars, ping_pong,
        cc_maps=[PROJECT_CC], nrpn_maps=[PROJECT_NRPN],
    )


@mcp.tool()
def morph_drum_params(
    drum: int,
    start: dict[str, int],
    target: dict[str, int],
    duration_bars: float = 4,
    ping_pong: bool = False,
    name: str = "",
) -> str:
    """Smoothly morph drum parameters: level, pitch, decay, distortion, eq, pan.

    Same behavior as morph_synth_params but for drum track parameters on MIDI channel 10.
    Multiple morphs can run concurrently with different names.

    Available params: patch_select, level, pitch, decay, distortion, eq, pan.

    Args:
        drum: Drum number (1-4).
        start: Starting param values. e.g. {"pitch": 40, "decay": 30}
        target: Target param values. e.g. {"pitch": 100, "decay": 90}
        duration_bars: Duration in bars for one sweep direction. Default 4 bars.
        ping_pong: If True, continuously sweep back and forth. Default False.
        name: Optional name for this morph. Auto-generated if empty.
    """
    global _morph_counter

    if drum not in DRUM_CC:
        return f"Invalid drum number {drum}. Must be 1-4."

    if not name:
        _morph_counter += 1
        name = f"morph_{_morph_counter}"
    morph_id = f"d{drum}_{name}"

    return _start_morph(
        morph_id, DRUMS_CHANNEL, start, target, duration_bars, ping_pong,
        cc_maps=[DRUM_CC[drum]], nrpn_maps=[],
    )


@mcp.tool()
def stop_morph(name: str = "", synth: int = 0) -> str:
    """Stop one or more parameter morphs.

    Can stop by name, by synth, or all at once.

    Args:
        name: Stop a specific morph by name. If empty, uses synth param.
        synth: Stop all morphs on this synth (1 or 2). If 0, stops all morphs.
    """
    if name:
        # Stop by exact name — check both synths
        stopped = []
        for morph_id in list(_morph_threads.keys()):
            if morph_id.endswith(f"_{name}"):
                _morph_threads[morph_id].set()
                del _morph_threads[morph_id]
                stopped.append(morph_id)
        if stopped:
            return f"Stopped: {', '.join(stopped)}"
        return f"No morph named '{name}' found"

    if synth in (1, 2):
        # Stop all morphs on a specific synth
        prefix = f"s{synth}_"
        stopped = []
        for morph_id in list(_morph_threads.keys()):
            if morph_id.startswith(prefix):
                _morph_threads[morph_id].set()
                del _morph_threads[morph_id]
                stopped.append(morph_id)
        if stopped:
            return f"Stopped {len(stopped)} morph(es) on synth {synth}: {', '.join(stopped)}"
        return f"No morphs running on synth {synth}"

    # Stop everything
    count = len(_morph_threads)
    for stop_event in _morph_threads.values():
        stop_event.set()
    _morph_threads.clear()
    return f"Stopped all {count} morph(es)" if count else "No morphs running"


@mcp.tool()
def send_project_file(file_path: str, slot: int = 0, filename: str = "") -> dict:
    """Send an .ncs project file to the Circuit Tracks via SysEx.

    Transfers a complete project file (160,780 bytes) to the device using
    the Novation Components file management protocol. The device must be
    connected and will show transfer progress.

    Args:
        file_path: Path to the .ncs project file.
        slot: Target project slot on the device (0-63).
        filename: Filename to set on device. If empty, auto-generated from slot.
    """
    with open(file_path, "rb") as f:
        ncs_data = f.read()

    return send_ncs_project(
        _midi,
        ncs_data,
        slot=slot,
        filename=filename if filename else None,
    )


_current_song = None  # type: ignore  # Stores the last loaded SongData for export
_current_project_slot: int | None = None  # Tracks last selected project slot


@mcp.tool()
def load_song(song: dict) -> dict:
    """Load a complete song into the MCP's internal sequencer for preview.

    This does NOT write anything to the Circuit Tracks project storage.
    Synth patches and drum samples are sent to the device for live preview,
    but patterns, FX, mixer, and song structure stay in the MCP sequencer
    only. Use start_sequencer to play back, then export_song_to_project
    to save permanently to a project slot on the device.

    The song format:
    {
      "name": "My Song",
      "bpm": 120,
      "swing": 50,
      "scale": {"root": "C", "type": "minor"},
      "sounds": {
        "synth1": {"preset": "pad", "name": "WarmPad", "params": {...}, "mod_matrix": [...], "macros": {...}},
        "synth2": {"preset": "bass"},
        "drum1": {"sample": 0}, "drum2": {"sample": 2},
        "drum3": {"sample": 42}, "drum4": {"sample": 46}
      },
      "fx": {
        "reverb_preset": 3,
        "delay_preset": 5,
        "reverb": {"type": 2, "decay": 80, "damping": 60},
        "delay": {"time": 64, "feedback": 70},
        "reverb_sends": {"synth1": 40, "drum2": 10},
        "delay_sends": {"synth1": 30},
        "sidechain": {"synth1": {"source": "drum1", "depth": 80}}
      },
      "mixer": {"synth1": {"level": 100, "pan": 64}},
      "patterns": {
        "intro": {"length": 16, "tracks": {
          "synth1": {"steps": {"0": {"note": 60, "velocity": 100, "gate": 0.8}}},
          "drum1": {"steps": {"0": {}, "4": {}, "8": {}, "12": {}}}
        }}
      },
      "song": ["intro", "verse", "chorus", "verse", "chorus"]
    }

    Synth presets: "pad", "bass", "lead", "pluck" (or omit for init patch).
    Sounds.params uses the same parameter names as create_synth_patch.
    Pattern step format is identical to set_pattern.
    Drum steps: {} = trigger at default velocity. "note" field ignored.
    All sections optional except "patterns".

    Args:
        song: Complete song description dict.
    """
    global _current_song
    from circuit_mcp.song import parse_song, load_song_to_sequencer

    try:
        song_data = parse_song(song)
    except ValueError as e:
        return {"error": str(e)}

    result = load_song_to_sequencer(song_data, _engine, _midi)
    _current_song = song_data

    return {
        "status": "loaded",
        "name": song_data.name,
        "bpm": song_data.bpm,
        "patterns": list(song_data.patterns.keys()),
        "song_order": song_data.song,
        **result,
    }


@mcp.tool()
def export_song_to_project(slot: int = -1, name: str = "") -> dict:
    """Export the loaded song to the Circuit Tracks as an NCS project file.

    Converts all patterns, FX, mixer, and song structure into a binary .ncs
    project and transfers it to the device via SysEx. Requires a song to be
    loaded first via load_song.

    By default, exports to the currently selected project slot (set via
    select_project). Pass an explicit slot to override.

    Note: Synth patches are NOT stored in the .ncs file (they are separate
    on the device). Use save_synth_patch to persist patches to flash slots.

    Args:
        slot: Target project slot (0-63). Defaults to the currently selected project.
        name: Project name (up to 16 chars). Uses the song name if empty.
    """
    global _current_song
    if _current_song is None:
        return {"error": "No song loaded. Call load_song first."}

    # Resolve slot: use explicit value, or fall back to current project
    target_slot = slot if slot >= 0 else _current_project_slot
    if target_slot is None:
        return {
            "error": "No project slot specified and no project currently selected. "
            "Either pass a slot number or use select_project first."
        }
    if not 0 <= target_slot <= 63:
        return {"error": f"Invalid slot {target_slot}. Must be 0-63."}

    from circuit_mcp.song import export_song_to_device

    try:
        result = export_song_to_device(_current_song, _midi, slot=target_slot, name=name)
    except Exception as e:
        return {"error": f"Export failed: {e}"}

    return {
        "status": "exported",
        "slot": target_slot,
        "name": name or _current_song.name,
        **result,
    }


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
