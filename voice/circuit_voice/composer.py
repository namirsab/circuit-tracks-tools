"""The composer step: a text model writes the song JSON, we validate it.

The live voice model never writes song JSON. ``Composer.compose`` sends the
brief (or the current song plus a revision) to a ``ComposerModel``, validates
the answer with ``SongSchema`` and the house rules, and retries with the error
text up to ``max_retries`` times. The model is pluggable: ``GeminiComposer``
in production, ``FakeComposer`` in tests, an Anthropic backend later.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from typing import Any, Protocol

from circuit_tracks.song_schema import SongSchema, get_song_json_schema

from .mcp_client import McpBackend
from .prompt_composer import compose_prompt, retry_prompt, revise_prompt, system_prompt

REFERENCE_SECTIONS = ("song_format", "best_practices")


class ComposeFailed(RuntimeError):
    """The composer could not produce a valid song; the message is the last error."""


class ComposerModel(Protocol):
    name: str

    async def generate(self, system: str, messages: Sequence[tuple[str, str]], json_schema: dict[str, Any]) -> Any:
        """Return the parsed JSON answer for a conversation of (role, text) turns."""


class GeminiComposer:
    def __init__(self, api_key: str, model_id: str = "gemini-3.8-flash", temperature: float = 0.7):
        from google import genai  # noqa: PLC0415 - only when a real model is wanted

        self._client = genai.Client(api_key=api_key)
        self.name = model_id
        self.temperature = temperature

    async def generate(self, system: str, messages: Sequence[tuple[str, str]], json_schema: dict[str, Any]) -> Any:
        from google.genai import types  # noqa: PLC0415

        contents = [types.Content(role=role, parts=[types.Part(text=text)]) for role, text in messages]
        response = await self._client.aio.models.generate_content(
            model=self.name,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_json_schema=json_schema,
                temperature=self.temperature,
            ),
        )
        return json.loads(response.text or "null")


class FakeComposer:
    """Returns scripted answers in order; records every call."""

    name = "fake"

    def __init__(self, answers: Sequence[Any]):
        self.answers = list(answers)
        self.calls: list[list[tuple[str, str]]] = []

    async def generate(self, system: str, messages: Sequence[tuple[str, str]], json_schema: dict[str, Any]) -> Any:
        self.calls.append(list(messages))
        if not self.answers:
            raise ComposeFailed("fake composer ran out of answers")
        return self.answers.pop(0)


def check_house_rules(song: dict[str, Any]) -> list[str]:
    """Rules SongSchema does not enforce; empty list means fine."""
    problems: list[str] = []
    patterns = song.get("patterns") or {}
    if not patterns:
        problems.append("the song has no patterns")
    lengths = {name: p.get("length", 32) for name, p in patterns.items()}
    if len(set(lengths.values())) > 1:
        problems.append(f"patterns must all have the same length, got {lengths}")
    sounds = song.get("sounds") or {}
    for synth in ("synth1", "synth2"):
        if not sounds.get(synth):
            problems.append(f"'sounds' must include a full patch config for {synth}")
    for name in song.get("song") or []:
        if name not in patterns:
            problems.append(f"song order references unknown pattern {name!r}")
    return problems


def validate_song(candidate: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(candidate, dict):
        return None, "the answer is not a JSON object"
    try:
        SongSchema.model_validate(copy.deepcopy(candidate))  # validation mutates its input
    except Exception as e:  # noqa: BLE001 - pydantic errors are long; keep the head
        return None, str(e)[:1200]
    problems = check_house_rules(candidate)
    if problems:
        return None, "; ".join(problems)
    return candidate, None


class Composer:
    def __init__(self, model: ComposerModel, backend: McpBackend | None = None, max_retries: int = 2):
        self.model = model
        self.backend = backend
        self.max_retries = max_retries
        self._system: str | None = None
        self._schema = get_song_json_schema()

    async def prepare(self) -> None:
        sections: dict[str, str] = {}
        if self.backend is not None:
            for name in REFERENCE_SECTIONS:
                result = await self.backend.call("get_parameter_reference", {"section": name})
                if not result.is_error:
                    sections[name] = result.text
        self._system = system_prompt(sections)

    @property
    def system(self) -> str:
        if self._system is None:
            self._system = system_prompt({})
        return self._system

    async def compose(self, brief: str, current_song: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a validated song dict; raises ComposeFailed after the retries."""
        if current_song is not None:
            first = revise_prompt(brief, json.dumps(current_song, ensure_ascii=False))
        else:
            first = compose_prompt(brief)
        messages: list[tuple[str, str]] = [("user", first)]
        error: str | None = None
        for _attempt in range(self.max_retries + 1):
            answer = await self.model.generate(self.system, messages, self._schema)
            song, error = validate_song(answer)
            if song is not None:
                if current_song is not None and not song.get("name") and current_song.get("name"):
                    song["name"] = current_song["name"]
                return song
            messages.append(("model", json.dumps(answer, ensure_ascii=False)[:20000]))
            messages.append(("user", retry_prompt(error or "invalid")))
        raise ComposeFailed(error or "invalid song")


def track_edits(old: dict[str, Any], new: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]] | None:
    """Changed (pattern, track, track_data) triples when only step data changed; None means reload."""
    old_p, new_p = old.get("patterns") or {}, new.get("patterns") or {}
    if set(old_p) != set(new_p):
        return None
    for key in ("bpm", "scale", "swing", "sounds", "fx", "mixer", "song"):
        if old.get(key) != new.get(key):
            return None
    edits: list[tuple[str, str, dict[str, Any]]] = []
    for name in new_p:
        if old_p[name].get("length", 32) != new_p[name].get("length", 32):
            return None
        old_t, new_t = old_p[name].get("tracks") or {}, new_p[name].get("tracks") or {}
        for track in set(old_t) | set(new_t):
            if old_t.get(track) != new_t.get(track):
                data = new_t.get(track) or {"steps": {}}
                if set(data) - {"steps"}:
                    return None  # track-level params/macros: set_track cannot carry them
                edits.append((name, track, data))
    return edits
