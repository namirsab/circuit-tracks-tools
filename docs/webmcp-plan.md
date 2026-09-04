# Web Tracks as an MCP target: WebMCP + Agent Link

Status: implemented on branch `feat/webapp-agent-tools` on 2026-09-02 following the
recommendations below (registry + headless API, WebMCP adapter, Agent Link relay in
`link/`, song compiler and patch builder ports with golden vectors). Usage lives in
`webapp/README.md` (AI agents section) and `link/README.md`; this document keeps the
reasoning. Deviations from the plan: the page-global and WebMCP surfaces are always
on (same-origin, harmless); only the relay is opt-in via **Connect an AI agent**.

## Goal

Let any MCP client (Claude Code, Claude Desktop, claude.ai, Cursor, ...) compose
and perform music on Web Tracks, so people without a Circuit Tracks can use the
same agent workflow the Python MCP server gives hardware owners. The agent's
changes must show up live on the pads, knobs and LCD of the web app.

## What "WebMCP" actually buys us today

WebMCP is the W3C/WebML community-group API that lets a *page* publish tools:

```js
const mc = document.modelContext ?? navigator.modelContext; // renamed July 2026
await mc.registerTool({
  name: 'load_song',
  description: '...',
  inputSchema: { type: 'object', properties: { ... }, required: [...] },
  async execute(args, { signal }) {
    return { content: [{ type: 'text', text: '...' }] }; // MCP-shaped result
  },
});
mc.unregisterTool('load_song');
```

Landscape as of September 2026:

- Chrome: shipped natively in 146, public **origin trial from 149 to 156**.
  Production use on `webtracks.namirsab.dev` needs an origin-trial token meta
  tag, otherwise the API is only behind `chrome://flags`. Edge has it behind a
  flag. Firefox and Safari have no timeline.
