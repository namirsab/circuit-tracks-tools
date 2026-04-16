"""Tests for the MCP server tool functions.

Uses a mock MidiConnection to verify that tools send the correct MIDI
messages and return the expected results without requiring hardware.
"""

import asyncio
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Mock MIDI that records all sent messages
# ---------------------------------------------------------------------------


class MockMidi:
    """Records MIDI messages for assertion."""

    def __init__(self):
        self.messages = []
        self.is_connected = True
        self.has_input = True
        self.port_name = "MockPort"
        self._connected = True
        self._input_port_name = "MockPort"

    def connect(self, port_name):
        self.port_name = port_name
        self._connected = True

    def disconnect(self):
        self._connected = False
        self.port_name = None

    def note_on(self, channel, note, velocity=100):
        self.messages.append(("note_on", channel, note, velocity))

    def note_off(self, channel, note):
        self.messages.append(("note_off", channel, note))

    def control_change(self, channel, control, value):
        self.messages.append(("cc", channel, control, value))

    def nrpn(self, channel, msb, lsb, value):
        self.messages.append(("nrpn", channel, msb, lsb, value))

    def program_change(self, channel, program):
        self.messages.append(("pc", channel, program))

    def send_sysex(self, data):
        self.messages.append(("sysex", data))

    def send_realtime(self, msg_type):
        self.messages.append(("realtime", msg_type))

    def send_clock(self):
        self.messages.append(("clock",))

    def all_notes_off(self, channel):
        self.messages.append(("all_notes_off", channel))

    def sysex_request(self, request_data, timeout_s=3.0, match_fn=None):
        return None  # no hardware response

    @staticmethod
    def list_output_ports():
        return ["MockPort"]

    @staticmethod
    def list_input_ports():
        return ["MockPort"]

    def send(self, msg):
        self.messages.append(("raw", msg))


# ---------------------------------------------------------------------------
# Fixture: patch module globals before importing server
# ---------------------------------------------------------------------------


@pytest.fixture
def server():
    """Import server with mocked globals. Returns (module, mock_midi)."""
    mock_midi = MockMidi()

    import circuit_mcp.server as srv

    # Patch the module-level globals
    original_midi = srv._midi
    original_engine = srv._engine
    original_morph = srv._morph

    srv._midi = mock_midi
    # Re-create engine and morph with mock midi
    from circuit_tracks.morph import MorphEngine
    from circuit_tracks.sequencer import SequencerEngine

    srv._engine = SequencerEngine(mock_midi)
    srv._morph = MorphEngine(mock_midi)

    yield srv, mock_midi

    # Restore
    srv._midi = original_midi
    srv._engine = original_engine
    srv._morph = original_morph


# ---------------------------------------------------------------------------
# Connection tools
# ---------------------------------------------------------------------------


class TestConnection:
    def test_list_midi_ports(self, server):
        srv, _ = server
        with (
            patch("circuit_tracks.midi.MidiConnection.list_output_ports", return_value=["MockPort"]),
            patch("circuit_tracks.midi.MidiConnection.list_input_ports", return_value=["MockPort"]),
        ):
            result = srv.list_midi_ports()
        assert result["output_ports"] == ["MockPort"]
        assert result["input_ports"] == ["MockPort"]

    def test_connection_status(self, server):
        srv, mock_midi = server
        result = srv.connection_status()
        assert result["connected"] is True
        assert result["port_name"] == "MockPort"


# ---------------------------------------------------------------------------
# Note playing
# ---------------------------------------------------------------------------


