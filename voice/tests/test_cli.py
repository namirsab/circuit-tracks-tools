"""Config parsing for the circuit-voice CLI (pure, no devices, no network)."""

import pytest

from circuit_voice.cli import Config, ConfigError, build_parser


def test_defaults():
    cfg = Config.from_args([], env={"GEMINI_API_KEY": "k"})
    assert cfg.mode == "ptt"
    assert cfg.ptt_key == "alt_r"
    assert cfg.silence_ms == 1200
    assert cfg.voice == "Kore"
    assert cfg.mcp == "inproc"
    assert cfg.tools == "creative"
    assert cfg.model == "gemini-3.8-live"
    assert cfg.composer_model == "gemini-3.8-flash"
    assert cfg.api_key == "k"
    assert cfg.log_path is not None
    assert cfg.max_utterance_s == 180


def test_flags_override_defaults():
    cfg = Config.from_args(
        [
            "--mode",
            "open",
            "--silence-ms",
            "1500",
            "--voice",
            "Puck",
            "--tools",
            "control",
            "--no-log",
            "--song",
            "x.json",
            "--songs-dir",
            "songs",
            "--midi-port",
            "Circuit",
            "--max-minutes",
            "30",
        ],
        env={"GEMINI_API_KEY": "k"},
    )
    assert cfg.mode == "open"
    assert cfg.silence_ms == 1500
    assert cfg.voice == "Puck"
    assert cfg.tools == "control"
    assert cfg.log_path is None
    assert cfg.song == "x.json"
    assert cfg.songs_dir == "songs"
    assert cfg.midi_port == "Circuit"
    assert cfg.max_minutes == 30


def test_missing_api_key_is_an_error_for_run():
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        Config.from_args([], env={})


def test_list_devices_needs_no_key():
    cfg = Config.from_args(["--list-devices"], env={})
    assert cfg.list_devices is True


def test_silence_ms_range():
    with pytest.raises(ConfigError):
        Config.from_args(["--silence-ms", "100"], env={"GEMINI_API_KEY": "k"})


def test_help_lists_every_spec_flag():
    text = build_parser().format_help()
    for flag in [
        "--ptt-key",
        "--mode",
        "--silence-ms",
        "--voice",
        "--output-device",
        "--mcp",
        "--song",
        "--songs-dir",
        "--composer-model",
        "--tools",
        "--midi-port",
        "--log",
        "--no-log",
        "--max-minutes",
        "--dry-run",
        "--list-devices",
        "--no-midi",
        "--quiet",
    ]:
        assert flag in text, flag
