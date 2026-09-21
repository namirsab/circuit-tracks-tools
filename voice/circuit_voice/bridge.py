"""Bridge between the Live model's function calls and the MCP backend.

``prepare`` fetches the server's input schemas for every card in the active
profile and builds Gemini ``FunctionDeclaration`` objects (full JSON Schema via
``parameters_json_schema``). ``dispatch`` runs one call: normalise arguments,
route to a virtual handler or the MCP tool, and wrap the outcome in the
``response`` dict of a ``FunctionResponse``: ``{"result": ...}`` or
``{"error": "<German sentence>"}``.
"""

from __future__ import annotations

import asyncio
import copy
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

HEARD_WAIT_S = 1.5  # how long a confirmed call waits for the transcript of what the user said
YES_MAX_WORDS = 8  # a yes is short; a longer sentence is a new request
_YES = (
    "ja jaa jawohl jo joa yo jep jup yes yeah yep ok okay oke klar mach machs mache bitte gerne gern genau los "
    "passt einverstanden sicher richtig stimmt si sí go bestätige bestätigt speichern speicher überschreiben"
)
_NO = "nein nee ne nö no nicht stopp stop halt abbrechen abbruch warte lieber doch"
YES_WORDS = frozenset(_YES.split())
NO_WORDS = frozenset(_NO.split())


def heard_yes(text: str) -> bool | None:
    """True for a clear yes, False for a clear no, None when it is neither (or not understood)."""
    words = [w.strip(".,!?;:'\"()").lower() for w in text.split()]
    words = [w for w in words if w]
    if any(w in NO_WORDS for w in words):
        return False
    if 0 < len(words) <= YES_MAX_WORDS and any(w in YES_WORDS for w in words):
        return True
    return None


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
        self._heard: str | None = None  # what the user said last; None when there is no transcript at all
        self._asked: set[str] = set()  # tools whose question is waiting for an answer
        if "get_state" in self.cards:
            self._virtual["get_state"] = self._get_state
        if "undo_last" in self.cards:
            self._virtual["undo_last"] = self._undo_last

    async def sync_device(self) -> dict[str, Any]:
        """Take over what the device reported since the last look (project pad, Play/Stop, tempo)."""
        result = await self.backend.call("get_device_state", {})
        state = result.data if not result.is_error and isinstance(result.data, dict) else {}
        self.ledger.observe_device(state)
        return state

    async def load_song_dict(self, data: dict[str, Any], label: str) -> str:
        """Make ``data`` the current song: engine patterns, patches sent, ledger updated."""
        result = await self.backend.call("load_song", {"song": copy.deepcopy(data)})  # the server mutates its input
        ledger = self.ledger
        ledger.last_load_ok = not result.is_error
        if result.is_error:
            return f"Der Song {label} konnte nicht geladen werden: {result.text[:300]}"
        info = result.data if isinstance(result.data, dict) else {}
        name = info.get("name") or data.get("name") or label
        ledger.song_loaded(
            name, float(info.get("bpm") or data.get("bpm") or 120), info.get("patterns", []), info.get("song_order", [])
        )
        ledger.source = "internal"
        ledger.song_json = data
        patterns = ", ".join(ledger.patterns)
        return f"Song {name} geladen, {int(ledger.bpm)} BPM, Patterns: {patterns}."

    async def _get_state(self, args: dict[str, Any]) -> str:
        status = None
        await self.sync_device()
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

    # --- what the user said, for confirmations ---

    def listening_started(self) -> None:
        self._heard = ""
        self._last_key, self._repeats = None, 0  # the user speaks: the same call again is not a loop

    def heard(self, text: str) -> None:
        self._heard = ((self._heard or "") + " " + text).strip()
        if self._asked and heard_yes(self._heard) is False:
            self._asked.clear()  # a clear no drops the open question

    async def _check_confirmation(self, name: str, args: dict[str, Any]) -> dict[str, Any] | str:
        """Overwriting and long jobs need the user's own clear yes, not the model's impression of one.

        Seen on hardware: the transcript "llave" was passed on as a yes and a project slot was
        overwritten; another time the model confirmed a job nobody had been asked about.
        Returns the arguments to run with, or the error text for the model.
        """
        if not args.get("confirmed") or self._heard is None:
            return args  # nothing to check, or no transcripts in this setup (text mode, tests)
        if name not in self._asked:
            return {k: v for k, v in args.items() if k != "confirmed"}  # ask first
        waited = 0.0
        while not self._heard and waited < HEARD_WAIT_S:  # the transcript can trail the tool call
            await asyncio.sleep(0.1)
            waited += 0.1
        if not self._heard:
            log.warning("confirmed %s without a transcript of the answer", name)
            return args
        if heard_yes(self._heard):
            return args
        return (
            f"Not confirmed: the user said '{self._heard[:80]}', which is not a clear yes. Do not run it. Ask the "
            "same question again in one short German sentence and wait for the answer."
        )

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
        checked = await self._check_confirmation(name, dict(args or {}))
        if isinstance(checked, str):
            self._last_stuck = True
            return Dispatch(name, {"error": checked}, self.is_blocking(name))
        out = await self._dispatch(name, checked)
        result = out.response.get("result")
        if isinstance(result, dict) and "needs_confirmation" in result:
            self._asked.add(name)
        elif checked.get("confirmed"):
            self._asked.discard(name)
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