class TestPlayNotes:
    def test_play_single_note(self, server):
        srv, mock_midi = server
        result = asyncio.get_event_loop().run_until_complete(
            srv.play_notes(channel=0, notes=[60], velocity=100, duration_ms=10)
        )
        assert "Played note 60" in result
        assert ("note_on", 0, 60, 100) in mock_midi.messages
        assert ("note_off", 0, 60) in mock_midi.messages

    def test_play_chord(self, server):
        srv, mock_midi = server
        result = asyncio.get_event_loop().run_until_complete(
            srv.play_notes(channel=0, notes=[60, 64, 67], velocity=90, duration_ms=10)
        )
        assert "Played chord" in result
        assert ("note_on", 0, 60, 90) in mock_midi.messages
        assert ("note_on", 0, 64, 90) in mock_midi.messages
        assert ("note_on", 0, 67, 90) in mock_midi.messages

    def test_play_drum(self, server):
        srv, mock_midi = server
        result = srv.play_drum(drum=1, velocity=110)
        assert "Triggered drum 1" in result
        assert ("note_on", 9, 60, 110) in mock_midi.messages

    def test_play_drum_invalid(self, server):
        srv, _ = server
        result = srv.play_drum(drum=5)
        assert "Invalid" in result


# ---------------------------------------------------------------------------
# Synth parameter setting
# ---------------------------------------------------------------------------


class TestSynthParams:
    def test_set_synth_params_cc(self, server):
        srv, mock_midi = server
        result = srv.set_synth_params(synth=1, params={"filter_frequency": 80})
        assert "filter_frequency=80" in result
        # filter_frequency is CC 74 on channel 0
        assert ("cc", 0, 74, 80) in mock_midi.messages

    def test_set_synth_params_nrpn(self, server):
        srv, mock_midi = server
        # env2_attack is an NRPN param (env1_attack is CC)
        result = srv.set_synth_params(synth=2, params={"env2_attack": 50})
        assert "env2_attack=50" in result
        nrpn_msgs = [m for m in mock_midi.messages if m[0] == "nrpn"]
        assert len(nrpn_msgs) >= 1
        assert nrpn_msgs[0][1] == 1  # channel (synth 2)

    def test_set_synth_params_unknown(self, server):
        srv, _ = server
        result = srv.set_synth_params(synth=1, params={"nonexistent_param": 50})
        assert "Unknown params" in result

    def test_set_synth_params_invalid_synth(self, server):
        srv, _ = server
        result = srv.set_synth_params(synth=3, params={"filter_frequency": 80})
        assert "Invalid" in result

    def test_set_synth_params_mixed(self, server):
        srv, mock_midi = server
        result = srv.set_synth_params(synth=1, params={"filter_frequency": 80, "bad_param": 10})
        assert "filter_frequency=80" in result
        assert "Unknown params: bad_param" in result


# ---------------------------------------------------------------------------
# Drum parameter setting
# ---------------------------------------------------------------------------


class TestDrumParams:
    def test_set_drum_params(self, server):
        srv, mock_midi = server
        result = srv.set_drum_params(drum=1, params={"pitch": 80, "decay": 60})
        assert "pitch=80" in result
        assert "decay=60" in result
        cc_msgs = [m for m in mock_midi.messages if m[0] == "cc"]
        assert len(cc_msgs) == 2
        # All drum params go on channel 9
        assert all(m[1] == 9 for m in cc_msgs)

    def test_set_drum_params_invalid_drum(self, server):
        srv, _ = server
        result = srv.set_drum_params(drum=5, params={"pitch": 80})
        assert "Invalid" in result

    def test_set_drum_params_patch_select_blocked(self, server):
        srv, _ = server
        result = srv.set_drum_params(drum=1, params={"patch_select": 5})
        assert "patch_select" in result
        assert "NCS export" in result


# ---------------------------------------------------------------------------
# Project params
# ---------------------------------------------------------------------------


class TestProjectParams:
    def test_set_project_params_cc(self, server):
        srv, mock_midi = server
        result = srv.set_project_params(params={"reverb_synth1_send": 100})
        assert "reverb_synth1_send=100" in result
        cc_msgs = [m for m in mock_midi.messages if m[0] == "cc"]
        assert len(cc_msgs) == 1
        assert cc_msgs[0][1] == 15  # project channel

    def test_set_project_params_nrpn(self, server):
        srv, mock_midi = server
        result = srv.set_project_params(params={"reverb_decay": 80})
        assert "reverb_decay=80" in result
        nrpn_msgs = [m for m in mock_midi.messages if m[0] == "nrpn"]
        assert len(nrpn_msgs) == 1
        assert nrpn_msgs[0][1] == 15  # project channel


