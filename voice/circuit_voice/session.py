"""The Gemini Live session: configuration, the audio and tool loops, reconnects.

``build_live_config`` is pure and snapshot-tested. ``Session.run`` connects
through an injectable ``connect`` factory (``client.aio.live.connect`` in
production, a scripted fake in tests) and runs two loops per connection: the
send loop forwards microphone frames while the listen controller says so and
sends manual activity signals, the receive loop plays model audio, flushes on
interruption, routes tool calls through the bridge (BLOCKING ones awaited,
NON_BLOCKING ones as tasks that answer with ``scheduling=INTERRUPT``), stores
resumption handles and reconnects after ``go_away`` or a dropped socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from google.genai import types

from .audio import Player
from .bridge import Bridge
from .cli import Config
from .listen import ListenController
from .prompt_de import GOODBYE_DE, SYSTEM_INSTRUCTION_DE

log = logging.getLogger("circuit_voice.session")

COMPRESSION_TRIGGER_TOKENS = 100_000
AUDIO_MIME = "audio/pcm;rate=16000"


def build_live_config(
    config: Config,
    declarations: list[types.FunctionDeclaration],
    resumption_handle: str | None = None,
    text_only: bool = False,
) -> types.LiveConnectConfig:
    """Live config for the configured listening mode (see the spec, Listening modes)."""
    if config.mode == "open":
        realtime = types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                disabled=False,
                start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                prefix_padding_ms=100,
                silence_duration_ms=config.silence_ms,
            )
        )
        proactivity = types.ProactivityConfig(proactive_audio=True)
    else:
        realtime = types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)
        )
        proactivity = None
    return types.LiveConnectConfig(
        response_modalities=[types.Modality.TEXT if text_only else types.Modality.AUDIO],
        system_instruction=types.Content(parts=[types.Part(text=SYSTEM_INSTRUCTION_DE)], role="user"),
        speech_config=None
        if text_only
        else types.SpeechConfig(
            voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=config.voice))
        ),
        tools=[types.Tool(function_declarations=declarations)] if declarations else None,
        realtime_input_config=realtime,
        proactivity=proactivity,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=None if text_only else types.AudioTranscriptionConfig(),
        context_window_compression=types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow(), trigger_tokens=COMPRESSION_TRIGGER_TOKENS
        ),
        session_resumption=types.SessionResumptionConfig(handle=resumption_handle),
    )


class Session:
    def __init__(
        self,
        config: Config,
        bridge: Bridge,
        listen: ListenController,
        player: Player,
        connect: Callable[..., Any],
        mic: AsyncIterator[bytes] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        backoff: tuple[float, float] = (1.0, 10.0),
        clock: Callable[[], float] = time.monotonic,
        greeting: str | None = None,
    ):
        self.greeting = greeting
        self._greeted = False
        self.config = config
        self.bridge = bridge
        self.listen = listen
        self.player = player
        self.connect = connect
        self.mic = mic
        self._on_event = on_event or (lambda _e: None)
        self._backoff = backoff
        self._clock = clock
        self.resumption_handle: str | None = None
        self.deadline_s: float | None = config.max_minutes * 60 if config.max_minutes else None
        self._started = clock()
        self._stop = False
        self._live: Any = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._listen_stop_at: float | None = None
        listen._on_event = self._listen_event  # events arrive from the hotkey thread

    # --- events ---

    def emit(self, type_: str, **data: Any) -> None:
        self._on_event({"type": type_, "t": self._clock(), **data})

    def _listen_event(self, name: str) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._handle_listen_event, name)

    def _handle_listen_event(self, name: str) -> None:
        self.emit(name)
        if name == "listen_stop":
            self._listen_stop_at = self._clock()
        self.player.play_earcon(name)
        live = self._live
        if live is not None and self.config.mode != "open":
            signal = (
                {"activity_start": types.ActivityStart()}
                if name == "listen_start"
                else {"activity_end": types.ActivityEnd()}
            )
            self._loop.create_task(live.send_realtime_input(**signal))

    # --- lifecycle ---

    def stop(self) -> None:
        self._stop = True

    def _time_left(self) -> float | None:
        if self.deadline_s is None:
            return None
        return self.deadline_s - (self._clock() - self._started)

    async def run(self, max_connections: int | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        connections = 0
        delay = self._backoff[0]
        while not self._stop:
            if max_connections is not None and connections >= max_connections:
                break
            left = self._time_left()
            if left is not None and left <= 0:
                break
            connections += 1
            try:
                await self._run_once()
                delay = self._backoff[0]
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - reconnect, never die on a network blip
                log.warning("live session ended with %s: %s", type(e).__name__, e)
                self.emit("error", message=f"{type(e).__name__}: {e}")
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._backoff[1])
            if self._stop:
                break
            left = self._time_left()
            if left is not None and left <= 0:
                break
            if max_connections is None or connections < max_connections:
                self.emit("reconnect", handle=self.resumption_handle)
        if self._time_left() is not None and self._time_left() <= 0:
            self.emit("goodbye", text=GOODBYE_DE)

    async def _run_once(self) -> None:
        config = build_live_config(self.config, self.bridge.declarations(), self.resumption_handle)
        async with self.connect(config=config) as live:
            self._live = live
            self.emit("connected", handle=self.resumption_handle)
            if self.greeting and not self._greeted:
                self._greeted = True
                prompt = f"(Systemhinweis: Die Sitzung beginnt. Sag der Person kurz auf Deutsch: {self.greeting})"
                await live.send_client_content(
                    turns=types.Content(role="user", parts=[types.Part(text=prompt)]), turn_complete=True
                )
            send_task = asyncio.ensure_future(self._send_loop(live))
            tick_task = asyncio.ensure_future(self._tick_loop())
            try:
                await self._receive_loop(live)
            finally:
                self._live = None
                for t in (send_task, tick_task, *self._tasks.values()):
                    t.cancel()
                for t in (send_task, tick_task, *self._tasks.values()):
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await t
                self._tasks.clear()

    async def _tick_loop(self) -> None:
        while True:
            self.listen.tick()
            left = self._time_left()
            if left is not None and left <= 0:
                self._stop = True
                if self._live is not None:
                    with contextlib.suppress(Exception):
                        await self._live.close()
                return
            await asyncio.sleep(0.25)

    async def _send_loop(self, live: Any) -> None:
        if self.mic is None:
            return
        async for frame in self.mic:
            if self.listen.forwarding:
                await live.send_realtime_input(audio=types.Blob(data=frame, mime_type=AUDIO_MIME))

    async def _receive_loop(self, live: Any) -> None:
        async for msg in live.receive():
            update = msg.session_resumption_update
            if update is not None and update.resumable and update.new_handle:
                self.resumption_handle = update.new_handle
            if msg.go_away is not None:
                self.emit("go_away", time_left=str(msg.go_away.time_left))
            if msg.server_content is not None:
                self._handle_content(msg.server_content)
            if msg.tool_call is not None:
                for fc in msg.tool_call.function_calls or []:
                    await self._handle_tool_call(live, fc)
            if msg.tool_call_cancellation is not None:
                for cid in msg.tool_call_cancellation.ids or []:
                    task = self._tasks.pop(cid, None)
                    if task is not None:
                        task.cancel()
                        self.emit("tool_cancelled", id=cid)

    def _handle_content(self, sc: types.LiveServerContent) -> None:
        if sc.interrupted:
            self.player.flush()
            self.emit("interrupted")
        if sc.input_transcription is not None and sc.input_transcription.text:
            self.emit("input_transcript", text=sc.input_transcription.text)
        if sc.output_transcription is not None and sc.output_transcription.text:
            self.emit("output_transcript", text=sc.output_transcription.text)
        if sc.model_turn is not None:
            for part in sc.model_turn.parts or []:
                if part.inline_data is not None and part.inline_data.data:
                    self.player.play(part.inline_data.data)
                    self.emit("audio", samples=len(part.inline_data.data) // 2)
        if sc.turn_complete:
            self.emit("turn_complete")

    async def _handle_tool_call(self, live: Any, fc: types.FunctionCall) -> None:
        latency = None if self._listen_stop_at is None else self._clock() - self._listen_stop_at
        self.emit("tool_call", id=fc.id, name=fc.name, args=fc.args or {}, latency=latency)
        if self.bridge.is_blocking(fc.name):
            out = await self.bridge.dispatch(fc.name, fc.args)
            self.emit("tool_result", id=fc.id, name=fc.name, response=out.response)
            self.player.play_earcon("error" if "error" in out.response else "success")
            await live.send_tool_response(
                function_responses=[types.FunctionResponse(id=fc.id, name=fc.name, response=out.response)]
            )
            return
        self._tasks[fc.id] = asyncio.ensure_future(self._run_background_tool(live, fc))

    async def _run_background_tool(self, live: Any, fc: types.FunctionCall) -> None:
        out = await self.bridge.dispatch(fc.name, fc.args)
        self._tasks.pop(fc.id, None)
        self.emit("tool_result", id=fc.id, name=fc.name, response=out.response)
        await live.send_tool_response(
            function_responses=[
                types.FunctionResponse(
                    id=fc.id,
                    name=fc.name,
                    response=out.response,
                    scheduling=types.FunctionResponseScheduling.INTERRUPT,
                )
            ]
        )


async def run_text_turn(
    config: Config, bridge: Bridge, connect: Callable[..., Any], text: str, timeout: float = 60.0
) -> dict[str, Any]:
    """Send one text turn to a TEXT-modality session; returns tool calls and the reply (``--dry-run``)."""
    live_config = build_live_config(config, bridge.declarations(), text_only=True)
    calls: list[dict[str, Any]] = []
    reply: list[str] = []

    async def turn() -> None:
        async with connect(config=live_config) as live:
            await live.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=text)]), turn_complete=True
            )
            async for msg in live.receive():
                if msg.tool_call is not None:
                    for fc in msg.tool_call.function_calls or []:
                        out = await bridge.dispatch(fc.name, fc.args)
                        calls.append({"name": fc.name, "args": fc.args or {}, "response": out.response})
                        await live.send_tool_response(
                            function_responses=[types.FunctionResponse(id=fc.id, name=fc.name, response=out.response)]
                        )
                sc = msg.server_content
                if sc is not None and sc.model_turn is not None:
                    reply.extend(p.text for p in sc.model_turn.parts or [] if p.text)
                if sc is not None and sc.turn_complete and not [t for t in (msg.tool_call,) if t]:
                    if calls and not reply:
                        continue  # the model will speak after the tool result
                    break

    await asyncio.wait_for(turn(), timeout)
    return {"tool_calls": calls, "reply": "".join(reply)}
