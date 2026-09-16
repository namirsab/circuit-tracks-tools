"""The curated tools the voice agent exposes to the model.

A card is data: which MCP tool it maps to (or none for a virtual tool the
harness implements), a voice-oriented description, whether the model must wait
for the result (BLOCKING) or may keep talking (NON_BLOCKING), an optional
schema override, an argument normaliser (spoken 1-based numbers → MIDI 0-based),
and which profiles include it. German aliases live in the system instruction,
not here; the model reads these descriptions in English.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

PROFILES = ("full", "perform")
TRACKS = ["synth1", "synth2", "drum1", "drum2", "drum3", "drum4", "midi1", "midi2"]


class ArgumentError(ValueError):
    """Bad spoken argument; the message is German and goes back to the model."""


@dataclass(frozen=True)
class ToolCard:
    name: str
    description: str
    mcp_tool: str | None = None  # None: virtual tool, handled by the harness
    blocking: bool = True
    read_only: bool = False
    schema: dict[str, Any] | None = None  # overrides the server's inputSchema
    normalise: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    # (args, ledger) -> args with defaults filled from what the agent knows
    fill: Callable[[dict[str, Any], Any], dict[str, Any]] | None = None
    # (spoken args, tool result) -> what the model gets back; default: the result itself
    summarise: Callable[[dict[str, Any], Any], Any] | None = None
    # async (bridge, args) -> note prefixed to the result, run before the tool (e.g. stop the other source)
    before: Callable[..., Any] | None = None
    profiles: tuple[str, ...] = PROFILES


def _int_in_range(args: dict[str, Any], key: str, lo: int, hi: int, label: str) -> int:
    try:
        value = int(args[key])
    except (KeyError, TypeError, ValueError) as e:
        raise ArgumentError(f"{label} fehlt oder ist keine Zahl.") from e
    if not lo <= value <= hi:
        raise ArgumentError(f"{label} muss zwischen {lo} und {hi} liegen.")
    return value


def _select_patch(args: dict[str, Any]) -> dict[str, Any]:
    synth = _int_in_range(args, "synth", 1, 2, "Der Synth")
    number = _int_in_range(args, "number", 1, 64, "Die Patch-Nummer")
    return {"synth": synth, "patch_number": number - 1}


def _fill_start(args: dict[str, Any], ledger: Any) -> dict[str, Any]:
    out = dict(args)
    if not out.get("pattern"):
        first = ledger.first_pattern()
        if first is None:
            raise ArgumentError("Es ist kein Song geladen. Lade erst einen Song oder lass einen bauen.")
        out["pattern"] = first
    if not out.get("bpm"):
        out["bpm"] = ledger.bpm or 120
    return out


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


SYNTH_PARAM_HINTS = (
    "Common params (0-127): filter_frequency (Filter/Cutoff auf=higher), filter_resonance, "
    "osc1_wave/osc2_wave, osc1_level/osc2_level, osc2_pitch_coarse, "
    "amp_env_attack/amp_env_decay/amp_env_sustain/amp_env_release (Hüllkurve), "
    "filter_env_attack/filter_env_decay/filter_env_sustain/filter_env_release, filter_env_depth, "
    "lfo1_rate/lfo1_wave, distortion_level, chorus_level, portamento, voice_mode. "
    "Without a number, move about 20 steps ('etwas' 10, 'viel' 40)."
)

CARDS: list[ToolCard] = [
    # --- internal sequencer (workflow A) ---
    ToolCard(
        "get_sequencer_status",
        "Read the internal sequencer state: running, current pattern and step, BPM, muted tracks, pattern queue, "
        "patterns defined. Use it to answer 'Was läuft gerade?' before speaking about state.",
        mcp_tool="get_sequencer_status",
        read_only=True,
    ),
    ToolCard(
        "start_sequencer",
        "Start the internal sequencer looping a pattern. Omit pattern and bpm to use the loaded song's first pattern "
        "and tempo. Stops the device transport first if it is running.",
        mcp_tool="start_sequencer",
        schema=_obj(
            {
                "pattern": {"type": "string", "description": "pattern name; default: first pattern of the song"},
                "bpm": {"type": "number", "description": "tempo; default: the loaded song's tempo"},
            },
            [],
        ),
        fill=_fill_start,
        before="stop_device_first",
    ),
    ToolCard("stop_sequencer", "Stop the internal sequencer immediately (all notes off).", mcp_tool="stop_sequencer"),
    ToolCard(
        "set_bpm",
        "Change the internal sequencer tempo while it plays. For 'schneller'/'langsamer' without a number, "
        "change by about 5 BPM ('viel' 15) from the current BPM.",
        mcp_tool="set_bpm",
    ),
    ToolCard(
        "mute_track",
        "Mute or unmute one track: synth1, synth2, drum1..drum4, midi1, midi2. 'Drums' or 'Schlagzeug' means all four "
        "drum tracks (call once per track). muted=false unmutes. Works for the internal sequencer and, via levels, "
        "for a project playing on the device.",
        schema=_obj(
            {"track": {"type": "string", "enum": TRACKS}, "muted": {"type": "boolean", "default": True}},
            ["track"],
        ),
    ),
    ToolCard(
        "queue_patterns",
        "Queue patterns to play after the current one, in order (song structure by voice).",
        mcp_tool="queue_patterns",
    ),
    ToolCard("set_song", "Replace the whole pattern queue with this ordered list of patterns.", mcp_tool="set_song"),
    ToolCard("clear_queue", "Clear the pattern queue so the current pattern loops.", mcp_tool="clear_queue"),
    ToolCard("list_patterns", "List the pattern names of the loaded song.", mcp_tool="list_patterns", read_only=True),
    # --- sounds (both workflows) ---
    ToolCard(
        "set_synth_params",
        "Set synth parameters on synth 1 or 2 while playing. " + SYNTH_PARAM_HINTS,
        mcp_tool="set_synth_params",
    ),
    ToolCard(
        "set_drum_params",
        "Set drum parameters on drum 1-4 (0-127): level (leiser/lauter), pitch, decay, distortion, eq, pan.",
        mcp_tool="set_drum_params",
    ),
    ToolCard(
        "set_project_params",
        "Set project-wide parameters (0-127): reverb_synth1_send/reverb_synth2_send/reverb_drum1_send.. (Hall), "
        "delay_*_send (Echo/Delay), synth1_level/synth2_level/synth1_pan/synth2_pan (Mixer), "
        "master_filter_frequency/master_filter_resonance, reverb_decay, delay_time, delay_feedback.",
        mcp_tool="set_project_params",
    ),
    ToolCard(
        "set_macro",
        "Turn a macro knob (1-8) on synth 1 or 2 to a value 0-127. 1=Oscillator 2=OscMod 3=AmpEnv 4=FilterEnv "
        "5=FilterFreq 6=Resonance 7=Modulation 8=FX.",
        mcp_tool="set_macro",
    ),
    ToolCard(
        "select_patch",
        "Select a synth patch by its number as shown on the device (1-64) on synth 1 or 2.",
        mcp_tool="select_patch",
        schema=_obj(
            {
                "synth": {"type": "integer", "minimum": 1, "maximum": 2},
                "number": {"type": "integer", "minimum": 1, "maximum": 64},
            },
            ["synth", "number"],
        ),
        normalise=_select_patch,
        summarise=lambda a, r: f"Patch {a['number']} auf Synth {a['synth']} gewählt",
    ),
    ToolCard(
        "play_notes",
        "Audition notes: play MIDI notes on channel 0 (synth 1) or 1 (synth 2) for duration_ms.",
        mcp_tool="play_notes",
    ),
    ToolCard("play_drum", "Audition one drum hit (drum 1-4).", mcp_tool="play_drum"),
    ToolCard(
        "get_synth_patch",
        "Read the current patch of synth 1 or 2 from the device (name and parameters). Slow: a SysEx round trip.",
        mcp_tool="get_synth_patch",
        blocking=False,
        read_only=True,
    ),
    # --- precise, step-by-step building (no model in between) ---
    ToolCard(
        "new_song",
        "Start an empty song: one 32-step pattern ('pattern1'), chromatic scale, lead on synth1 and bass on synth2. "
        "Use it when the user wants to build from nothing ('Neuer Song', 'fang neu an').",
        schema=_obj({"name": {"type": "string"}, "bpm": {"type": "number", "minimum": 40, "maximum": 240}}, []),
    ),
    ToolCard(
        "write_notes",
        "Put exact notes on a synth track, one per step: notes as names the device uses (C3 = middle, F#4, Bb2, "
        "German H) or MIDI numbers, e.g. 'C3 E3 G3'. spacing = steps between notes (4 = every beat), start_step "
        "is spoken 1-based; without it the notes are appended after the last one. gate defaults to the spacing. "
        "replace=true clears the track first.",
        schema=_obj(
            {
                "pattern": {"type": "string", "description": "pattern name; default: the first pattern"},
                "track": {"type": "string", "enum": ["synth1", "synth2", "midi1", "midi2"]},
                "notes": {"type": "string", "description": "space-separated note names or numbers"},
                "start_step": {"type": "integer", "minimum": 1, "maximum": 32},
                "spacing": {"type": "integer", "minimum": 1, "maximum": 32, "default": 4},
                "gate": {"type": "number", "minimum": 0.25, "maximum": 16},
                "velocity": {"type": "integer", "minimum": 1, "maximum": 127, "default": 100},
                "replace": {"type": "boolean", "default": False},
            },
            ["track", "notes"],
        ),
    ),
    ToolCard(
        "write_drums",
        "Put drum hits on exact steps (spoken 1-based, 1-32) of drum1..drum4, e.g. kick on [1, 5, 9, 13]. "
        "Adds to existing hits unless replace=true.",
        schema=_obj(
            {
                "pattern": {"type": "string"},
                "track": {"type": "string", "enum": ["drum1", "drum2", "drum3", "drum4"]},
                "steps": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 32}},
                "velocity": {"type": "integer", "minimum": 1, "maximum": 127, "default": 100},
                "replace": {"type": "boolean", "default": False},
            },
            ["track", "steps"],
        ),
    ),
    ToolCard(
        "clear_track",
        "Remove every note or hit from one track of a pattern.",
        schema=_obj({"pattern": {"type": "string"}, "track": {"type": "string"}}, ["track"]),
    ),
    # --- songs on disk and on the device ---
    ToolCard(
        "list_saved_songs",
        "List the saved songs the user can load by name ('Welche Songs gibt es?').",
        schema=_obj({}, []),
        read_only=True,
    ),
    ToolCard(
        "load_saved_song",
        "Load a saved song by its spoken name into the internal sequencer (does not start playback).",
        schema=_obj({"name": {"type": "string"}}, ["name"]),
    ),
    ToolCard(
        "save_to_device",
        "Save the loaded song into a project slot (1-64) on the Circuit Tracks, overwriting that slot. Call it "
        "first without confirmed; it returns the German question to ask. After the user says yes, call it again "
        "with confirmed=true. name defaults to the song name (max 16 characters).",
        schema=_obj(
            {
                "slot": {"type": "integer", "minimum": 1, "maximum": 64},
                "name": {"type": "string"},
                "confirmed": {"type": "boolean", "default": False},
            },
            ["slot"],
        ),
    ),
    # --- projects stored on the device (workflow B) ---
    ToolCard(
        "transport",
        "Start, stop or continue the project playing on the Circuit Tracks itself (its own patterns, our MIDI "
        "clock). Stops the internal sequencer first if it runs. bpm is optional; the device's project tempo is "
        "used otherwise.",
        schema=_obj(
            {"action": {"type": "string", "enum": ["start", "stop", "continue"]}, "bpm": {"type": "number"}}, ["action"]
        ),
    ),
    ToolCard(
        "set_tempo",
        "Change the tempo (20-300 BPM) while the device plays from our clock. For the internal sequencer use "
        "set_bpm instead.",
        schema=_obj({"bpm": {"type": "number", "minimum": 20, "maximum": 300}}, ["bpm"]),
    ),
    ToolCard(
        "select_project",
        "Select a project stored on the device by its number (1-64). While playing it switches at the end of "
        "the pattern unless immediate=true. Returns a German question when sounds were changed since the last "
        "project select; ask the user and call again with confirmed=true after a clear yes.",
        schema=_obj(
            {
                "number": {"type": "integer", "minimum": 1, "maximum": 64},
                "immediate": {"type": "boolean", "default": False},
                "confirmed": {"type": "boolean", "default": False},
            },
            ["number"],
        ),
    ),
    ToolCard(
        "import_project",
        "Read a project (1-64) from the device into the internal sequencer as a song, so it can be revised and "
        "saved again. Slow (SysEx).",
        schema=_obj({"number": {"type": "integer", "minimum": 1, "maximum": 64}}, ["number"]),
        blocking=False,
    ),
    # --- composer step (separate model) ---
    ToolCard(
        "compose",
        "Build a new song from a German brief (style, tempo, key, mood, instruments). Runs in the background for "
        "about half a minute: say you are working on it, stay responsive, and summarise when the result arrives. "
        "Replaces the loaded song.",
        schema=_obj({"brief": {"type": "string"}}, ["brief"]),
        blocking=False,
        profiles=("full",),
    ),
    ToolCard(
        "revise",
        "Change the loaded song by instruction ('Hi-Hats leiser', 'Bassline auf Synth 2 dazu', 'Chorus doppelt so "
        "lang'). Runs in the background; keeps the music playing where possible.",
        schema=_obj({"instruction": {"type": "string"}}, ["instruction"]),
        blocking=False,
        profiles=("full",),
    ),
    # --- virtual tools (harness) ---
    ToolCard(
        "get_state",
        "What the agent knows: playback source, loaded song, tempo, mutes, unsaved changes, last change. "
        "Cheap; never touches the device. Only when the user asks about the state, never before a change.",
        schema=_obj({}, []),
        read_only=True,
    ),
    ToolCard(
        "undo_last",
        "Revert the last parameter, tempo or mute change ('Rückgängig'). Says when nothing can be undone.",
        schema=_obj({}, []),
    ),
    ToolCard(
        "set_listening",
        "Switch how the agent listens: 'ptt' (hold the key), 'toggle' (press to start and stop), 'open' (always "
        "listening, headphones recommended).",
        schema=_obj({"mode": {"type": "string", "enum": ["ptt", "toggle", "open"]}}, ["mode"]),
    ),
]

_BY_NAME = {c.name: c for c in CARDS}


def card(name: str) -> ToolCard | None:
    return _BY_NAME.get(name)


def cards_for_profile(profile: str) -> list[ToolCard]:
    if profile not in PROFILES:
        raise ValueError(f"unknown tool profile {profile!r}")
    return [c for c in CARDS if profile in c.profiles]
