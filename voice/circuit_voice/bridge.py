"""Bridge between the Live model's function calls and the MCP backend.

``prepare`` fetches the server's input schemas for every card in the active
profile and builds Gemini ``FunctionDeclaration`` objects (full JSON Schema via
``parameters_json_schema``). ``dispatch`` runs one call: normalise arguments,
route to a virtual handler or the MCP tool, and wrap the outcome in the
``response`` dict of a ``FunctionResponse``: ``{"result": ...}`` or
``{"error": "<German sentence>"}``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from google.genai import types

from .ledger import Ledger
from .mcp_client import McpBackend
from .tool_cards import ArgumentError, ToolCard, cards_for_profile

log = logging.getLogger("circuit_voice.bridge")

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
        self.cards: dict[str, ToolCard] = {c.name: c for c in cards_for_profile(profile)}
        self._virtual: dict[str, VirtualHandler] = {}
        self._declarations: list[types.FunctionDeclaration] = []
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

    def declarations(self) -> list[types.FunctionDeclaration]:
        return list(self._declarations)

    def is_blocking(self, name: str) -> bool:
        c = self.cards.get(name)
        return True if c is None else c.blocking

    def register_virtual(self, name: str, handler: VirtualHandler) -> None:
        if name not in self.cards or self.cards[name].mcp_tool is not None:
            raise ValueError(f"{name} is not a virtual tool in this profile")
        self._virtual[name] = handler

    async def dispatch(self, name: str, args: dict[str, Any] | None) -> Dispatch:
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
            except Exception as e:  # noqa: BLE001 - report, never crash the session
                log.exception("virtual tool %s failed", name)
                return Dispatch(name, {"error": f"{name} ist fehlgeschlagen: {e}"}, c.blocking)
        result = await self.backend.call(c.mcp_tool, args)
        if result.is_error:
            return Dispatch(name, {"error": result.text}, c.blocking)
        self.ledger.observe(name, args, result.data)
        data = c.summarise(spoken, result.data) if c.summarise is not None else result.data
        return Dispatch(name, {"result": data}, c.blocking)
