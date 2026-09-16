"""Terminal rendering, events log, dry run and greeting."""

from __future__ import annotations

import json

from fakes import FakeConnector, FakeLiveSession
from google.genai import types

from circuit_voice import app
from circuit_voice.cli import Config
from circuit_voice.events import EventLog
from circuit_voice.ui import IDLE, LISTENING, OPEN, UIState, render


def test_render_banner_and_lines():
    st = UIState(mode="ptt")
    assert render({"type": "listen_start"}, st) == LISTENING
    assert render({"type": "listen_stop"}, st) == IDLE
    assert LISTENING != IDLE
    assert render({"type": "listen_start"}, UIState(mode="open")) == OPEN
    assert render({"type": "tool_call", "name": "set_bpm", "args": {"bpm": 100}, "latency": 0.5}, st).startswith(
        "→ set_bpm"
    )
    assert "✗" in render({"type": "tool_result", "response": {"error": "nein"}}, st)
    assert render({"type": "audio", "samples": 10}, st) is None
    assert render({"type": "input_transcript", "text": "Stopp"}, st) == "Du:    Stopp"


def test_event_log_schema_and_redaction(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path, redact=["sk-SECRET"])
    log.write({"type": "error", "t": 1.0, "message": "key sk-SECRET rejected"})
    log.write({"type": "tool_call", "t": 2.0, "name": "set_bpm", "args": {"bpm": 1}})
    log.close()
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert len(lines) == 2
    for line in lines:
        assert {"ts", "type", "t"} <= set(line)
    assert "sk-SECRET" not in path.read_text()
    assert EventLog(None).write({"type": "x"}) is not None


async def test_dry_run_prints_tool_calls(server, capsys):
    config = Config.from_args(["--dry-run", "Stopp", "--no-midi", "--no-log"], env={"GEMINI_API_KEY": "k"})
    fake = FakeLiveSession(
        [
            types.LiveServerMessage(
                tool_call=types.LiveServerToolCall(
                    function_calls=[types.FunctionCall(id="1", name="stop_sequencer", args={})]
                )
            ),
            types.LiveServerMessage(
                server_content=types.LiveServerContent(
                    model_turn=types.Content(parts=[types.Part(text="Gestoppt.")]), turn_complete=True
                )
            ),
        ]
    )
    connector = FakeConnector([fake])
    code = await app.dry_run(config, "Stopp", connect=connector)
    out = capsys.readouterr().out
    assert code == 0
    assert "→ stop_sequencer" in out and "Agent: Gestoppt." in out
    assert connector.configs[0].response_modalities == [types.Modality.AUDIO]
    assert fake.client_content[0]["turn_complete"] is True


async def test_greeting_sent_once_per_session(server):
    from circuit_voice.audio import Player
    from circuit_voice.bridge import Bridge
    from circuit_voice.listen import ListenController
    from circuit_voice.mcp_client import McpBackend
    from circuit_voice.session import Session

    config = Config.from_args(["--no-log"], env={"GEMINI_API_KEY": "k"})
    first, second = FakeLiveSession([], tail=0.05), FakeLiveSession([], tail=0.05)
    async with McpBackend("inproc") as backend:
        bridge = Bridge(backend)
        await bridge.prepare()
        session = Session(
            config,
            bridge,
            ListenController("ptt"),
            Player(stream_factory=lambda **kw: None),
            connect=FakeConnector([first, second]),
            greeting="Bereit.",
            backoff=(0.01, 0.01),
        )
        await session.run(max_connections=2)
    assert len(first.client_content) == 1 and "Bereit." in first.client_content[0]["turns"].parts[0].text
    assert second.client_content == []
