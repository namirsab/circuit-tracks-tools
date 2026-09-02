# Web Tracks

An unofficial, fan-made browser groovebox inspired by the Novation Circuit
Tracks. Fully self-contained: everything it needs lives in this folder, so it
can be zipped, hosted, or run locally as-is.

By [Namir Sayed-Ahmad Baraza](https://namirsab.dev). MIT licensed (see the
repository root `LICENSE`).

## Disclaimer

Web Tracks is a **non-commercial fan project**. It is **not affiliated with,
endorsed, or sponsored by Novation, Focusrite plc, or any of their
subsidiaries**. "Circuit Tracks", "Novation", and "Components" are trademarks
of their respective owners and are used here only to describe compatibility.

- The `.ncs` project format and the Components pack format were independently
  **reverse-engineered** by observing files the hardware/Components produce.
  No Novation source code, firmware, or proprietary documentation was used.
- **All bundled content is generated from code**: the 64 drum samples are
  synthesized by `scripts/generate_webapp_pack.py` (stdlib DSP, no recordings),
  and the 128 patches and 16 demo projects are authored in this repository.
  Nothing here is copied from Novation factory content.
- The synth engine is an original Web Audio implementation; it approximates
  the hardware's behaviour but shares no code or DSP with it.

## Run

Serve this folder with any static file server and open it in a browser:

```sh
python3 -m http.server 8765
# then open http://localhost:8765/
```

(Opening `index.html` via `file://` won't work — browsers block module imports
and sample fetches without a server.)

## Deploy

The app is 100% static — no build step, no backend. Any static host works.
The optional Agent Link relay (`link/`) is a separate tiny service; see its README.

**With Coolify** (two options):

- *Dockerfile (recommended, deterministic):* New Resource → your Git repo →
  Build Pack **Dockerfile** → set **Base Directory** to `/webapp` (the
  `Dockerfile` in this folder serves it with nginx). Attach your domain,
  deploy; Coolify handles HTTPS via Let's Encrypt and redeploys on push.
- *Static build pack:* New Resource → your Git repo → Build Pack **Static**,
  no build command, **Publish Directory** `/webapp`.

No SPA fallback or special headers are needed — it's a single page, all
assets are same-origin relative paths, and packs/samples are plain files.

## AI agents (MCP)

Web Tracks exposes its whole console as [MCP](https://modelcontextprotocol.io)
tools, so Claude (or any MCP client) can compose, perform and sound-design on
this page with no hardware. Tool names and arguments mirror the
`circuit-tracks-mcp` hardware server wherever the concept exists, so prompts
written for a real Circuit Tracks work here unchanged; the agent's moves show
up live on the pads, knobs and LCD, and the sidebar keeps a log of its calls
with an **Undo last agent change** button.

Three ways in:

- **Agent Link (any MCP client, no extension).** Press **Connect an AI agent…**
  in the sidebar. The tab opens a WebSocket to a small relay and shows a
  private URL; add it to your client and keep the tab open:

  ```sh
  claude mcp add --transport http webtracks "https://link.example/mcp/<session>/<secret>"
  ```

  Claude Desktop and claude.ai take the same URL as a custom connector.
  The relay only forwards tool calls; audio never leaves the browser. The
  default relay is `wss://link.webtracks.namirsab.dev/ws`; run your own with
  the code in [`link/`](../link/) and change the address under *Relay URL*.
  A reload keeps the same URL (the session is resumed).
- **WebMCP.** The same tools are registered on `document.modelContext` when
  the browser has it (Chrome origin trial / `chrome://flags`), for in-browser
  agents and WebMCP bridge extensions.
- **Page global.** `window.webtracks.call(name, args)` and
  `window.webtracks.list()` from the console or any script runner, e.g.
  Claude in Chrome's JavaScript tool.

Tools (26 + song tools): `get_parameter_reference`, `get_sequencer_status`,
`load_song`, `read_project`, `set_pattern`, `set_track`, `get_pattern`,
`list_patterns`, `clear_pattern`, `start_sequencer`, `stop_sequencer`,
`transport`, `set_bpm`, `set_swing`, `queue_patterns`, `set_song`,
`clear_queue`, `select_pattern`, `mute_track`, `set_synth_params`,
`edit_synth_patch`, `create_synth_patch`, `get_synth_patch`,
`save_synth_patch`, `set_drum_params`, `set_project_params`, `set_macro`, `get_macros`,
`play_notes`, `play_drum`, `list_drum_samples`, `list_patches`,
`select_patch`, `list_projects`, `select_project`, `export_song_to_project`,
`download_project`, `undo`. Call `get_parameter_reference` with no section
first: it returns the workflow, the rules and Web Tracks specific notes.
The song format and parameter reference are generated from the Python
library (`scripts/generate_agent_data.py`) so both servers answer alike.

Browsers block sound until the page has been clicked once; the **Connect**
button doubles as that click. Tests: `cd webapp && node --test tests/*.test.mjs`
(the song compiler is checked against golden `.ncs` files produced by the
Python library).

## What's bundled

- `pack/` — the **Web Tracks Starter** pack: 64 drum samples, a full bank
  of 128 synth patches, and 16 demo projects in Novation Components pack
  format (`index.json` + `samples/` + `patches/` + `projects/`). All of it
  is generated from code (`scripts/generate_webapp_pack.py` in the parent
  repo) — no third-party audio, freely redistributable.
- `data/Empty.ncs` — the blank project template used when exporting a fresh
  in-app project to a hardware-ready `.ncs` file.

## Using your own sounds

- **Load pack…** (sidebar, or Shift+Projects) — pick a `.circuittrackspack`
  (or `.zip`) pack archive: a zipped Components export with `index.json`,
  `samples/`, `patches/`, and `projects/`. Dropping one anywhere works too.
- **Load pack folder…** (sidebar) — same, but from an unzipped pack folder.
- Files are read locally in the browser; nothing is uploaded.
- Drop a `.ncs` project anywhere to load it; drop a `.syx` patch on Synth 1 /
  Synth 2.

## Projects view

The **Projects** button opens the 64-slot project grid (32 per page, each
stored project in its project colour; empty slots are grey). Press a pad to
load that slot — while playing, the switch is queued to the end of the
current pattern like the hardware; **Shift+pad switches immediately**.
Pressing an empty slot starts an init project in that slot. Drop a `.ncs`
file onto a pad to fill that slot without switching. Shift+Projects opens
the pack picker (Packs).

## Saving & exporting

- **Save** works like the hardware: press once to arm (the button blinks
  and the grid shows the 14 project colours — press one to recolour), press
  again to save into the current slot. **Shift+Save** downloads the project
  as a `.ncs` file.
- Sidebar export buttons: **Export project** (.ncs), **Export patches**
  (both synth patches as .syx), and **Export pack** — bundles the current
  samples, patch bank, and all stored project slots into a
  `.circuittrackspack` you can reload later (or open in Components).
- Saved slots live in memory for the session; the page warns before
  closing if you have unexported saves.