# ---------------------------------------------------------------------------
# Patch selection and program change
# ---------------------------------------------------------------------------


class TestPatchSelect:
    def test_select_patch_synth1(self, server):
        srv, mock_midi = server
        result = srv.select_patch(synth=1, patch_number=5)
        assert "Selected patch 5" in result
        assert ("pc", 0, 5) in mock_midi.messages

    def test_select_patch_synth2(self, server):
        srv, mock_midi = server
        srv.select_patch(synth=2, patch_number=10)
        assert ("pc", 1, 10) in mock_midi.messages

    def test_select_patch_invalid_synth(self, server):
        srv, _ = server
        result = srv.select_patch(synth=3, patch_number=5)
        assert "Invalid" in result

    def test_select_patch_out_of_range(self, server):
        srv, _ = server
        result = srv.select_patch(synth=1, patch_number=65)
        assert "out of range" in result.lower() or "Invalid" in result

    def test_select_project(self, server):
        srv, mock_midi = server
        srv.select_project(project_number=3)
        assert ("pc", 15, 3) in mock_midi.messages


# ---------------------------------------------------------------------------
# Pattern / sequencer tools
# ---------------------------------------------------------------------------


class TestPatternSequencer:
    def test_set_and_get_pattern(self, server):
        srv, _ = server
        result = srv.set_pattern(
            name="test_pat",
            tracks={
                "synth1": {"steps": {"0": {"note": 60}, "4": {"note": 64}}},
                "drum1": {"steps": {"0": {}, "8": {}}},
            },
        )
        assert "test_pat" in result

        pat = srv.get_pattern("test_pat")
        assert "synth1" in pat["tracks"]
        assert "drum1" in pat["tracks"]
        # synth1 should have 2 steps with notes
        assert "0" in pat["tracks"]["synth1"]["steps"]
        assert "4" in pat["tracks"]["synth1"]["steps"]

    def test_list_patterns(self, server):
        srv, _ = server
        srv.set_pattern(name="a", tracks={"synth1": {"steps": {"0": {"note": 60}}}})
        srv.set_pattern(name="b", tracks={"synth1": {"steps": {"0": {"note": 64}}}})
        result = srv.list_patterns()
        assert "a" in result["patterns"]
        assert "b" in result["patterns"]

    def test_clear_pattern(self, server):
        srv, _ = server
        srv.set_pattern(name="to_clear", tracks={"synth1": {"steps": {"0": {"note": 60}}}})
        result = srv.clear_pattern("to_clear")
        assert "cleared" in result.lower()
        # After clearing, pattern is removed
        pat = srv.get_pattern("to_clear")
        assert "error" in pat

    def test_get_nonexistent_pattern(self, server):
        srv, _ = server
        result = srv.get_pattern("nope")
        assert "error" in result or "not found" in str(result).lower()

    def test_set_track(self, server):
        srv, _ = server
        srv.set_pattern(name="base", tracks={"synth1": {"steps": {"0": {"note": 60}}}})
        result = srv.set_track(
            pattern_name="base",
            track="drum1",
            steps={"0": {}, "4": {}, "8": {}, "12": {}},
        )
        assert "drum1" in result
        pat = srv.get_pattern("base")
        assert "0" in pat["tracks"]["drum1"]["steps"]

    def test_mute_track(self, server):
        srv, _ = server
        result = srv.mute_track(track="synth1", muted=True)
        assert "muted" in result.lower()

    def test_set_bpm(self, server):
        srv, _ = server
        result = srv.set_bpm(140.0)
        assert "140" in result


# ---------------------------------------------------------------------------
# Create synth patch
# ---------------------------------------------------------------------------


