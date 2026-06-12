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

- `pack/` — the **Circuit Web Starter** pack: 64 drum samples, a full bank
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
