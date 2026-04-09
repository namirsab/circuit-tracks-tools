"""circuit-tracks: Python library for controlling Novation Circuit Tracks via MIDI."""

from circuit_tracks.macros import DEFAULT_MACROS, MacroTarget, apply_macro
from circuit_tracks.midi import MidiConnection
from circuit_tracks.morph import MorphEngine
from circuit_tracks.ncs_parser import parse_ncs, serialize_ncs
from circuit_tracks.ncs_transfer import send_ncs_project, send_patch_to_slot
from circuit_tracks.patch import parse_patch_data, request_current_patch, send_current_patch
from circuit_tracks.patch_builder import PatchBuilder
from circuit_tracks.sequencer import Pattern, SequencerEngine, Step, Track, TrackType
from circuit_tracks.song import export_song_to_device, load_song_to_sequencer, parse_song, quantize_to_scale

__all__ = [
    "DEFAULT_MACROS",
    "MacroTarget",
    "MidiConnection",
    "MorphEngine",
    "PatchBuilder",
    "Pattern",
    "SequencerEngine",
    "Step",
    "Track",
    "TrackType",
    "apply_macro",
    "export_song_to_device",
    "load_song_to_sequencer",
    "parse_ncs",
    "parse_patch_data",
    "parse_song",
    "quantize_to_scale",
    "request_current_patch",
    "send_current_patch",
    "send_ncs_project",
    "send_patch_to_slot",
    "serialize_ncs",
]