class TestCreatePatch:
    def test_create_patch_init(self, server):
        srv, mock_midi = server
        result = srv.create_synth_patch(synth=1, name="TestPatch")
        assert result["synth"] == 1
        assert result["name"] == "TestPatch"
        # Should have sent a sysex to load the patch
        sysex_msgs = [m for m in mock_midi.messages if m[0] == "sysex"]
        assert len(sysex_msgs) == 1

    def test_create_patch_with_preset(self, server):
        srv, mock_midi = server
        result = srv.create_synth_patch(synth=1, name="MyPad", preset="pad")
        assert result["preset"] == "pad"
        assert result["name"] == "MyPad"

    def test_create_patch_with_params(self, server):
        srv, mock_midi = server
        result = srv.create_synth_patch(
            synth=2,
            name="Tweaked",
            params={"filter_frequency": 100, "osc1_wave": 5},
        )
        assert result["params_set"] == 2

    def test_create_patch_with_mod_matrix(self, server):
        srv, _ = server
        result = srv.create_synth_patch(
            synth=1,
            name="Modded",
            mod_matrix=[
                {"source": "LFO 1+", "dest": "filter frequency", "depth": 20},
                {"source": "env filter", "dest": "osc 1 & 2 pitch", "depth": -10},
            ],
        )
        assert result["mod_slots"] == 2

    def test_create_patch_with_macros(self, server):
        srv, _ = server
        result = srv.create_synth_patch(
            synth=1,
            name="Macro",
            macros={
                "1": {"targets": [{"dest": "filter_frequency", "start": 0, "end": 127}]},
                "5": {"targets": [{"dest": "osc1_wave_interpolate", "start": 0, "end": 127}]},
            },
        )
        assert result["macros_set"] == 2

    def test_create_patch_invalid_synth(self, server):
        srv, _ = server
        result = srv.create_synth_patch(synth=3, name="Bad")
        assert "error" in result


# ---------------------------------------------------------------------------
# Macro tools
# ---------------------------------------------------------------------------


class TestMacros:
    def test_set_macro(self, server):
        srv, mock_midi = server
        srv.set_macro(synth=1, macro=1, value=100)
        cc_msgs = [m for m in mock_midi.messages if m[0] == "cc"]
        assert len(cc_msgs) > 0  # macro sends CC messages for target params

    def test_set_macro_invalid(self, server):
        srv, _ = server
        result = srv.set_macro(synth=1, macro=9, value=50)
        assert "Invalid" in result or "error" in result.lower()

    def test_get_macros(self, server):
        srv, _ = server
        result = srv.get_macros()
        # Returns dict keyed by macro number (1-8)
        assert "1" in result
        assert len(result) == 8


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class TestTransport:
    def test_transport_start(self, server):
        srv, mock_midi = server
        srv.transport(action="start", bpm=120)
        assert ("realtime", "start") in mock_midi.messages

    def test_transport_stop(self, server):
        srv, mock_midi = server
        srv.transport(action="stop")
        assert ("realtime", "stop") in mock_midi.messages

    def test_transport_continue(self, server):
        srv, mock_midi = server
        srv.transport(action="continue", bpm=120)
        assert ("realtime", "continue") in mock_midi.messages


# ---------------------------------------------------------------------------
# Morph tools
# ---------------------------------------------------------------------------


