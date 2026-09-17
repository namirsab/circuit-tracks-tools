"""Bridge between the Live model's function calls and the MCP backend.

``prepare`` fetches the server's input schemas for every card in the active
profile and builds Gemini ``FunctionDeclaration`` objects (full JSON Schema via
``parameters_json_schema``). ``dispatch`` runs one call: normalise arguments,
route to a virtual handler or the MCP tool, and wrap the outcome in the
``response`` dict of a ``FunctionResponse``: ``{"result": ...}`` or
``{"error": "<German sentence>"}``.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from google.genai import types

from .ledger import Ledger
from .mcp_client import McpBackend
from .tool_cards import ArgumentError, ToolCard, cards_for_profile, normalise_profile

log = logging.getLogger("circuit_voice.bridge")

REPEAT_WINDOW_S = 30.0  # identical calls without progress inside this window count as a loop

VirtualHandler = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class Dispatch:
    name: str
    response: dict[str, Any]
    blocking: bool


def strip_titles(schema: Any) -> Any:
    """Drop pydantic's ``title`` keys; they only cost tokens."""
    if isinstance(schema, dict):
        return {k: strip_titles(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [strip_titles(v) for v in schema]
    return schema


class Bridge:
    def __init__(self, backend: McpBackend, profile: str = "full", ledger: Ledger | None = None):
        self.backend = backend
        self.ledger = ledger if ledger is not None else Ledger()
        self.profile = normalise_profile(profile)
        self.cards: dict[str, ToolCard] = {c.name: c for c in cards_for_profile(self.profile)}
        self._virtual: dict[str, VirtualHandler] = {}
        self._internal: dict[str, VirtualHandler] = {}  # undo handlers not exposed as tools
        self._declarations: list[types.FunctionDeclaration] = []
        self._last_key: tuple[str, str] | None = None
        self._last_at = 0.0
        self._last_stuck = False
        self._repeats = 0
        if "get_state" in self.cards:
            self._virtual["get_state"] = self._get_state
        if "undo_last" in self.cards:
            self._virtual["undo_last"] = self._undo_last

    async def _get_state(self, args: dict[str, Any]) -> str:
        status = None
        if self.ledger.source == "internal":
            result = await self.backend.call("get_sequencer_status", {})
            if not result.is_error and isinstance(result.data, dict):
                status = result.data
        return self.ledger.summary_de(status)

    async def _undo_last(self, args: dict[str, Any]) -> str:
        change = self.ledger.undo()
        if change is None:
            return "Es gibt nichts, was ich rückgängig machen kann."
        if change.revert is None:
            return f"Die letzte Änderung ({change.text_de}) kann ich nicht rückgängig machen."
        handler = self._virtual.get(change.tool) or self._internal.get(change.tool)
        if handler is not None:
            out = await handler(dict(change.revert))
            return f"Rückgängig: {out}" if isinstance(out, str) else f"Rückgängig: {change.text_de}."
        result = await self.backend.call(change.tool, change.revert)
        if result.is_error:
            return f"Rückgängig fehlgeschlagen: {result.text}"
        return f"Rückgängig: {change.text_de}."

    async def prepare(self) -> None:
        decls = []
        for c in self.cards.values():
            if c.schema is not None:
                schema = c.schema
            elif c.mcp_tool is not None:
                schema = strip_titles(await self.backend.input_schema(c.mcp_tool))
            else:
                schema = {"type": "object", "properties": {}}
            decls.append(
                types.FunctionDeclaration(
                    name=c.name,
                    description=c.description,
                    parameters_json_schema=schema,
                    behavior=types.Behavior.BLOCKING if c.blocking else types.Behavior.NON_BLOCKING,
                )
            )
        self._declarations = decls

    async def set_profile(self, profile: str) -> None:
        """Switch the tool surface (control/creative); handlers stay registered, declarations are rebuilt."""
        self.profile = normalise_profile(profile)
        self.cards = {c.name: c for c in cards_for_profile(self.profile)}
        await self.prepare()

    def declarations(self) -> list[types.FunctionDeclaration]:
        return list(self._declarations)

    def is_blocking(self, name: str) -> bool:
        c = self.cards.get(name)
        return True if c is None else c.blocking

    def register_internal(self, name: str, handler: VirtualHandler) -> None:
        """A handler only ``undo_last`` can reach (e.g. restoring a track's steps)."""
        self._internal[name] = handler

    def register_virtual(self, name: str, handler: VirtualHandler) -> None:
        self._virtual[name] = handler  # cards decide what the model may call; handlers may outlive a profile

    async def dispatch(self, name: str, args: dict[str, Any] | None) -> Dispatch:
        """Run one tool call, and stop a model that repeats a call which made no progress.

        Seen on hardware: the same call came about 30 times in 20 seconds because the
        answer (a question to ask the user) never changed.
        """
        key = (name, json.dumps(args or {}, sort_keys=True, default=str))
        now = time.monotonic()
        if key == self._last_key and self._last_stuck and now - self._last_at < REPEAT_WINDOW_S:
            self._repeats += 1
        else:
            self._repeats = 0
        self._last_key, self._last_at = key, now
        if self._repeats >= 2:
            log.warning("stopped a repeated call without progress: %s", name)
            return Dispatch(
                name,
                {
                    "error": "Same call, same answer. Stop calling tools now; tell the user in one German sentence "
                    "what is open (for a question: ask it once and wait; after a yes pass confirmed=true)."
                },
                True,
            )
        out = await self._dispatch(name, args)
        result = out.response.get("result")
        self._last_stuck = "error" in out.response or (isinstance(result, dict) and "needs_confirmation" in result)
        return out

    async def _dispatch(self, name: str, args: dict[str, Any] | None) -> Dispatch:
        spoken = dict(args or {})
        args = dict(spoken)
        c = self.cards.get(name)
        if c is None:
            log.warning("model called a tool outside the profile: %s", name)
            return Dispatch(name, {"error": f"Das Werkzeug {name} gibt es nicht."}, True)
        try:
            if c.fill is not None:
                args = c.fill(args, self.ledger)
            if c.normalise is not None:
                args = c.normalise(args)
        except ArgumentError as e:
            return Dispatch(name, {"error": str(e)}, c.blocking)
        if c.mcp_tool is None:
            handler = self._virtual.get(name)
            if handler is None:
                return Dispatch(name, {"error": f"{name} ist noch nicht verfügbar."}, c.blocking)
            try:
                return Dispatch(name, {"result": await handler(args)}, c.blocking)
            except ArgumentError as e:
                return Dispatch(name, {"error": str(e)}, c.blocking)
            except Exception as e:  # noqa: BLE001 - report, never crash the session
                log.exception("virtual tool %s failed", name)
                return Dispatch(name, {"error": f"{name} ist fehlgeschlagen: {e}"}, c.blocking)
        note = await self._before(c, args) if c.before else ""
        result = await self.backend.call(c.mcp_tool, args)
        if result.is_error:
            return Dispatch(name, {"error": result.text}, c.blocking)
        self.ledger.observe(name, args, result.data)
        data = c.summarise(spoken, result.data) if c.summarise is not None else result.data
        if note and isinstance(data, str):
            data = note + data
        return Dispatch(name, {"result": data}, c.blocking)

    async def _before(self, c: ToolCard, args: dict[str, Any]) -> str:
        if c.before == "stop_device_first":
            return await self.stop_device_first()
        if callable(c.before):
            return await c.before(self, args) or ""
        return ""

    async def stop_device_first(self) -> str:
        """One playback source at a time: stop the device transport before the internal engine starts."""
        if self.ledger.running and self.ledger.source == "device":
            result = await self.backend.call("transport", {"action": "stop"})
            if not result.is_error:
                self.ledger.running = False
                return "Ich habe das Gerät gestoppt. "
        return ""
