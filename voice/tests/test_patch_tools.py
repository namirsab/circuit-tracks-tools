"""Patch names: scan, cache, list, select by name."""

from __future__ import annotations

from contextlib import asynccontextmanager

from circuit_voice.bridge import Bridge
from circuit_voice.device_tools import register_device_tools
from circuit_voice.ledger import Ledger
from circuit_voice.mcp_client import McpBackend, ToolResult
from circuit_voice.patch_tools import PatchCatalog, register_patch_tools

NAMES = ["Bassix", "Random Decay", "Warm Pad"] + [f"Patch {i}" for i in range(4, 65)]


@asynccontextmanager
async def agent(server, tmp_path):
    async with McpBackend("inproc") as backend:
        ledger = Ledger()
        bridge = Bridge(backend, profile="full", ledger=ledger)
        await bridge.prepare()
        register_device_tools(bridge)
        catalog = PatchCatalog(tmp_path / "patches.json")
        register_patch_tools(bridge, catalog)
        selected = {"slot": 0}
        real_call = backend.call

        async def fake_call(name, args=None):
            if name == "get_synth_patch":
                return ToolResult(text="", data={"name": NAMES[selected["slot"]], "params": {}}, is_error=False)
            if name == "select_patch":
                selected["slot"] = args["patch_number"]
            return await real_call(name, args)

        backend.call = fake_call  # type: ignore[method-assign]
        yield bridge, ledger, catalog, server[1]


async def test_scan_then_list_and_select_by_name(server, tmp_path):
    async with agent(server, tmp_path) as (bridge, ledger, catalog, midi):
        out = await bridge.dispatch("list_patches", {})
        assert "noch nicht" in out.response["result"]
        out = await bridge.dispatch("select_patch", {"synth": 1, "name": "Bassix"})
        assert "finde keinen" in out.response["error"]

        ledger.selected_patch[2] = 7
        out = await bridge.dispatch("scan_patches", {})
        assert out.blocking is False
        assert "64 Patch-Namen" in out.response["result"] and "wieder auf Patch 7" in out.response["result"]
        assert catalog.names[1] == "Bassix" and catalog.names[64] == "Patch 64"
        assert [m for m in midi.messages if m[0] == "pc"][-1] == ("pc", 1, 6)

        out = await bridge.dispatch("list_patches", {"filter": "bass"})
        assert out.response["result"] == "Patches: 1 Bassix."
        out = await bridge.dispatch("select_patch", {"synth": 1, "name": "bassics"})
        assert "Patch 1 (Bassix) auf Synth 1" in out.response["result"]
        assert ("pc", 0, 0) in midi.messages and ledger.selected_patch[1] == 1
        out = await bridge.dispatch("select_patch", {"synth": 1, "number": 3})
        assert "3 (Warm Pad)" in out.response["result"]

        again = PatchCatalog(tmp_path / "patches.json")
        assert again.names[2] == "Random Decay"


async def test_scan_refuses_while_playing(server, tmp_path):
    async with agent(server, tmp_path) as (bridge, ledger, catalog, midi):
        ledger.running = True
        out = await bridge.dispatch("scan_patches", {})
        assert "Stopp" in out.response["result"]
        assert catalog.names == {}
