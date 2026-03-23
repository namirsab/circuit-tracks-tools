"""MCP server for controlling Novation Circuit Tracks via MIDI."""

import asyncio

from mcp.server.fastmcp import FastMCP

from circuit_mcp.constants import (
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
)
from circuit_mcp.midi import MidiConnection
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
def connect(port_name: str) -> str:
    """Connect to the Circuit Tracks MIDI output port.

    Args:
        port_name: The exact MIDI port name from list_midi_ports output.
    """
    midi = _midi
    midi.connect(port_name)
    return f"Connected to {port_name}"


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
    }


# --- Note Tools ---


@mcp.tool()
async def play_note(
    channel: int,
    note: int,
    velocity: int = 100,
    duration_ms: int = 500,
) -> str:
    """Play a single MIDI note on the Circuit Tracks.

    Channels: 0=Synth 1, 1=Synth 2, 9=Drums (notes 60,62,64,65 for drums 1-4).

    Args:
        channel: MIDI channel (0-indexed). 0=Synth1, 1=Synth2, 9=Drums.
        note: MIDI note number (0-127). Middle C = 60.
        velocity: Note velocity (0-127).
        duration_ms: How long to hold the note in milliseconds.
    """
    midi = _midi
    midi.note_on(channel, note, velocity)
    await asyncio.sleep(duration_ms / 1000.0)
    midi.note_off(channel, note)
    return f"Played note {note} on channel {channel} (vel={velocity}, dur={duration_ms}ms)"


@mcp.tool()
async def play_chord(
    channel: int,
    notes: list[int],
    velocity: int = 100,
    duration_ms: int = 500,
) -> str:
    """Play multiple notes simultaneously as a chord.

    Args:
        channel: MIDI channel (0-indexed). 0=Synth1, 1=Synth2.
        notes: List of MIDI note numbers to play together.
        velocity: Note velocity (0-127).
        duration_ms: How long to hold the chord in milliseconds.
    """
    midi = _midi
    for note in notes:
        midi.note_on(channel, note, velocity)
    await asyncio.sleep(duration_ms / 1000.0)
    for note in notes:
        midi.note_off(channel, note)
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
        length: Pattern length in steps (16 or 32), default 16.
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


# --- CC / NRPN Tools ---


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
def set_synth_param(synth: int, param_name: str, value: int) -> str:
    """Set a named synth parameter on Synth 1 or 2.

    Common params: filter_frequency, filter_resonance, osc1_wave, osc2_wave,
    osc1_level, osc2_level, env1_attack, env1_decay, env1_sustain, env1_release,
    drive, distortion_level, chorus_level, macro_knob1-8.

    Args:
        synth: Synth number (1 or 2).
        param_name: Parameter name (e.g., "filter_frequency").
        value: Parameter value (0-127 unless otherwise noted).
    """
    if synth not in (1, 2):
        return f"Invalid synth number {synth}. Must be 1 or 2."

    channel = SYNTH1_CHANNEL if synth == 1 else SYNTH2_CHANNEL
    midi = _midi

    if param_name in SYNTH_CC:
        cc = SYNTH_CC[param_name]
        midi.control_change(channel, cc, value)
        return f"Set synth {synth} {param_name}={value} (CC {cc})"
    elif param_name in SYNTH_NRPN:
        msb, lsb = SYNTH_NRPN[param_name]
        midi.nrpn(channel, msb, lsb, value)
        return f"Set synth {synth} {param_name}={value} (NRPN {msb}:{lsb})"
    else:
        available_cc = ", ".join(sorted(SYNTH_CC.keys()))
        available_nrpn = ", ".join(sorted(SYNTH_NRPN.keys()))
        return f"Unknown param '{param_name}'. CC params: {available_cc}. NRPN params: {available_nrpn}."


@mcp.tool()
def set_drum_param(drum: int, param_name: str, value: int) -> str:
    """Set a drum parameter on drums 1-4.

    Params: patch_select, level, pitch, decay, distortion, eq, pan.

    Args:
        drum: Drum number (1-4).
        param_name: Parameter name.
        value: Parameter value (0-127).
    """
    if drum not in DRUM_CC:
        return f"Invalid drum number {drum}. Must be 1-4."
    params = DRUM_CC[drum]
    if param_name not in params:
        return f"Unknown drum param '{param_name}'. Available: {', '.join(params.keys())}."

    midi = _midi
    cc = params[param_name]
    midi.control_change(DRUMS_CHANNEL, cc, value)
    return f"Set drum {drum} {param_name}={value} (CC {cc})"


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
    if not 0 <= project_number <= 63:
        return f"Invalid project number {project_number}. Must be 0-63."

    midi = _midi
    program = project_number + (64 if queued else 0)
    midi.program_change(PROJECT_CHANNEL, program)
    mode = "queued" if queued else "instant"
    return f"Selected project {project_number} ({mode})"


@mcp.tool()
def set_project_param(param_name: str, value: int) -> str:
    """Set a project-level parameter (reverb, delay, mixer, sidechain, master filter).

    Common params: reverb_synth1_send, delay_synth1_send, synth1_level,
    master_filter_frequency, reverb_type, delay_time, delay_feedback.

    Args:
        param_name: Parameter name.
        value: Parameter value.
    """
    midi = _midi

    if param_name in PROJECT_CC:
        cc = PROJECT_CC[param_name]
        midi.control_change(PROJECT_CHANNEL, cc, value)
        return f"Set project {param_name}={value} (CC {cc})"
    elif param_name in PROJECT_NRPN:
        msb, lsb = PROJECT_NRPN[param_name]
        midi.nrpn(PROJECT_CHANNEL, msb, lsb, value)
        return f"Set project {param_name}={value} (NRPN {msb}:{lsb})"
    else:
        available_cc = ", ".join(sorted(PROJECT_CC.keys()))
        available_nrpn = ", ".join(sorted(PROJECT_NRPN.keys()))
        return f"Unknown param '{param_name}'. CC params: {available_cc}. NRPN params: {available_nrpn}."


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
def get_parameter_info() -> dict:
    """Get a reference of all available parameter names organized by category.

    Returns the complete list of parameter names you can use with
    set_synth_param, set_drum_param, and set_project_param.
    """
    return {
        "synth_cc_params": sorted(SYNTH_CC.keys()),
        "synth_nrpn_params": sorted(SYNTH_NRPN.keys()),
        "drum_params": ["patch_select", "level", "pitch", "decay", "distortion", "eq", "pan"],
        "drum_numbers": [1, 2, 3, 4],
        "project_cc_params": sorted(PROJECT_CC.keys()),
        "project_nrpn_params": sorted(PROJECT_NRPN.keys()),
        "channels": {
            "synth1": 0,
            "synth2": 1,
            "drums": 9,
            "project": 15,
        },
    }


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
