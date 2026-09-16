"""Assemble the parts and run: the voice loop, or one text turn for --dry-run."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from typing import Any

from .audio import MicStream, Player
from .bridge import Bridge
from .cli import Config
from .compose_tools import register_compose_tools
from .composer import Composer, GeminiComposer
from .device_tools import register_device_tools
from .edit_tools import register_edit_tools
from .events import EventLog
from .generate_tools import register_generate_tools
from .listen import ListenController
from .mcp_client import McpBackend, MidiNotFound
from .patch_tools import PatchCatalog, register_patch_tools
from .prompt_de import GREETING_DE, GREETING_OPEN_DE, GREETING_TOGGLE_DE
from .session import Session, run_text_turn
from .songs import SongLibrary, register_song_tools
from .ui import TerminalUI


def _connect_factory(config: Config) -> Callable[..., Any]:
    from google import genai  # noqa: PLC0415
    from google.genai import types  # noqa: PLC0415

    # proactive audio and affective dialog live on v1beta
    client = genai.Client(api_key=config.api_key, http_options=types.HttpOptions(api_version="v1beta"))
    model = config.model

    def connect(*, config):
        return client.aio.live.connect(model=model, config=config)

    return connect


async def build_bridge(config: Config, backend: McpBackend) -> Bridge:
    bridge = Bridge(backend, profile=config.tools)
    await bridge.prepare()
    register_song_tools(bridge, SongLibrary(config.songs_dir))
    register_device_tools(bridge)
    register_edit_tools(bridge)
    register_patch_tools(bridge, PatchCatalog(config.patch_cache))
    if "compose" in bridge.cards:
        model = GeminiComposer(config.api_key or "", config.composer_model)
        composer = Composer(model, backend=backend)
        await composer.prepare()
        register_compose_tools(bridge, composer)
        register_generate_tools(bridge, model)
        await bridge.generate_prepare()  # type: ignore[attr-defined]
    if config.song:
        data = SongLibrary.load(config.song)
        text = await bridge.load_song_dict(data, config.song)  # type: ignore[attr-defined]
        print(text, file=sys.stderr)
    return bridge


async def run_agent(config: Config, connect: Callable[..., Any] | None = None) -> int:
    log = EventLog(config.log_path, redact=[config.api_key or ""])
    ui = TerminalUI(config.mode, quiet=config.quiet)

    def on_event(event: dict[str, Any]) -> None:
        log.write(event)
        ui.handle(event)

    async with McpBackend(config.mcp) as backend:
        if not config.no_midi and backend.kind == "inproc":
            try:
                port = await backend.connect_midi(config.midi_port)
                on_event({"type": "hint", "text": f"MIDI verbunden: {port}"})
            except MidiNotFound as e:
                on_event({"type": "hint", "text": str(e)})
                return 3
        bridge = await build_bridge(config, backend)
        listen = ListenController(config.mode, key=config.ptt_key, max_utterance_s=config.max_utterance_s)
        if config.key_source == "terminal" and listen.mode == "ptt":
            listen.mode = "toggle"  # a terminal cannot report key releases
        player = Player(device=config.output_device)
        player.start()
        # Half-duplex in open mode: the mic must not hear the agent's own voice,
        # otherwise the model answers itself in a loop (seen on 2026-09-16).
        mic = MicStream(
            device=config.input_device,
            gate=lambda: listen.forwarding and not (config.mode == "open" and player.speaking()),
        )
        mic.start()
        session = Session(
            config,
            bridge,
            listen,
            player,
            connect=connect or _connect_factory(config),
            mic=mic,
            on_event=on_event,
            greeting={"open": GREETING_OPEN_DE, "toggle": GREETING_TOGGLE_DE}.get(listen.mode, GREETING_DE),
        )

        async def set_listening(args: dict[str, Any]) -> str:
            mode = args["mode"]
            listen.set_mode(mode)
            ui.state.mode = mode
            names = {"ptt": "Taste halten", "toggle": "Taste drücken zum Starten und Stoppen", "open": "immer zuhören"}
            warn = " Am besten mit Kopfhörern, sonst höre ich die Musik mit." if mode == "open" else ""
            return f"Ich höre jetzt so zu: {names[mode]}.{warn}"

        bridge.register_virtual("set_listening", set_listening)
        hint = listen.start_terminal() if config.key_source == "terminal" else listen.start()
        if hint:
            on_event({"type": "hint", "text": hint})
        ui.state.mode = listen.mode
        try:
            await session.run()
        finally:
            listen.stop()
            mic.stop()
            player.stop()
            log.close()
    return 0


async def dry_run(config: Config, text: str, connect: Callable[..., Any] | None = None) -> int:
    async with McpBackend(config.mcp) as backend:
        if not config.no_midi and backend.kind == "inproc":
            try:
                await backend.connect_midi(config.midi_port)
            except MidiNotFound as e:
                print(str(e), file=sys.stderr)
        bridge = await build_bridge(config, backend)
        result = await run_text_turn(config, bridge, connect or _connect_factory(config), text)
    for call in result["tool_calls"]:
        print(f"→ {call['name']} {json.dumps(call['args'], ensure_ascii=False)}")
        print(f"   {json.dumps(call['response'], ensure_ascii=False)[:300]}")
    print(f"Agent: {result['reply']}")
    return 0


def main_async(config: Config) -> int:
    if config.dry_run is not None:
        return asyncio.run(dry_run(config, config.dry_run))
    return asyncio.run(run_agent(config))
