"""Tool cards → Gemini function declarations, and dispatch back to the MCP backend."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from circuit_voice.bridge import Bridge
from circuit_voice.mcp_client import McpBackend
from circuit_voice.tool_cards import CARDS, cards_for_profile

GOLDEN = Path(__file__).parent / "fixtures" / "declarations.json"


@asynccontextmanager
async def make_bridge(server, profile="full"):
    async with McpBackend("inproc") as backend:
        b = Bridge(backend, profile=profile)
        await b.prepare()
        yield b, server[1]


def _dump(decls):
    return [d.model_dump(exclude_none=True, mode="json") for d in decls]


async def test_declarations_match_golden(server):
    async with make_bridge(server) as (b, midi):
        current = _dump(b.declarations())
        if os.environ.get("UPDATE_GOLDEN"):
            GOLDEN.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n")
        assert current == json.loads(GOLDEN.read_text()), "run with UPDATE_GOLDEN=1 after an intended card change"


async def test_every_declaration_is_clean(server):
    async with make_bridge(server) as (b, midi):
        decls = b.declarations()
        assert {d.name for d in decls} == {c.name for c in cards_for_profile("full")}
        for d in decls:
            assert d.description and len(d.description) > 20, d.name
            schema = json.dumps(d.parameters_json_schema)
            assert '"title"' not in schema, d.name
            assert d.parameters_json_schema["type"] == "object"


async def test_select_patch_is_one_based(server):
    async with make_bridge(server) as (b, midi):
        out = await b.dispatch("select_patch", {"synth": 2, "number": 64})
        assert ("pc", 1, 63) in midi.messages
        assert "64" in out.response["result"]
        for bad in (0, 65):
            midi.messages.clear()
            out = await b.dispatch("select_patch", {"synth": 1, "number": bad})
            assert "error" in out.response
            assert "zwischen 1 und 64" in out.response["error"]
            assert midi.messages == []


async def test_unknown_or_hidden_tool_is_an_error(server):
    async with make_bridge(server) as (b, midi):
        out = await b.dispatch("export_song_to_project", {"slot": 0})
        assert "error" in out.response
        assert midi.messages == []
        out = await b.dispatch("nope", {})
        assert "error" in out.response


async def test_blocking_flag_follows_the_card(server):
    async with make_bridge(server) as (b, midi):
        by_name = {d.name: d for d in b.declarations()}
        assert by_name["set_bpm"].behavior.value == "BLOCKING"
        assert by_name["get_synth_patch"].behavior.value == "NON_BLOCKING"
        assert b.is_blocking("set_bpm") is True
        assert b.is_blocking("get_synth_patch") is False


async def test_mcp_result_is_passed_through(server):
    async with make_bridge(server) as (b, midi):
        out = await b.dispatch("get_sequencer_status", {})
        assert out.response["result"]["running"] is False
        out = await b.dispatch("set_bpm", {"bpm": 100})
        assert "100" in out.response["result"]


async def test_virtual_tool_handler(server):
    async with make_bridge(server) as (b, midi):
        seen = []

        async def handler(args):
            seen.append(args)
            return "Modus offen"

        b.register_virtual("set_listening", handler)
        out = await b.dispatch("set_listening", {"mode": "open"})
        assert seen == [{"mode": "open"}]
        assert out.response["result"] == "Modus offen"


def test_perform_profile_is_a_subset():
    full = {c.name for c in cards_for_profile("full")}
    perform = {c.name for c in cards_for_profile("perform")}
    assert perform <= full
    assert len(CARDS) == len({c.name for c in CARDS}), "duplicate card names"
