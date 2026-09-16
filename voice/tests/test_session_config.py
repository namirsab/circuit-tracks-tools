"""LiveConnectConfig per listening mode, snapshot-tested."""

from __future__ import annotations

import json
import os
from pathlib import Path

from google.genai import types

from circuit_voice.cli import Config
from circuit_voice.prompt_de import SYSTEM_INSTRUCTION_DE
from circuit_voice.session import build_live_config

FIXTURES = Path(__file__).parent / "fixtures"
DECL = [types.FunctionDeclaration(name="set_bpm", description="x", parameters_json_schema={"type": "object"})]


def cfg(mode, **kw):
    return Config.from_args(
        ["--mode", mode, *sum(([k, str(v)] for k, v in kw.items()), [])], env={"GEMINI_API_KEY": "k"}
    )


def dump(config):
    return config.model_dump(exclude_none=True, mode="json")


def test_snapshots_per_mode():
    for mode in ("ptt", "toggle", "open"):
        current = dump(build_live_config(cfg(mode), DECL))
        path = FIXTURES / f"live_config_{mode}.json"
        if os.environ.get("UPDATE_GOLDEN"):
            path.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n")
        assert current == json.loads(path.read_text()), f"{mode}: run with UPDATE_GOLDEN=1 after an intended change"


def test_manual_modes_disable_vad_and_open_mode_uses_silence():
    ptt = build_live_config(cfg("ptt"), DECL)
    assert ptt.realtime_input_config.automatic_activity_detection.disabled is True
    assert ptt.proactivity is None
    open_ = build_live_config(cfg("open", **{"--silence-ms": 1500}), DECL)
    vad = open_.realtime_input_config.automatic_activity_detection
    assert vad.disabled is False and vad.silence_duration_ms == 1500
    assert open_.proactivity.proactive_audio is True


def test_resumption_handle_and_text_mode():
    c = build_live_config(cfg("ptt"), DECL, resumption_handle="h1")
    assert c.session_resumption.handle == "h1"
    assert c.response_modalities == [types.Modality.AUDIO]
    assert c.speech_config.voice_config.prebuilt_voice_config.voice_name == "Kore"
    t = build_live_config(cfg("ptt"), DECL, text_only=True)
    assert t.response_modalities == [types.Modality.AUDIO]  # native audio models accept nothing else
    assert t.output_audio_transcription is not None
    assert t.tools[0].function_declarations[0].name == "set_bpm"


def test_prompt_contains_the_rules():
    for needle in ["1 bis 64", "confirmed=true", "nur eine Quelle", "Cutoff", "Rückgängig", "compose"]:
        assert needle in SYSTEM_INSTRUCTION_DE, needle
    assert SYSTEM_INSTRUCTION_DE.strip().startswith("Du bist die Stimme")