class TestMorph:
    def test_morph_synth_params(self, server):
        srv, mock_midi = server
        result = srv.morph_synth_params(
            synth=1,
            start={"filter_frequency": 20},
            target={"filter_frequency": 100},
            duration_bars=0.1,
            name="test_morph",
        )
        assert "s1_test_morph" in result
        # Start values should be sent immediately
        assert ("cc", 0, 74, 20) in mock_midi.messages

    def test_morph_synth_invalid_synth(self, server):
        srv, _ = server
        result = srv.morph_synth_params(synth=3, start={"filter_frequency": 20}, target={"filter_frequency": 100})
        assert "Invalid" in result

    def test_morph_unknown_param(self, server):
        srv, _ = server
        result = srv.morph_synth_params(synth=1, start={"bad_param": 0}, target={"bad_param": 100})
        assert "Unknown" in result

    def test_morph_mismatched_keys(self, server):
        srv, _ = server
        result = srv.morph_synth_params(
            synth=1,
            start={"filter_frequency": 20},
            target={"filter_resonance": 100},
        )
        assert "same parameter names" in result

    def test_stop_morph_by_name(self, server):
        srv, _ = server
        srv.morph_synth_params(
            synth=1,
            start={"filter_frequency": 20},
            target={"filter_frequency": 100},
            duration_bars=100,
            name="long_morph",
        )
        result = srv.stop_morph(name="long_morph")
        assert "Stopped" in result

    def test_stop_morph_all(self, server):
        srv, _ = server
        srv.morph_synth_params(
            synth=1,
            start={"filter_frequency": 20},
            target={"filter_frequency": 100},
            duration_bars=100,
            name="m1",
        )
        srv.morph_synth_params(
            synth=2,
            start={"filter_frequency": 30},
            target={"filter_frequency": 90},
            duration_bars=100,
            name="m2",
        )
        result = srv.stop_morph()
        assert "Stopped all 2" in result

    def test_stop_morph_not_found(self, server):
        srv, _ = server
        result = srv.stop_morph(name="nonexistent")
        assert "not found" in result.lower() or "No morph" in result

    def test_morph_drum_params(self, server):
        srv, mock_midi = server
        result = srv.morph_drum_params(
            drum=1,
            start={"pitch": 40},
            target={"pitch": 100},
            duration_bars=0.1,
            name="drum_test",
        )
        assert "d1_drum_test" in result

    def test_morph_project_params(self, server):
        srv, mock_midi = server
        result = srv.morph_project_params(
            start={"reverb_synth1_send": 0},
            target={"reverb_synth1_send": 100},
            duration_bars=0.1,
            name="proj_test",
        )
        assert "proj_proj_test" in result


# ---------------------------------------------------------------------------
# Parameter reference
# ---------------------------------------------------------------------------


class TestParameterReference:
    def test_get_parameter_reference_default(self, server):
        srv, _ = server
        result = srv.get_parameter_reference()
        assert "available_sections" in result
        assert "best_practices" in result
        assert "synth" in result["available_sections"]
        assert "song_format" in result["available_sections"]

    def test_get_parameter_reference_synth(self, server):
        srv, _ = server
        result = srv.get_parameter_reference("synth")
        assert result["section"] == "synth"
        assert "synth_cc_params" in result
        assert "channels" in result

    def test_get_parameter_reference_patch(self, server):
        srv, _ = server
        result = srv.get_parameter_reference("patch")
        assert result["section"] == "patch"
        assert "patch_parameters" in result
        assert "presets" in result

    def test_get_parameter_reference_drums(self, server):
        srv, _ = server
        result = srv.get_parameter_reference("drums")
        assert result["section"] == "drums"
        assert "drum_params" in result

    def test_get_parameter_reference_mod_matrix(self, server):
        srv, _ = server
        result = srv.get_parameter_reference("mod_matrix")
        assert result["section"] == "mod_matrix"
        assert "sources" in result
        assert "destinations" in result

    def test_get_parameter_reference_macros(self, server):
        srv, _ = server
        result = srv.get_parameter_reference("macros")
        assert result["section"] == "macros"
        assert "macro_destinations" in result
        assert "standard_layout" in result

    def test_get_parameter_reference_best_practices(self, server):
        srv, _ = server
        result = srv.get_parameter_reference("best_practices")
        assert result["section"] == "best_practices"
        assert "pattern_length" in result
        assert "macros_add_to_base" in result


# ---------------------------------------------------------------------------
# Sequencer status
# ---------------------------------------------------------------------------


class TestSequencerStatus:
    def test_get_sequencer_status(self, server):
        srv, _ = server
        result = srv.get_sequencer_status()
        assert "running" in result
        assert result["running"] is False
