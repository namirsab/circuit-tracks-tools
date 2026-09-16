"""Session loop against a scripted fake Live session: tool routing, interruption, resumption."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fakes import FakeConnector, FakeLiveSession
from google.genai import types

from circuit_voice.audio import Player
from circuit_voice.bridge import Bridge
from circuit_voice.cli import Config
from circuit_voice.listen import ListenController
from circuit_voice.mcp_client import McpBackend
from circuit_voice.session import Session


def tool_call(cid, name, args):
    return types.LiveServerMessage(
        tool_call=types.LiveServerToolCall(function_calls=[types.FunctionCall(id=cid, name=name, args=args)])
    )


def content(**kw):
    return types.LiveServerMessage(server_content=types.LiveServerContent(**kw))


@asynccontextmanager
async def harness(server, sessions, mode="ptt", mic=None, max_minutes=None):
    argv = ["--mode", mode, "--no-log"] + (["--max-minutes", str(max_minutes)] if max_minutes else [])
    config = Config.from_args(argv, env={"GEMINI_API_KEY": "k"})
    async with McpBackend("inproc") as backend:
        bridge = Bridge(backend, profile="full")
        await bridge.prepare()
        events = []
        listen = ListenController(mode, key="alt_r", max_utterance_s=5)
        player = Player(stream_factory=lambda **kw: None)
        connector = FakeConnector(sessions)
        session = Session(
            config, bridge, listen, player, connect=connector, mic=mic, on_event=events.append, backoff=(0.01, 0.01)
        )
        yield session, connector, events, server[1]


async def test_blocking_tool_call_round_trip(server):
    fake = FakeLiveSession([tool_call("c1", "set_bpm", {"bpm": 100})])
    async with harness(server, [fake]) as (session, connector, events, midi):
        await session.run(max_connections=1)
    assert len(fake.tool_responses) == 1
    fr = fake.tool_responses[0]
    assert fr.id == "c1" and fr.name == "set_bpm" and "100" in fr.response["result"]
    assert fr.scheduling is None
    assert any(e["type"] == "tool_call" and e["name"] == "set_bpm" for e in events)


async def test_go_away_reconnects_with_resumption_handle(server):
    first = FakeLiveSession(
        [
            types.LiveServerMessage(
                session_resumption_update=types.LiveServerSessionResumptionUpdate(new_handle="h1", resumable=True)
            ),
            types.LiveServerMessage(go_away=types.LiveServerGoAway(time_left="5s")),
        ]
    )
    second = FakeLiveSession([tool_call("c2", "set_bpm", {"bpm": 90})])
    async with harness(server, [first, second]) as (session, connector, events, midi):
        session.bridge.ledger.song_name = "Keep me"
        await session.run(max_connections=2)
        assert session.bridge.ledger.song_name == "Keep me"
    assert connector.configs[0].session_resumption.handle is None
    assert connector.configs[1].session_resumption.handle == "h1"
    assert second.tool_responses[0].id == "c2"
    assert any(e["type"] == "reconnect" for e in events)


async def test_interrupted_flushes_player_and_audio_is_played(server):
    audio = types.LiveServerMessage(
        server_content=types.LiveServerContent(
            model_turn=types.Content(
                parts=[types.Part(inline_data=types.Blob(data=b"\x01\x00" * 480, mime_type="audio/pcm"))]
            )
        )
    )
    fake = FakeLiveSession(
        [audio, content(interrupted=True), content(output_transcription=types.Transcription(text="Hallo"))]
    )
    async with harness(server, [fake]) as (session, connector, events, midi):
        played = []
        session.player.play = lambda pcm: played.append(len(pcm))  # type: ignore[method-assign]
        flushed = []
        session.player.flush = lambda: flushed.append(True)  # type: ignore[method-assign]
        await session.run(max_connections=1)
    assert played == [960]
    assert flushed == [True]
    assert any(e["type"] == "output_transcript" and e["text"] == "Hallo" for e in events)


async def test_non_blocking_tool_interrupts_and_cancellation_prevents_response(server):
    fake = FakeLiveSession(
        [
            tool_call("slow1", "get_synth_patch", {"synth": 1}),
            tool_call("slow2", "get_synth_patch", {"synth": 2}),
            types.LiveServerMessage(tool_call_cancellation=types.LiveServerToolCallCancellation(ids=["slow2"])),
        ],
        tail=0.4,
    )
    async with harness(server, [fake]) as (session, connector, events, midi):
        real_dispatch = session.bridge.dispatch

        async def slow_dispatch(name, args):
            await asyncio.sleep(0.15)
            return await real_dispatch(name, args)

        session.bridge.dispatch = slow_dispatch  # type: ignore[method-assign]
        await session.run(max_connections=1)
    ids = [fr.id for fr in fake.tool_responses]
    assert ids == ["slow1"]
    assert fake.tool_responses[0].scheduling == types.FunctionResponseScheduling.INTERRUPT


async def test_mic_frames_only_forwarded_while_listening(server):
    async def mic():
        for _ in range(3):
            yield b"\x00" * 640
            await asyncio.sleep(0.01)
        await asyncio.sleep(10)

    fake = FakeLiveSession([], tail=0.2)
    async with harness(server, [fake], mic=mic()) as (session, connector, events, midi):

        async def press_later():
            await asyncio.sleep(0.005)
            session.listen.key_down("alt_r")

        asyncio.get_running_loop().create_task(press_later())
        await session.run(max_connections=1)
    kinds = [("audio" if "audio" in r else next(iter(r))) for r in fake.realtime]
    assert "activity_start" in kinds
    assert kinds.count("audio") in (2, 3)
    assert kinds.index("activity_start") < kinds.index("audio")
    assert any(e["type"] == "listen_start" for e in events)


async def test_max_minutes_ends_the_session(server):
    fake = FakeLiveSession([], tail=5)
    async with harness(server, [fake], max_minutes=1) as (session, connector, events, midi):
        session.deadline_s = 0.05
        await asyncio.wait_for(session.run(max_connections=3), timeout=2)
    assert events[-1]["type"] == "goodbye"
