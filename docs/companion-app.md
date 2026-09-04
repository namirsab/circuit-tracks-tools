# Task: Build a Mobile Web Companion App for the Novation Circuit Tracks

## Goal

Build a fully functional, mobile-first web companion app for the Circuit Tracks. Core feature: a **sampler** — record sound on the phone, edit it, and transfer it into one of the Circuit Tracks' 64 drum sample slots over USB MIDI.

This is a **standalone app** in its own directory (e.g. `companion/`), independent from the `webapp/` clone. Reuse ideas and code from existing implementations, but do not couple the two apps.

## Platform constraint — decide and document your mitigation

Sample transfer uses Web MIDI (SysEx), which is **not available in iOS Safari**. It works in Android Chrome and desktop Chrome/Edge. Recording and editing (getUserMedia + Web Audio) work everywhere, including iOS.

You decide how to handle iOS — e.g. full sampler everywhere with transfer gated on Web MIDI support, plus `.wav` export as the iOS fallback. Whatever you choose: the app must never dead-end — on an unsupported browser the user must still be able to record, edit, and get their sample out. Document the decision in the app's README.

## Documentation — read these before writing any code

- `docs/sysex-file-protocol.md` — the complete reverse-engineered file transfer protocol: session handshake, directory listing, write sequence (WRITE_INIT / WRITE_DATA / WRITE_FINISH / SET_FILENAME), MSB-interleave encoding, CRC32, ACK handling. Drum samples are file type `0x05`, slots 0–63.
- `src/circuit_tracks/ncs_transfer.py` — working Python reference implementation of the protocol (`list_directory()`, `send_ncs_project()`, `encode_msb_interleave()`). Port its logic faithfully.
- `circuit_tracks_user_guide_v3_en.pdf` — sample section: slot behaviour, total sample memory limit, how samples map to drum tracks.
- `webapp/js/patch.js` — existing Web MIDI + SysEx handling in the browser, for reference.

## Sample format

Target format (verified against `CircuitFactorySamples.zip`): **WAV, 48 kHz, 16-bit PCM, mono**. Convert every recording to this format before transfer (resample, mixdown, 16-bit encode) client-side. Check the user guide for the total sample memory limit and enforce it in the UI.

## Features

### Recording

- Record via `getUserMedia` from any available input: built-in mic, headset/line-in, USB audio interface. Show an input-device picker with live level metering before recording.
- Show a live waveform while recording; no fixed max duration during capture (limit is enforced at edit/transfer time).

### Editing

- Waveform view with touch-friendly trim handles (start/end), zoom, and audition playback of the trimmed region.
- Normalize, gain adjust, and short fade-in/fade-out to kill clicks.
- Show the resulting sample length and size against the Circuit Tracks limits.

### Library

- Persist recordings locally (IndexedDB) with name, date, duration — survives page reloads, no server.
- Export any sample as a `.wav` file (this is also the iOS fallback path).

### Transfer

- Connect to the Circuit Tracks over Web MIDI (request SysEx permission, auto-detect the port by name).
- List the device's current sample slots via the directory listing (file type `0x05`) so the user sees what they'd overwrite.
- Pick a slot (0–63), send the sample using the documented write sequence, wait for per-block ACKs, verify CRC32, set the filename.
- Show transfer progress and clear success/failure states; overwriting a non-empty slot requires explicit confirmation.

### Look & feel

- Mobile-first, one-handed use: large touch targets, portrait layout, no hover-dependent UI.
- Installable PWA (manifest + icons) so it lives on the home screen; app shell works offline (transfer obviously needs the device connected).

## Future features — out of scope, don't build

- Patch editor

## Verification

Hardware transfer can't be fully verified without the device — split verification into what you can prove and what you hand off:

1. Unit-test the protocol layer against the Python reference: MSB-interleave encode/decode round-trips, CRC32, WRITE_INIT size nibbles, block addressing. Same input bytes must produce byte-identical SysEx messages to `ncs_transfer.py`.
2. Unit-test audio conversion: a stereo 44.1 kHz input comes out as mono 48 kHz 16-bit WAV with a valid header.
3. Use the `claude-in-chrome` MCP tools at a mobile viewport (390×844): no console errors on load; record → edit → save to library → export `.wav` works end-to-end using a fake/virtual audio input if no mic is available.
4. Verify the no-Web-MIDI path: with MIDI unavailable, the app still allows record/edit/export and clearly explains why transfer is disabled.
5. Report the remaining hardware steps for the user to run: connect Circuit Tracks via USB, list sample slots, transfer one sample to an empty slot, confirm it plays on the hardware.
