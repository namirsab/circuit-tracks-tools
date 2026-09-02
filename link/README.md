# Web Tracks Agent Link

A tiny relay that gives a [Web Tracks](../webapp/) browser tab a public
[MCP](https://modelcontextprotocol.io) endpoint, so MCP clients such as Claude
Code, Claude Desktop, claude.ai or Cursor can compose and perform on the web
groovebox without any hardware or browser extension.

```
Web Tracks tab  ──WebSocket──▶  Agent Link  ◀──Streamable HTTP MCP──  Claude
  (tools run here)               (relay only)     POST /mcp/<session>/<secret>
```

- The tab connects to `/ws`, gets a session id, a secret and the MCP URL to
  paste into the client. Pressing **Connect an AI agent** in the Web Tracks
  sidebar does this.
- MCP clients `POST` JSON-RPC to `/mcp/<session>/<secret>`. The relay answers
  `initialize` and `ping` itself and forwards `tools/list` and `tools/call` to
  the tab, which runs the tool in the page and returns the result.
- Nothing is stored. Sessions live in memory while the tab is connected. A tab
  that reconnects (reload, network blip) with its previous id and secret keeps
  the same URL. Audio never leaves the browser; only tool payloads (song JSON,
  parameter values) transit the relay.
- Without a connected tab the endpoint answers with JSON-RPC error `-32001`
  and a hint, so the client can tell the user what to do.

## Run

```sh
pip install -e ./link          # or: pip install webtracks-link
webtracks-link --host 127.0.0.1 --port 8770
# or: python -m webtracks_link
```

Then, in Web Tracks, open the **AI agent** section of the sidebar, set the
relay URL to `ws://localhost:8770/ws` under *Relay URL* and press
**Connect an AI agent**. Add the URL it shows to your client, e.g.

```sh
claude mcp add --transport http webtracks "https://link.example/mcp/<session>/<secret>"
```

Environment / flags: `LINK_HOST`, `LINK_PORT`, `LINK_PUBLIC_URL` (base URL the
relay is reachable at when behind a proxy; otherwise derived from the request
host and `X-Forwarded-Proto`), `LINK_CALL_TIMEOUT` (seconds a tool call may take,
default 60), `LINK_MAX_SESSIONS` (default 1000), `LINK_LOG_LEVEL`.

## Deploy

`Dockerfile` in this folder serves the relay on port 8770. With Coolify: New
Resource → this repo → Build Pack **Dockerfile** → Base Directory `/link`,
attach a domain (for example `link.webtracks.namirsab.dev`), and set
`LINK_PUBLIC_URL=https://<that domain>`. WebSockets must be allowed by the
proxy (Coolify's Traefik does this by default).

## Test

```sh
pip install -e "./link[dev]"
pytest link/tests
```

The tests start a real server, connect a fake tab over WebSocket and drive the
endpoint with plain HTTP and with the official MCP Python client.

## Versioning

Independent of the Python library and of the webapp: `webtracks_link/__init__.py`
holds the version; changes are listed in `CHANGELOG.md` here.
