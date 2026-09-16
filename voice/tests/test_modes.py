"""Control and creative modes: tool surface, prompt, live reconnect on switch."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fakes import FakeConnector, FakeLiveSession

from circuit_voice.audio import Player
from circuit_voice.bridge import Bridge
from circuit_voice.cli import Config
from circuit_voice.listen import ListenController
from circuit_voice.mcp_client import McpBackend
from circuit_voice.prompt_de import system_instruction
from circuit_voice.session import Session, build_live_config
from circuit_voice.tool_cards import cards_for_profile


def test_profiles_and_aliases():
    control = {c.name for c in cards_for_profile("control")}
    creative = {c.name for c in cards_for_profile("creative")}
    assert control < creative
    assert {"compose", "revise", "generate_track", "design_patch"} == creative - control
    assert {"write_notes", "write_drums", "select_patch", "save_to_device", "set_agent_mode"} <= control
    assert cards_for_profile("full") == cards_for_profile("creative")
    assert cards_for_profile("perform") == cards_for_profile("control")


def test_prompt_carries_the_mode():
    assert "Steuermodus" in system_instruction("control") and "erfindest keine Musik" in system_instruction("control")
    assert "Kreativmodus" in system_instruction("creative")
    cfg = Config.from_args([], env={"GEMINI_API_KEY": "k"})
    assert cfg.tools == "control"
    text = build_live_config(cfg, [], profile="creative").system_instruction.parts[0].text
    assert "Kreativmodus" in text


@asynccontextmanager
async def harness(server, sessions):
    config = Config.from_args(["--no-log"], env={"GEMINI_API_KEY": "k"})
    async with McpBackend("inproc") as backend:
        bridge = Bridge(backend, profile="control")
        await bridge.prepare()
        connector = FakeConnector(sessions)
        session = Session(
            config,
            bridge,
            ListenController("ptt"),
            Player(stream_factory=lambda **kw: None),
            connect=connector,
            backoff=(0.01, 0.01),
        )
        yield session, bridge, connector


async def test_mode_switch_reconnects_with_new_tools(server):
    first, second = FakeLiveSession([], tail=5), FakeLiveSession([], tail=0.05)
    async with harness(server, [first, second]) as (session, bridge, connector):
        import asyncio

        async def switch_later():
            await asyncio.sleep(0.1)
            await bridge.set_profile("creative")
            await session.request_reconnect()

        asyncio.get_running_loop().create_task(switch_later())
        await asyncio.wait_for(session.run(max_connections=2), timeout=3)
    names = [[d.name for d in c.tools[0].function_declarations] for c in connector.configs]
    assert "compose" not in names[0] and "compose" in names[1]
    assert "Kreativmodus" in connector.configs[1].system_instruction.parts[0].text