- Consumers: **no mainstream agent calls WebMCP tools yet**. Not Claude
  Desktop, not Claude Code, not Claude in Chrome. Google says Gemini in Chrome
  will be the first. The Claude in Chrome feature request
  (anthropics/claude-code#30645) was closed as stale in April 2026.
- Bridges that work today: the "WebMCP Bridge" Chrome extension plus its
  `webmcp-server` stdio process, `webmcp-cdp-bridge` (drives a tab over the
  DevTools protocol, no extension), and MCP-B (`@mcp-b/global` polyfill plus a
  Streamable HTTP endpoint on localhost). All third party, all local-only.

So WebMCP alone does not achieve "any MCP client". It is the right *shape* for
the tools and the right long-term surface, but we need our own transport to
reach today's clients. Hence two exposure paths over one tool set.

## Architecture

```
                 ┌──────────────────────────── browser tab ────────────────────────────┐
                 │  CircuitApp (audio, sequencer, UI)                                  │
                 │        ▲                                                            │
                 │  1. headless command API  (loadProject, setStep, setParam, play…)   │
                 │        ▲                                                            │
                 │  2. tool registry  [{name, description, inputSchema, execute}]      │
                 │        ▲              ▲                     ▲                       │
                 │  3a. WebMCP      3b. Agent Link        3c. window.webtracks         │
                 │  document.       WebSocket client      (dev console, tests,         │
                 │  modelContext         │                 Claude in Chrome JS)        │
                 └───────────────────────┼─────────────────────────────────────────────┘
                                         │ wss
   Gemini in Chrome,            ┌────────▼─────────┐   Streamable HTTP MCP
   bridge extensions            │  Agent Link relay │◄──────────────────────  Claude Code
   (via 3a)                     │  (tiny service)   │   POST /mcp/<session>   Claude Desktop
                                └──────────────────┘                          claude.ai, Cursor
```

1. **Headless command API.** Every mutation an agent needs, callable without
   DOM events, updating audio *and* UI. A lot exists already: `applyProject`,
   `seq.start/stop/setBpm/setSwing`, `synthTracks[i].setPatch`,
   `drums.applyConfig`, track mute/level/pan, `exportProject`. Gaps to close:
   step and pattern editing (today inlined in pad handlers), live synth/drum
   parameter changes (inlined in `knobChanged`), macro moves, pattern
   queueing, project-slot save. First task is an audit of `app.js` handlers,
   extracting methods the tools can call.
2. **Tool registry.** One array of MCP-shaped tool descriptors in
   `webapp/js/agent/tools.js`, plus JSON Schema validation of inputs so the
   model gets precise errors (vendor a small dependency-free validator, or
   validate in the compiler). Transport-agnostic and unit-testable in Node.
3. **Adapters.**
   - `webmcp.js`: feature-detect, `registerTool` for every entry, unregister
     on teardown. Add the origin-trial token for the production origin.
   - `link.js`: WebSocket client for the Agent Link relay (below).
   - `window.webtracks.tools`: same registry on the page for the dev console,
     Node tests, and Claude in Chrome's JavaScript execution, which is a
     zero-install way to drive it from Claude Code on day one.

## Tool surface (v1)

Mirror the Python tool names and argument shapes for everything that is not
MIDI plumbing, so prompts, skills and docs written for the hardware server work
unchanged, and an agent that learned one can use the other.

| Tool | Notes |
| --- | --- |
| `get_parameter_reference(section)` | Serve from `webapp/data/parameter-reference.json`, generated from the Python dicts by a script (same pattern as `generate_webapp_pack.py`). One source of truth. |
| `load_song(song)` | The centrepiece. Validate against `webapp/data/song.schema.json` (dumped from `get_song_json_schema()`), compile to a webapp project, `applyProject`. |
| `read_project()` | Project → song JSON so the agent can read what the human did on the pads and iterate. Port of the `ncs_to_song` idea on the project model. |
| `set_pattern`, `get_pattern`, `list_patterns`, `clear_pattern` | Incremental edits ("change the bass in pattern B"). |
| `queue_patterns`, `set_song`, `clear_queue` | Scenes / chains. |
| `start_sequencer`, `stop_sequencer`, `transport`, `set_bpm`, `get_sequencer_status`, `mute_track` | Direct sequencer calls. |
| `set_synth_params`, `set_drum_params`, `set_project_params`, `set_macro`, `get_macros` | Live tweaks while playing, reflected on the knobs. |
| `play_notes`, `play_drum` | Audition. |
| `get_synth_patch`, `edit_synth_patch`, `create_synth_patch`, `select_patch`, `save_synth_patch` | Needs the patch builder port (mod matrix + macro encoding). The unmerged `feat/webapp-patch-builder` branch already extends `patch.js` for this. |
| `list_drum_samples` | Better than hardware: the loaded pack has real sample names. |
| `export_song_to_project(slot, name)`, `select_project` | Save into a bank slot and/or download `.ncs`. |
| `list_midi_ports`, `connect`, `disconnect`, `send_cc`, `send_nrpn`, `start_clock`, `stop_clock`, `send_project_file`, `load_patch_file`, `set_drum_sample_names` | **Dropped.** Hardware or filesystem specific. `morph_*` deferred (needs a JS morph engine; nice later). |

Mark read-only tools with `annotations.readOnlyHint` so WebMCP hosts can skip
confirmation prompts for them.

## The song compiler

Port of `song.py` (`_schema_to_song_data` + the write half of `song_to_ncs`)
targeting the `defaultProject()` model instead of raw NCS bytes; `ncs.js`
already turns that model into bytes. Pieces:

- Scale quantisation and the +12 NCS offset. `scales.js` already matches the
  Python semantics (ties round up).
- Step notes, chords, gate, tie, micro-steps and probability into the 6-slot
  step structure.
- Synth step `macros` and drum `params` lanes into `paramLocks`.
- `sounds` → patch bytes (patch builder port) and `drumConfigs`.
- `fx`, `sidechain`, `mixer`, presets and the closest-preset fallbacks.
- `song: [names]` → scenes and `sceneChain`; pattern names → per-track slots.

**Conformance by golden vectors.** For every song fixture in `tests/test_song.py`
(plus the journal's `ethereal-drift-song.json`), Python emits NCS bytes. The JS
test parses those with `parseNCS` and deep-compares against `compileSong(song)`
after normalisation. Passing means the browser path and the hardware path agree
byte for byte where it matters. Runner: `node --test webapp/tests/`, no deps,
plain ES modules, which also gives the webapp its first automated tests.

## Agent Link relay

A deliberately tiny service so a public MCP URL can reach a browser tab:

- Tab opens `wss://link.<host>/ws`, gets a session id and a 128-bit secret.
  The sidebar shows `https://link.<host>/mcp/<id>/<secret>` with a copy button
  and connection state.
- Relay implements Streamable HTTP: `POST /mcp/...` with JSON-RPC. It answers
  `initialize` and `ping` itself, forwards `tools/list` and `tools/call` to the
  tab over the socket, and pushes `notifications/tools/list_changed` when the
  tab re-registers. When the tab is gone, calls return a clear error.
- Stateless relay, no persistence, no audio ever leaves the browser. Only
  tool payloads (song JSON) transit it, and only after the user opts in.
- Same code runs locally (`webtracks-link` on `localhost`) for privacy or
  offline use; the app accepts a custom relay URL.
- Implementation: Python with the `mcp` 2.0 SDK (`server.lowlevel.Server`
  with dynamic `list_tools`/`call_tool` handlers plus the Streamable HTTP
  session manager) or ~250 lines of Starlette speaking JSON-RPC directly.
  Deploy on Coolify next to the webapp. It is a third deployable with its own
  version, separate from both the webapp and the Python library.
- Client setup becomes one line, e.g.
  `claude mcp add --transport http webtracks <url>`.

## In-app UX

- Agent tools are inert until the user clicks **Connect an AI agent**. That
  click also resumes the AudioContext, so an agent-triggered `play` is not
  blocked by autoplay policy.
- Activity log in the sidebar: each tool call as it happens, so watching the
  agent work is part of the fun.
- Snapshot the project before every mutating tool; an **Undo agent change**
  button restores it. Persistence already autosaves, so this is cheap.
- Agent tools respect the same constraints as the hardware server (uniform
  pattern length, gate ≤ 16, macro-add semantics) by reusing its rules text.

## Alternatives considered

- **Python server drives the webapp as a virtual device** (WebSocket or a
  Web MIDI virtual port carrying CC/NRPN/SysEx). Reuses all 48 tools
  unchanged, but keeps the `pip install` requirement, which is exactly the
  friction for people without hardware, forces the webapp to grow a full MIDI
  message interpreter, and runs the sequencer clock in Python over a socket
  instead of in the browser. Worth revisiting later purely as a hardware-parity
  test rig.
- **Webapp-native tool dialect** instead of mirroring the Python names.
  Cheaper up front, but throws away the tuned song format and doubles the
  prompt engineering.
- **Rely only on third-party bridges** instead of Agent Link. Zero infra, but
  every user installs an extension plus a Node process, and claude.ai can
  never reach it.

## Phases

0. **Spike** (small). Registry with three tools (`get_parameter_reference`,
   `transport`, load a fixed demo project) on `window.webtracks` and
   `document.modelContext`. Drive it from Claude Code via Claude in Chrome's
   JavaScript tool and via `webmcp-cdp-bridge`. Confirms the loop and the fun
   before any porting. Also verifies whether extension-executed JS sees page
   globals or needs a `postMessage` shim.
1. **Headless API + live tools** (medium). Handler audit, extracted methods,
   transport/params/mute/audition/status/export tools, WebMCP adapter, Node
   test runner.
2. **Song compiler + `load_song` + `read_project`** (large). Generated schema
   and parameter-reference JSON, golden-vector tests against Python.
3. **Patch tools** (medium). Port `patch_builder.py`; land or reuse
   `feat/webapp-patch-builder`.
4. **Agent Link** (medium). Relay, sidebar UI, Coolify deploy, client setup
   docs for Claude Code, Claude Desktop and claude.ai.
5. **Polish** (small). Activity log, undo, origin-trial token, README and
   changelog, demo recording, `morph_*` if wanted.

## Open decisions

1. Build Agent Link (recommended) or ship WebMCP only and point users at
   third-party bridges?
2. Mirror the Python tool names (recommended) or design a webapp-native API?
3. Relay language and home: Python in this repo under `link/`, or Node?
4. Adopt `node --test` for webapp tests (recommended, required for golden
   vectors)?
5. Accept generated artifacts in `webapp/data/` (song schema, parameter
   reference) produced by a Python script, as the pack already is?

## Sources

- https://www.spronta.com/blog/state-of-webmcp-july-2026/
- https://studiomeyer.io/en/blog/webmcp-reality-check-may-2026
- https://dev.to/ai-agent-economy/webmcp-in-2026-which-browsers-support-navigatormodelcontext-complete-compatibility-status-1oe4
- https://github.com/webmachinelearning/webmcp
- https://github.com/anthropics/claude-code/issues/30645
- https://github.com/littleplato/webmcp-cdp-bridge
- https://chromewebstore.google.com/detail/webmcp-bridge/chgjbookknohehmaocfijekhaocaanaf
- https://github.com/MiguelsPizza/WebMCP
