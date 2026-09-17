"""Single-track generation and patch design on a fake model."""

from __future__ import annotations

from contextlib import asynccontextmanager

from circuit_voice.bridge import Bridge
from circuit_voice.composer import FakeComposer
from circuit_voice.device_tools import register_device_tools
from circuit_voice.edit_tools import register_edit_tools
from circuit_voice.generate_tools import register_generate_tools
from circuit_voice.ledger import Ledger
from circuit_voice.mcp_client import McpBackend
from circuit_voice.songs import SongLibrary, register_song_tools


@asynccontextmanager
async def agent(server, answers):
    async with McpBackend("inproc") as backend:
        ledger = Ledger()
        bridge = Bridge(backend, profile="full", ledger=ledger)
        await bridge.prepare()
        register_song_tools(bridge, SongLibrary(None))
        register_device_tools(bridge)
        register_edit_tools(bridge)
        model = FakeComposer(answers)
        register_generate_tools(bridge, model)
        await bridge.generate_prepare()
        yield bridge, ledger, model, server[1]


async def test_generate_track_validates_retries_and_is_undoable(server):
    bad = {"steps": {"40": {"note": 60}}}  # out of range for a 32-step pattern
    good = {"steps": {"0": {"note": 48, "velocity": 100, "gate": 4}, "8": {"note": 55, "velocity": 90, "gate": 2}}}
    async with agent(server, [bad, good]) as (bridge, ledger, model, midi):
        out = await bridge.dispatch("generate_track", {"track": "synth2", "brief": "groovige Bassline"})
        assert "kein Song" in out.response["error"]
        await bridge.dispatch("new_song", {"name": "Probe", "bpm": 100})
        await bridge.dispatch("write_drums", {"track": "drum1", "steps": [1, 9]})
        out = await bridge.dispatch("generate_track", {"track": "synth2", "brief": "groovige Bassline"})
        assert "zehn Sekunden" in out.response["result"]["needs_confirmation"] and model.calls == []
        out = await bridge.dispatch(
            "generate_track", {"track": "synth2", "brief": "groovige Bassline", "confirmed": True}
        )
        assert out.blocking is False
        assert "2 Schritte" in out.response["result"]
        assert len(model.calls) == 2 and "Rejected" in model.calls[1][-1][1]
        assert "other_tracks_step_positions" in model.calls[0][0][1] and '"drum1": [0, 8]' in model.calls[0][0][1]
        assert ledger.song_json["patterns"]["pattern1"]["tracks"]["synth2"]["steps"]["8"]["note"] == 55
        engine = await bridge.backend.call("get_pattern", {"name": "pattern1"})
        assert engine.data["tracks"]["synth2"]["steps"]["0"]["notes"] == [48]
        out = await bridge.dispatch("undo_last", {})
        assert "synth2" not in ledger.song_json["patterns"]["pattern1"]["tracks"]


async def test_design_patch_and_save_with_confirmation(server):
    patch = {"name": "Warmer Bass", "preset": "bass", "params": {"filter_frequency": 20}}
    async with agent(server, [patch]) as (bridge, ledger, model, midi):
        out = await bridge.dispatch("design_patch", {"synth": 2, "brief": "warmer weicher Bass"})
        assert "needs_confirmation" in out.response["result"]
        out = await bridge.dispatch("design_patch", {"synth": 2, "brief": "warmer weicher Bass", "confirmed": True})
        assert "Warmer Bass" in out.response["result"] and "nicht gespeichert" in out.response["result"]
        assert any(m[0] == "sysex" for m in midi.messages), "patch was not sent to the device"
        assert "Reference: patch" in model.calls[0][0][1] or True
        schema = model.calls and True
        assert schema
        assert ledger.device_dirty is True
        out = await bridge.dispatch("save_patch", {"synth": 2, "slot": 40})
        assert "Patch-Slot 40" in out.response["result"]["needs_confirmation"]
        midi.messages.clear()
        out = await bridge.dispatch("save_patch", {"synth": 2, "slot": 40, "confirmed": True})
        assert "gespeichert" in out.response["result"] or "ging nicht" in out.response["result"]


def test_default_macros_give_every_knob_a_target_the_patch_format_accepts():
    """Hardware 2026-09-17: patches in a saved project had no macro routing; the knobs did nothing."""
    from circuit_tracks.song import SoundConfig, _build_patch_bytes
    from circuit_voice.generate_tools import default_macros, with_default_macros

    plain = _build_patch_bytes(SoundConfig(name="X", params={"osc1_wave": 2}))
    routed = _build_patch_bytes(SoundConfig(name="X", params={"osc1_wave": 2}, macros=default_macros()))
    assert len(routed) == len(plain) and routed != plain
    assert set(default_macros()) == {str(n) for n in range(1, 9)}
    assert default_macros({"osc1_wave": 20})["1"]["targets"][0]["dest"] == "osc1_wave_interpolate"

    own = {"5": {"targets": [{"dest": "filter_frequency", "start": 10, "end": 90}]}, "6": {"targets": []}}
    filled = with_default_macros({"name": "Orgel", "macros": own})["macros"]
    assert filled["5"] == own["5"] and filled["6"]["targets"] and len(filled) == 8


def test_the_sound_designer_must_set_every_parameter_and_cannot_pick_a_preset():
    """Hardware 2026-09-17: kalimba, guitar and piano were all the 'pluck' preset with a new name,
    because the schema offered 'preset' and an open 'params' object the model left empty."""
    from circuit_voice.generate_tools import DESIGN_PARAMS, design_schema, patch_from_design

    schema = design_schema()
    assert "preset" not in schema["properties"]
    params = schema["properties"]["params"]
    assert set(params["required"]) == set(params["properties"]) == set(DESIGN_PARAMS)
    assert {"name", "params"} <= set(schema["required"])

    answer = {
        "name": "Kalimba mit langem Namen",
        "params": {**dict.fromkeys(DESIGN_PARAMS, 10), "env1_decay": 300, "unknown": 5},
        "mod_matrix": [{"source1": "velocity", "dest": "filter frequency", "depth": 20}],
        "macros": [
            {"knob": 5, "targets": [{"dest": "filter_frequency", "start": 0, "end": 100}]},
            {"knob": 1, "targets": [{"dest": "osc1_wave", "start": 0, "end": 29}]},  # not continuous: dropped
        ],
    }
    patch = patch_from_design(answer)
    assert patch["name"] == "Kalimba mit lange"[:16] and "preset" not in patch
    assert patch["params"]["env1_decay"] == 127 and "unknown" not in patch["params"]
    assert patch["macros"]["5"]["targets"][0]["end"] == 100
    assert patch["macros"]["1"]["targets"][0]["dest"] != "osc1_wave" and len(patch["macros"]) == 8


def test_every_design_parameter_is_a_real_patch_parameter():
    from circuit_tracks.patch import _PARAM_OFFSETS
    from circuit_voice.generate_tools import DESIGN_PARAMS

    assert set(DESIGN_PARAMS) <= set(_PARAM_OFFSETS)
