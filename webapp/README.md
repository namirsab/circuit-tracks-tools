# Circuit Tracks Web

A browser clone of the Novation Circuit Tracks groovebox. Fully self-contained:
everything it needs lives in this folder, so it can be zipped, hosted, or run
locally as-is.

## Run

Serve this folder with any static file server and open it in a browser:

```sh
python3 -m http.server 8765
# then open http://localhost:8765/
```

(Opening `index.html` via `file://` won't work — browsers block module imports
and sample fetches without a server.)

## What's bundled

- `pack/` — the **Circuit Web Starter** pack: 64 drum samples and 16 synth
  patches in Novation Components pack format (`index.json` + `samples/` +
  `patches/`). All of it is synthesized from code
  (`scripts/generate_webapp_pack.py` in the parent repo) — no third-party
  audio, freely redistributable.
- `data/Empty.ncs` — the blank project template used when exporting a fresh
  in-app project to a hardware-ready `.ncs` file.

## Using your own sounds

- **Load sample pack…** (sidebar) — pick a Components pack folder (e.g. a
  factory pack downloaded with Novation Components: `index.json`, `samples/`,
  `patches/`). Files are read locally; nothing is uploaded.
- Drop a `.ncs` project anywhere to load it; drop a `.syx` patch on Synth 1 /
  Synth 2.
