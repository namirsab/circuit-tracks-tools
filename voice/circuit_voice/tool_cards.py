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

PROFILES = ("control", "creative")
PROFILE_ALIASES = {"full": "creative", "perform": "control", "steuer": "control", "kreativ": "creative"}
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
        first = ledger.edit_pattern()
        if first is None:
            raise ArgumentError("Es ist kein Song geladen. Lade erst einen Song oder lass einen bauen.")
        out["pattern"] = first
    if not out.get("bpm"):
        out["bpm"] = getattr(ledger, "song_bpm", None) or ledger.bpm or 120  # the song's tempo, not the device's
    return out


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


SYNTH_PARAM_HINTS = (
    "Raw patch parameters (0-127), the base values under the macro knobs; for filter, resonance and envelopes "
    "prefer set_macro. Common: filter_frequency, filter_resonance, "
    "osc1_wave/osc2_wave, osc1_level/osc2_level, osc1_semitones/osc2_semitones (64 = no shift), "
    "env1_attack/env1_decay/env1_sustain/env1_release (Lautstärke-Hüllkurve; 'mehr Release' = env1_release), "
    "env2_attack/env2_decay/env2_sustain/env2_release (Filter-Hüllkurve), env2_to_filter_freq, "
    "lfo1_rate/lfo1_waveform, distortion_level, chorus_level, portamento_rate, "
    "polyphony_mode (0 = mono, 2 = polyphon; chords need 2). "
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
        "Turn one of the device's eight macro knobs (1-8) on synth 1 or 2 to 0-127; this is what the patches on the "
        "device respond to. Usual layout: 1=Oscillator 2=OscMod 3=AmpEnv 4=FilterEnv 5=Filter (Cutoff: 'Filter "
        "auf' = higher, 'zu' = lower) 6=Resonance 7=Modulation 8=FX. Prefer this over set_synth_params for filter, "
        "resonance and envelope requests unless the user names a raw parameter.",
        schema=_obj(
            {
                "synth": {"type": "integer", "minimum": 1, "maximum": 2},
                "macro": {"type": "integer", "minimum": 1, "maximum": 8},
                "value": {"type": "integer", "minimum": 0, "maximum": 127},
            },
            ["synth", "macro", "value"],
        ),
    ),
    ToolCard(
        "select_patch",
        "Select a synth patch on synth 1 or 2 by its number as shown on the device (1-64) or by its name "
        "(needs the names read in once with scan_patches).",
        schema=_obj(
            {
                "synth": {"type": "integer", "minimum": 1, "maximum": 2},
                "number": {"type": "integer", "minimum": 1, "maximum": 64},
                "name": {"type": "string"},
            },
            ["synth"],
        ),
    ),
    ToolCard(
        "list_patches",
        "The patch names on the device with their numbers, optionally filtered by a word ('bass'). Always try "
        "this first; it says when the names still need scan_patches.",
        schema=_obj({"filter": {"type": "string"}}, []),
        read_only=True,
    ),
    ToolCard(
        "scan_patches",
        "Read the names of all 64 patches from the device (a few seconds, only while nothing plays; the selected "
        "patch changes during the scan and is restored). Only after list_patches said the names are unknown.",
        schema=_obj({"synth": {"type": "integer", "minimum": 1, "maximum": 2, "default": 2}}, []),
        blocking=False,
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
        "Start an empty song: one 32-step pattern ('pattern1'), lead on synth1 and bass on synth2. The scale is "
        "C minor like a fresh device project; notes outside it move to the nearest scale note. "
        "Use it when the user wants to build from nothing ('Neuer Song', 'fang neu an').",
        schema=_obj({"name": {"type": "string"}, "bpm": {"type": "number", "minimum": 40, "maximum": 240}}, []),
    ),
    ToolCard(
        "write_notes",
        "Put exact notes or chords on a synth track. notes is an array with one entry per step: a note name the "
        "device uses (C3 = middle, F#4, Bb2, German H) or a MIDI number, or an array of notes for a chord, e.g. "
        '[["E3","G3","B3"], "E3"] = E minor chord, then a single E. The synths play up to six notes at '
        'once. A rest is an empty string "": it keeps its place (\'C3 C3 leer leer C4\' = ["C3","C3","","","C4"]). '
        "spacing = steps between entries (4 = every beat, 2 = eighths, 1 = every step), start_step "
        "is spoken 1-based; without it the notes are appended after the last one. gate defaults to the spacing. "
        "replace=true clears the track first.",
        schema=_obj(
            {
                "pattern": {"type": "string", "description": "pattern name; default: the pattern last worked on"},
                "track": {"type": "string", "enum": ["synth1", "synth2", "midi1", "midi2"]},
                "notes": {
                    "type": "array",
                    "description": "one entry per step; an entry is one note or an array of notes (chord)",
                    "items": {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 6},
                        ]
                    },
                },
                "start_step": {"type": "integer", "minimum": 1, "maximum": 32},
                "spacing": {"type": "integer", "minimum": 1, "maximum": 32, "default": 4},
                "gate": {"type": "number", "minimum": 0.25, "maximum": 16},
                "velocity": {"type": "integer", "minimum": 1, "maximum": 127, "default": 100},
                "velocities": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1, "maximum": 127},
                    "description": "per-note velocities, cycled when shorter (accents: [110, 70])",
                },
                "replace": {"type": "boolean", "default": False},
            },
            ["track", "notes"],
        ),
    ),
    ToolCard(
        "write_drums",
        "Put drum hits on exact steps (spoken 1-based, 1-32) of drum1..drum4, e.g. four-on-the-floor kick = "
        "[1, 5, 9, 13, 17, 21, 25, 29]. Patterns have 32 steps (two bars): always fill both bars. "
        "Adds to existing hits unless replace=true.",
        schema=_obj(
            {
                "pattern": {"type": "string"},
                "track": {"type": "string", "enum": ["drum1", "drum2", "drum3", "drum4"]},
                "steps": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 32}},
                "velocity": {"type": "integer", "minimum": 1, "maximum": 127, "default": 100},
                "velocities": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1, "maximum": 127},
                    "description": "per-hit velocities in step order, cycled when shorter ([110, 70] = accents)",
                },
                "replace": {"type": "boolean", "default": False},
            },
            ["track", "steps"],
        ),
    ),
    ToolCard(
        "set_step",
        "Change one existing step of a track: its velocity, notes (synth; one note or a chord) or gate, e.g. "
        "'Schritt 5 lauter', 'auf Schritt 5 einen C-Dur-Akkord'. Spoken 1-based step.",
        schema=_obj(
            {
                "pattern": {"type": "string"},
                "track": {"type": "string"},
                "step": {"type": "integer", "minimum": 1, "maximum": 32},
                "velocity": {"type": "integer", "minimum": 1, "maximum": 127},
                "notes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 6,
                    "description": "the notes sounding on this step: one note, or several for a chord",
                },
                "gate": {"type": "number", "minimum": 0.25, "maximum": 16},
            },
            ["track", "step"],
        ),
    ),
    ToolCard(
        "list_drum_samples",
        "The drum samples on the device by number and name (64 per page of the bank, page 1-4).",
        mcp_tool="list_drum_samples",
        read_only=True,
    ),
    ToolCard(
        "add_pattern",
        "Add a pattern to the song ('zweites Pattern', 'neues Pattern', 'noch ein Teil'). Without a name it becomes "
        "pattern2, pattern3, ... copy_from = an existing pattern to start from ('eine Kopie', 'eine Variation'); "
        "without it the new pattern is empty. If the user did not say which, ask once: leer oder als Kopie? "
        "The new pattern is appended to the song order, later edits without a pattern name go to it, and a running "
        "sequencer switches to it when the current pattern ends. To go back, pass pattern= to the edit tools or "
        "use queue_patterns.",
        schema=_obj({"name": {"type": "string"}, "copy_from": {"type": "string"}}, []),
    ),
    ToolCard(
        "set_drum_sample",
        "Choose the sample (1-64, by number or by a word from its name such as 'Snare') for drum 1-4. The device "
        "cannot switch samples live over MIDI, so it is stored in the song and audible after saving to the device "
        "and playing the project from there. Says so.",
        schema=_obj(
            {
                "drum": {"type": "integer", "minimum": 1, "maximum": 4},
                "number": {"type": "integer", "minimum": 1, "maximum": 64},
                "name": {"type": "string"},
            },
            ["drum"],
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
        "with confirmed=true. name defaults to the song name (max 16 characters). Without slot ('hier', 'im "
        "aktuellen Slot') it takes the project selected on the device, else the slot of the last save.",
        schema=_obj(
            {
                "slot": {"type": "integer", "minimum": 1, "maximum": 64},
                "name": {"type": "string"},
                "confirmed": {"type": "boolean", "default": False},
            },
            [],
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
    # --- small model jobs: one track, one patch ---
    ToolCard(
        "generate_track",
        "Have a musical idea written for ONE track by a text model (a few seconds): 'groovige Bassline auf Synth 2', "
        "'Hi-Hat-Pattern mit Offbeats auf drum3'. Replaces that track only. Without confirmed it returns how long it "
        "takes and the question to ask; after a clear yes call again with confirmed=true.",
        schema=_obj(
            {
                "pattern": {"type": "string"},
                "track": {"type": "string"},
                "brief": {"type": "string"},
                "confirmed": {"type": "boolean", "default": False},
            },
            ["track", "brief"],
        ),
        blocking=False,
        profiles=("creative",),
    ),
    ToolCard(
        "design_patch",
        "Design a new synth sound from a description ('warmer, weicher Bass mit langsamem Filter') and load it into "
        "synth 1 or 2 (working memory, not saved). Without confirmed it returns the question to ask first; after a "
        "clear yes call again with confirmed=true.",
        schema=_obj(
            {
                "synth": {"type": "integer", "minimum": 1, "maximum": 2},
                "brief": {"type": "string"},
                "confirmed": {"type": "boolean", "default": False},
            },
            ["synth", "brief"],
        ),
        blocking=False,
        profiles=("creative",),
    ),
    ToolCard(
        "save_patch",
        "Save the current sound of synth 1 or 2 into a patch slot (1-64) on the device, overwriting it. Call first "
        "without confirmed to get the question; after a clear yes call again with confirmed=true.",
        schema=_obj(
            {
                "synth": {"type": "integer", "minimum": 1, "maximum": 2},
                "slot": {"type": "integer", "minimum": 1, "maximum": 64},
                "confirmed": {"type": "boolean", "default": False},
            },
            ["synth", "slot"],
        ),
    ),
    # --- composer step (separate model) ---
    ToolCard(
        "compose",
        "Build a new song from a German brief (style, tempo, key, mood, instruments). Runs in the background for "
        "about half a minute. Without confirmed it returns the question to ask first. Replaces the loaded song.",
        schema=_obj({"brief": {"type": "string"}, "confirmed": {"type": "boolean", "default": False}}, ["brief"]),
        blocking=False,
        profiles=("creative",),
    ),
    ToolCard(
        "revise",
        "Change the loaded song by instruction ('Hi-Hats leiser', 'Bassline auf Synth 2 dazu', 'Chorus doppelt so "
        "lang'). Slow (half a minute or more). Without confirmed it returns the question to ask first.",
        schema=_obj(
            {"instruction": {"type": "string"}, "confirmed": {"type": "boolean", "default": False}}, ["instruction"]
        ),
        blocking=False,
        profiles=("creative",),
    ),
    # --- movement over time and housekeeping ---
    ToolCard(
        "morph_synth_params",
        "Sweep synth parameters over time ('Filter langsam von zu nach auf', 'in Bewegung bringen'): start and "
        "target values per parameter, duration in bars, optional ping_pong to go back and forth. Runs in the "
        "background until stop_morph.",
        mcp_tool="morph_synth_params",
    ),
    ToolCard(
        "morph_drum_params",
        "Sweep drum parameters (level, pitch, decay, ...) over time for drum 1-4; same shape as morph_synth_params.",
        mcp_tool="morph_drum_params",
    ),
    ToolCard(
        "morph_project_params",
        "Sweep project parameters (reverb/delay sends, mixer, master filter) over time; same shape as "
        "morph_synth_params.",
        mcp_tool="morph_project_params",
    ),
    ToolCard("stop_morph", "Stop running parameter sweeps (all, by name, or by synth).", mcp_tool="stop_morph"),
    ToolCard(
        "clear_pattern",
        "Remove every step from every track of a pattern ('Pattern eins leeren').",
        mcp_tool="clear_pattern",
    ),
    ToolCard(
        "get_pattern",
        "The steps of a pattern per track, to answer 'Was liegt auf Synth eins?'.",
        mcp_tool="get_pattern",
        read_only=True,
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


def normalise_profile(profile: str) -> str:
    profile = PROFILE_ALIASES.get(profile.lower(), profile.lower())
    if profile not in PROFILES:
        raise ValueError(f"unknown tool profile {profile!r}")
    return profile


def cards_for_profile(profile: str) -> list[ToolCard]:
    profile = normalise_profile(profile)
    return [c for c in CARDS if profile in c.profiles]
