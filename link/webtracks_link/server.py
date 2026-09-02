"""Agent Link relay: a public MCP endpoint for a Web Tracks browser tab.

A tab opens a WebSocket to ``/ws`` and receives a session id, a secret and
the MCP URL to paste into an MCP client. MCP clients speak Streamable HTTP to
``POST /mcp/{session}/{secret}``: the relay answers ``initialize`` and
``ping`` itself and forwards ``tools/list`` and ``tools/call`` to the tab over
the socket, returning whatever the page's tool registry produced. Nothing is
stored: sessions live in memory while the tab is connected, and a tab that
reconnects with its previous id and secret keeps the same URL.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import time
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import __version__

log = logging.getLogger("webtracks_link")

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
DEFAULT_PROTOCOL = "2025-06-18"
HELLO_TIMEOUT = 10.0
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{24,64}$")

SERVER_INFO = {"name": "Web Tracks", "version": __version__}
INSTRUCTIONS = (
    "You are connected to Web Tracks, a browser groovebox that emulates the Novation "
    "Circuit Tracks, through the Agent Link relay. The tools run inside the user's open "
    "browser tab and every change is visible on its pads, knobs and LCD; keep that tab open. "
    "Start with get_parameter_reference (no section) for the workflow, rules and Web Tracks "
    "notes, then get_sequencer_status. Compose with load_song, listen with start_sequencer, "
    "tweak live with set_synth_params / set_drum_params / set_project_params / set_macro, "
    "and save with export_song_to_project or download_project."
)

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603
TAB_GONE = -32001
TAB_TIMEOUT = -32002

TAB_GONE_MESSAGE = (
    "No Web Tracks tab is connected to this link. Open Web Tracks, press "
    "'Connect an AI agent' in the sidebar and use the URL it shows."
)


class TabGone(Exception):
    """The tab behind a session went away (or never answered)."""


class TabError(Exception):
    """The tab answered a forwarded request with an error."""


class Session:
    """One browser tab: its socket plus the requests waiting on it."""

    def __init__(self, sid: str, secret: str) -> None:
        self.id = sid
        self.secret = secret
        self.ws: WebSocket | None = None
        self.online = False
        self.client: Any = None
        self.created = time.time()
        self.calls = 0
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}

    def attach(self, ws: WebSocket) -> None:
        self.ws = ws
        self.online = True

    def detach(self, ws: WebSocket) -> None:
        if self.ws is not ws:
            return
        self.ws = None
        self.online = False
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(TabGone())
        self._pending.clear()

    async def request(self, method: str, params: dict, timeout: float) -> Any:
        if not self.online or self.ws is None:
            raise TabGone()
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self.ws.send_json({"id": rid, "method": method, "params": params})
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)

    def resolve(self, msg: dict) -> None:
        fut = self._pending.get(msg.get("id"))
        if fut is None or fut.done():
            return
        if "error" in msg:
            err = msg["error"]
            fut.set_exception(TabError(err.get("message", str(err)) if isinstance(err, dict) else str(err)))
        else:
            fut.set_result(msg.get("result"))


def jsonrpc_error(rid: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": err}


def negotiate_protocol(requested: Any) -> str:
    return requested if requested in PROTOCOL_VERSIONS else DEFAULT_PROTOCOL


class Relay:
    def __init__(
        self, public_url: str | None = None, call_timeout: float | None = None, max_sessions: int | None = None
    ) -> None:
        self.sessions: dict[str, Session] = {}
        self.public_url = public_url if public_url is not None else os.environ.get("LINK_PUBLIC_URL")
        self.call_timeout = (
            call_timeout if call_timeout is not None else float(os.environ.get("LINK_CALL_TIMEOUT", "60"))
        )
        self.max_sessions = (
            max_sessions if max_sessions is not None else int(os.environ.get("LINK_MAX_SESSIONS", "1000"))
        )

    # ----- URLs -----
    def base_url(self, headers, url) -> str:
        if self.public_url:
            return self.public_url.rstrip("/")
        proto = headers.get("x-forwarded-proto") or {"ws": "http", "wss": "https"}.get(url.scheme, url.scheme)
        host = headers.get("x-forwarded-host") or headers.get("host") or url.netloc
        return f"{proto}://{host}"

    def mcp_url(self, base: str, session: Session) -> str:
        return f"{base}/mcp/{session.id}/{session.secret}"

    # ----- sessions -----
    def open_session(self, resume: Any) -> Session | None:
        if isinstance(resume, dict):
            sid = str(resume.get("session", ""))
            secret = str(resume.get("secret", ""))
            if SESSION_ID_RE.match(sid) and SECRET_RE.match(secret):
                existing = self.sessions.get(sid)
                if existing is None:
                    if len(self.sessions) >= self.max_sessions:
                        return None
                    session = Session(sid, secret)
                    self.sessions[sid] = session
                    return session
                if secrets.compare_digest(existing.secret, secret):
                    return existing
                # The id belongs to someone else: hand out a fresh session.
        if len(self.sessions) >= self.max_sessions:
            return None
        while True:
            sid = secrets.token_urlsafe(6)
            if sid not in self.sessions:
                break
        session = Session(sid, secrets.token_urlsafe(24))
        self.sessions[sid] = session
        return session

    async def websocket(self, ws: WebSocket) -> None:
        await ws.accept()
        first: Any = {}
        try:
            first = await asyncio.wait_for(ws.receive_json(), HELLO_TIMEOUT)
        except WebSocketDisconnect:
            return
        except Exception:  # noqa: BLE001 - a silent or malformed hello means a fresh session
            first = {}
        if not isinstance(first, dict):
            first = {}
        session = self.open_session(first.get("resume"))
        if session is None:
            await ws.send_json({"type": "error", "message": "relay is full, try again later"})
            await ws.close(code=1013)
            return
        old = session.ws
        if old is not None and old is not ws:
            session.detach(old)
            try:
                await old.close(code=4000, reason="replaced by a newer connection")
            except Exception:  # noqa: BLE001
                pass
        session.attach(ws)
        session.client = first.get("client")
        base = self.base_url(ws.headers, ws.url)
        await ws.send_json(
            {
                "type": "hello",
                "session": session.id,
                "secret": session.secret,
                "mcp_url": self.mcp_url(base, session),
                "relay": SERVER_INFO,
            }
        )
        log.info("tab connected: session=%s client=%s sessions=%d", session.id, session.client, len(self.sessions))
        try:
            while True:
                msg = await ws.receive_json()
                if not isinstance(msg, dict):
                    continue
                if "id" in msg and ("result" in msg or "error" in msg):
                    session.resolve(msg)
                elif msg.get("type") == "ping":
                    await ws.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001
            log.warning("session %s socket error: %s", session.id, exc)
        finally:
            session.detach(ws)
            if not session.online:
                self.sessions.pop(session.id, None)
            log.info("tab disconnected: session=%s calls=%d", session.id, session.calls)

    # ----- MCP over Streamable HTTP -----
    async def mcp(self, request: Request) -> Response:
        if request.method == "GET":
            # No server-initiated stream: clients poll tools/list as needed.
            return Response(status_code=405, headers={"Allow": "POST, DELETE"})
        if request.method == "DELETE":
            return Response(status_code=204)
        session = self.sessions.get(request.path_params["session"])
        secret = request.path_params["secret"]
        if session is None or not secrets.compare_digest(session.secret, secret) or not session.online:
            return JSONResponse(jsonrpc_error(None, TAB_GONE, TAB_GONE_MESSAGE), status_code=404)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(jsonrpc_error(None, PARSE_ERROR, "Request body is not valid JSON"), status_code=400)
        batch = isinstance(body, list)
        messages = body if batch else [body]
        responses = []
        for msg in messages:
            if not isinstance(msg, dict):
                responses.append(jsonrpc_error(None, INVALID_REQUEST, "Each message must be a JSON-RPC object"))
                continue
            if "method" not in msg:
                continue  # a response to a server request; the relay sends none
            if "id" in msg:
                responses.append(await self.handle_request(session, msg))
            else:
                self.handle_notification(session, msg)
        if not responses:
            return Response(status_code=202)
        return JSONResponse(responses if batch else responses[0], headers={"Mcp-Session-Id": session.id})

    def handle_notification(self, session: Session, msg: dict) -> None:
        if msg.get("method") == "notifications/initialized":
            log.info("session %s: client initialised", session.id)

    async def handle_request(self, session: Session, msg: dict) -> dict:
        rid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            return jsonrpc_error(rid, INVALID_REQUEST, "params must be an object")
        try:
            if method == "initialize":
                info = params.get("clientInfo") or {}
                log.info("session %s: initialize from %s %s", session.id, info.get("name"), info.get("version"))
                result: Any = {
                    "protocolVersion": negotiate_protocol(params.get("protocolVersion")),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                    "instructions": INSTRUCTIONS,
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = await session.request("tools/list", {}, self.call_timeout)
                if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
                    raise TabError("tab returned a malformed tools/list result")
            elif method == "tools/call":
                name = params.get("name")
                if not isinstance(name, str) or not name:
                    return jsonrpc_error(rid, INVALID_REQUEST, "tools/call needs a tool name")
                session.calls += 1
                log.info("session %s: tools/call %s", session.id, name)
                result = await session.request(
                    "tools/call", {"name": name, "arguments": params.get("arguments") or {}}, self.call_timeout
                )
                if not isinstance(result, dict) or "content" not in result:
                    raise TabError("tab returned a malformed tools/call result")
            else:
                return jsonrpc_error(rid, METHOD_NOT_FOUND, f"Method not supported by Web Tracks Agent Link: {method}")
            return {"jsonrpc": "2.0", "id": rid, "result": result}
        except TabGone:
            return jsonrpc_error(rid, TAB_GONE, TAB_GONE_MESSAGE)
        except TimeoutError:
            return jsonrpc_error(rid, TAB_TIMEOUT, f"The Web Tracks tab did not answer within {self.call_timeout:g} s")
        except TabError as exc:
            return jsonrpc_error(rid, INTERNAL_ERROR, f"Web Tracks tab error: {exc}")

    # ----- misc -----
    async def index(self, request: Request) -> Response:
        return JSONResponse(
            {
                "name": "Web Tracks Agent Link",
                "version": __version__,
                "sessions": sum(1 for s in self.sessions.values() if s.online),
                "how": "Open Web Tracks, press 'Connect an AI agent', and add the URL it shows to your MCP client.",
            }
        )

    async def healthz(self, request: Request) -> Response:
        return PlainTextResponse("ok")


def create_app(relay: Relay | None = None) -> Starlette:
    relay = relay or Relay()
    app = Starlette(
        routes=[
            Route("/", relay.index),
            Route("/healthz", relay.healthz),
            Route("/mcp/{session}/{secret}", relay.mcp, methods=["GET", "POST", "DELETE"]),
            WebSocketRoute("/ws", relay.websocket),
        ],
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["*"],
                allow_headers=["*"],
                expose_headers=["Mcp-Session-Id"],
            )
        ],
    )
    app.state.relay = relay
    return app


app = create_app()
