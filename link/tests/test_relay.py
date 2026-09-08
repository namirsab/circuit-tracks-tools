"""End-to-end tests for the Agent Link relay: a fake Web Tracks tab on the
WebSocket side, real HTTP (and a real MCP client) on the other."""

from __future__ import annotations

import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from websockets.sync.client import connect as ws_connect
from webtracks_link.server import Relay, create_app

TOOLS = [
    {
        "name": "set_bpm",
        "description": "Set tempo",
        "inputSchema": {"type": "object", "properties": {"bpm": {"type": "number"}}},
    },
    {
        "name": "get_sequencer_status",
        "description": "Status",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base_url():
    port = free_port()
    relay = Relay(public_url=f"http://127.0.0.1:{port}", call_timeout=0.8)
    config = uvicorn.Config(create_app(relay), host="127.0.0.1", port=port, log_level="warning", ws="websockets")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("relay did not start")
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


class FakeTab:
    """Plays the role of webapp/js/agent/link.js."""

    def __init__(self, base_url: str, resume: dict | None = None):
        self.ws = ws_connect(base_url.replace("http", "ws", 1) + "/ws", legacy=True)
        hello = {"type": "hello", "client": "fake-tab"}
        if resume:
            hello["resume"] = resume
        self.ws.send(json.dumps(hello))
        self.hello = json.loads(self.ws.recv())
        self.calls: list[dict] = []
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    @property
    def mcp_url(self) -> str:
        return self.hello["mcp_url"]

    def serve(self) -> None:
        try:
            for raw in self.ws:
                msg = json.loads(raw)
                if msg.get("method") == "tools/list":
                    self.ws.send(json.dumps({"id": msg["id"], "result": {"tools": TOOLS}}))
                elif msg.get("method") == "tools/call":
                    params = msg["params"]
                    self.calls.append(params)
                    name = params["name"]
                    if name == "boom":
                        self.ws.send(json.dumps({"id": msg["id"], "error": {"message": "exploded"}}))
                    elif name == "slow":
                        time.sleep(1.5)
                        self.ws.send(
                            json.dumps({"id": msg["id"], "result": {"content": [{"type": "text", "text": "late"}]}})
                        )
                    else:
                        time.sleep(0.05 if name == "a" else 0)
                        self.ws.send(
                            json.dumps(
                                {
                                    "id": msg["id"],
                                    "result": {
                                        "content": [
                                            {"type": "text", "text": f"{name} ok {json.dumps(params.get('arguments'))}"}
                                        ],
                                        "structuredContent": {"echo": params.get("arguments")},
                                    },
                                }
                            )
                        )
        except Exception:  # noqa: BLE001 - socket closed by the test
            pass

    def close(self) -> None:
        self.ws.close()
        self.thread.join(timeout=2)


def rpc(url: str, method: str, params: dict | None = None, rid: int | None = 1, **kw):
    body: dict = {"jsonrpc": "2.0", "method": method}
    if rid is not None:
        body["id"] = rid
    if params is not None:
        body["params"] = params
    return httpx.post(url, json=body, headers={"Accept": "application/json, text/event-stream"}, timeout=5, **kw)


def test_hello_initialize_and_notifications(base_url):
    tab = FakeTab(base_url)
    try:
        assert tab.hello["type"] == "hello"
        assert tab.mcp_url.startswith(f"{base_url}/mcp/{tab.hello['session']}/")
        r = rpc(
            tab.mcp_url,
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
        )
        assert r.status_code == 200
        assert r.headers["mcp-session-id"] == tab.hello["session"]
        result = r.json()["result"]
        assert result["protocolVersion"] == "2025-06-18"
        assert result["serverInfo"]["name"] == "Web Tracks"
        assert result["capabilities"]["tools"] == {"listChanged": False}
        assert "get_parameter_reference" in result["instructions"]
        r = rpc(tab.mcp_url, "initialize", {"protocolVersion": "1999-01-01"})
        assert r.json()["result"]["protocolVersion"] == "2025-06-18"
        r = rpc(tab.mcp_url, "notifications/initialized", rid=None)
        assert r.status_code == 202 and r.content == b""
    finally:
        tab.close()


def test_tools_list_and_call_round_trip(base_url):
    tab = FakeTab(base_url)
    try:
        r = rpc(tab.mcp_url, "tools/list")
        assert r.json()["result"]["tools"] == TOOLS
        r = rpc(tab.mcp_url, "tools/call", {"name": "set_bpm", "arguments": {"bpm": 128}}, rid="abc")
        body = r.json()
        assert body["id"] == "abc"
        assert body["result"]["content"][0]["text"] == 'set_bpm ok {"bpm": 128}'
        assert body["result"]["structuredContent"] == {"echo": {"bpm": 128}}
        assert tab.calls == [{"name": "set_bpm", "arguments": {"bpm": 128}}]
        r = rpc(tab.mcp_url, "tools/call", {"name": "get_sequencer_status"})
        assert tab.calls[-1] == {"name": "get_sequencer_status", "arguments": {}}
        assert rpc(tab.mcp_url, "ping").json()["result"] == {}
    finally:
        tab.close()


def test_errors_from_tab_client_and_relay(base_url):
    tab = FakeTab(base_url)
    try:
        assert rpc(tab.mcp_url, "tools/call", {"name": "boom"}).json()["error"]["code"] == -32603
        assert rpc(tab.mcp_url, "tools/call", {}).json()["error"]["code"] == -32600
        assert rpc(tab.mcp_url, "resources/list").json()["error"]["code"] == -32601
        slow = rpc(tab.mcp_url, "tools/call", {"name": "slow"}).json()
        assert slow["error"]["code"] == -32002
        bad = httpx.post(tab.mcp_url, content=b"{not json", headers={"content-type": "application/json"}, timeout=5)
        assert bad.status_code == 400 and bad.json()["error"]["code"] == -32700
        wrong = rpc(tab.mcp_url[:-4] + "xxxx", "ping")
        assert wrong.status_code == 404 and wrong.json()["error"]["code"] == -32001
        assert httpx.get(tab.mcp_url, timeout=5).status_code == 405
        assert httpx.delete(tab.mcp_url, timeout=5).status_code == 204
    finally:
        tab.close()
    time.sleep(0.2)
    gone = rpc(tab.mcp_url, "ping")
    assert gone.status_code == 404 and "Connect an AI agent" in gone.json()["error"]["message"]


def test_batch_and_concurrent_calls(base_url):
    tab = FakeTab(base_url)
    try:
        r = httpx.post(
            tab.mcp_url,
            json=[
                {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            ],
            timeout=5,
        )
        body = r.json()
        assert [m["id"] for m in body] == [1, 2]
        assert body[1]["result"]["tools"] == TOOLS

        results: dict[str, str] = {}

        def call(name: str) -> None:
            results[name] = rpc(tab.mcp_url, "tools/call", {"name": name, "arguments": {"n": name}}).json()["result"][
                "content"
            ][0]["text"]

        threads = [threading.Thread(target=call, args=(n,)) for n in ("a", "b", "c")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert results == {n: f'{n} ok {{"n": "{n}"}}' for n in ("a", "b", "c")}
    finally:
        tab.close()


def test_resume_keeps_the_same_url(base_url):
    first = FakeTab(base_url)
    url = first.mcp_url
    resume = {"session": first.hello["session"], "secret": first.hello["secret"]}
    first.close()
    time.sleep(0.2)
    assert rpc(url, "ping").status_code == 404
    second = FakeTab(base_url, resume=resume)
    try:
        assert second.mcp_url == url
        assert rpc(url, "ping").status_code == 200
        other = FakeTab(base_url)
        try:
            assert other.mcp_url != url
        finally:
            other.close()
        bogus = FakeTab(base_url, resume={"session": "short", "secret": "x"})
        try:
            assert bogus.hello["session"] != "short"
        finally:
            bogus.close()
    finally:
        second.close()


def test_status_pages(base_url):
    tab = FakeTab(base_url)
    try:
        info = httpx.get(base_url + "/", timeout=5).json()
        assert info["name"] == "Web Tracks Agent Link" and info["sessions"] >= 1
        assert httpx.get(base_url + "/healthz", timeout=5).text == "ok"
    finally:
        tab.close()


def test_real_mcp_client(base_url):
    """The official MCP Python client speaks Streamable HTTP to the relay."""
    import asyncio

    from mcp.client import Client

    tab = FakeTab(base_url)

    async def run():
        async with Client(tab.mcp_url) as client:
            tools = await client.list_tools()
            result = await client.call_tool("set_bpm", {"bpm": 100})
            return client.server_info, client.instructions, tools, result

    try:
        info, instructions, tools, result = asyncio.run(run())
        assert info.name == "Web Tracks"
        assert "get_parameter_reference" in instructions
        assert [t.name for t in tools.tools] == ["set_bpm", "get_sequencer_status"]
        assert tools.tools[1].annotations.read_only_hint is True
        assert result.content[0].text == 'set_bpm ok {"bpm": 100}'
        assert result.structured_content == {"echo": {"bpm": 100}}
    finally:
        tab.close()
