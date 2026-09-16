"""Connection to the circuit-tracks MCP server.

Three targets behind one interface: ``inproc`` (the hardware server imported
and driven in this process, the default), ``stdio:<command>`` (a subprocess),
and an ``http(s)://`` URL (Streamable HTTP, for example an Agent Link session
of Web Tracks). Tool results from the hardware server are text content holding
JSON; ``call`` parses that into ``ToolResult.data`` and never raises for tool
errors, so the voice session can always report something to the user.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from typing import Any

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

CIRCUIT_PORT_HINT = "Circuit Tracks"
ERROR_PREFIXES = ("Invalid", "Not connected", "No song", "No response", "No MIDI", "No project", "Error", "Unknown")


class MidiNotFound(RuntimeError):
    """No usable MIDI port; the message lists what is available."""


@dataclass(frozen=True)
class ToolResult:
    text: str
    data: Any
    is_error: bool


class McpBackend:
    def __init__(self, target: str = "inproc", call_timeout: float = 60.0):
        self.target = target
        self.call_timeout = call_timeout
        if target == "inproc":
            self.kind = "inproc"
        elif target.startswith("stdio:"):
            self.kind = "stdio"
        elif target.startswith(("http://", "https://")):
            self.kind = "http"
        else:
            raise ValueError(f"Unknown MCP target {target!r}: use inproc, stdio:<command> or an http(s) URL")
        self._client: Client | None = None
        self._tools: dict[str, Any] | None = None

    async def __aenter__(self) -> McpBackend:
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def connect(self) -> None:
        if self.kind == "inproc":
            import circuit_mcp.server as hardware

            server: Any = hardware.mcp
        elif self.kind == "stdio":
            parts = shlex.split(self.target[len("stdio:") :])
            server = stdio_client(StdioServerParameters(command=parts[0], args=parts[1:]))
        else:
            server = self.target
        self._client = Client(server, read_timeout_seconds=self.call_timeout)
        await self._client.__aenter__()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None
            self._tools = None

    @property
    def client(self) -> Client:
        if self._client is None:
            raise RuntimeError("McpBackend is not connected")
        return self._client

    async def tools(self) -> dict[str, Any]:
        """Tool descriptors by name (cached after the first listing)."""
        if self._tools is None:
            listed = await self.client.list_tools()
            self._tools = {t.name: t for t in listed.tools}
        return self._tools

    async def tool_names(self) -> list[str]:
        return sorted(await self.tools())

    async def input_schema(self, name: str) -> dict[str, Any]:
        tool = (await self.tools())[name]
        return getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", {}) or {}

    async def call(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
        try:
            result = await self.client.call_tool(name, args or {})
        except Exception as e:  # noqa: BLE001 - the session must never die on a tool failure
            return ToolResult(text=f"{name}: {e}", data=None, is_error=True)
        text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        is_error = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
        data = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
        if data is None and text:
            try:
                data = json.loads(text)
            except ValueError:
                data = text
        if isinstance(data, dict) and set(data) == {"result"}:
            data = data["result"]  # MCPServer wraps plain string returns
        # The hardware server reports failures as plain strings or {"error": ...}
        # rather than isError; recognise its conventions.
        if (isinstance(data, dict) and "error" in data and len(data) <= 2) or (
            isinstance(data, str) and data.startswith(ERROR_PREFIXES)
        ):
            is_error = True
        return ToolResult(text=text, data=data, is_error=is_error)

    async def connect_midi(self, port_name: str | None = None) -> str:
        """Connect the hardware server to the Circuit Tracks; returns the port used."""
        ports = await self.call("list_midi_ports", {})
        outputs: list[str] = (ports.data or {}).get("output_ports", []) if isinstance(ports.data, dict) else []
        if port_name is None:
            port_name = next((p for p in outputs if CIRCUIT_PORT_HINT.lower() in p.lower()), None)
        if port_name is None or port_name not in outputs:
            listing = ", ".join(outputs) if outputs else "keine"
            raise MidiNotFound(
                f"Kein MIDI-Port mit '{CIRCUIT_PORT_HINT}' gefunden. Verfügbare Ports: {listing}. "
                "Ist der Circuit Tracks per USB angeschlossen und eingeschaltet?"
            )
        result = await self.call("connect", {"port_name": port_name})
        if result.is_error:
            raise MidiNotFound(result.text)
        return port_name
