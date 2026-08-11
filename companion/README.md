# Circuit Sampler — mobile companion app for the Novation Circuit Tracks

A mobile-first PWA sampler: record sound on your phone, trim and polish it,
and transfer it into one of the Circuit Tracks' 64 drum sample slots over
USB MIDI — no server, no build step.

```
Record (mic / line-in / USB interface / tab audio) — or Import a file
  → Edit (trim, zoom, normalize, gain, fades)
    → Library (IndexedDB, survives reloads)
      → Transfer (Web MIDI SysEx → drum sample slot 0–63)
      → …or Export .wav (works everywhere, incl. iOS)
```

## Running it

Any static file server works. From the repo root:

```sh
python3 -m http.server 8765
# open http://localhost:8765/companion/ (Chrome/Edge desktop or Android Chrome)
```

For Web MIDI on a phone the page must be served over HTTPS (or localhost).
Once opened, it can be installed to the home screen (PWA) and the app shell
works offline.

### URL flags (for testing/demo)

- `?fakeinput=1` — adds a **“Test tone (fake input)”** device that synthesizes
  a pulsing tone in Web Audio and routes it through the exact same
  `MediaStream` capture path as a real microphone. Lets you exercise the full
  record → edit → export flow with no mic and no permission prompt.
- `?nomidi=1` — simulates a browser without Web MIDI, to check the fallback UI.

## Sampling sources beyond the microphone

- **Import audio** (Library → *Import audio*, works on every platform incl.
  iOS): any file the browser can decode (mp3, m4a, wav, ogg, flac). It's mixed
  down to mono and enters the same edit → transfer pipeline as a recording.
- **Tab / system audio** (input picker → *Tab / system audio (screen share)*,
  desktop Chrome/Edge only): captures another tab's audio via
  `getDisplayMedia` — pick the tab (e.g. the Spotify web player) and enable
  **“Also share tab audio”** in Chrome's picker, then record as usual.
- **Internal audio on the phone itself is not possible from a browser.**
  Neither Android nor iOS exposes system/app audio to web pages, and Spotify
  additionally opts out of Android's playback-capture API, so even native
  apps can't record it. Practical phone routes: import a file you have, or
  sample on the desktop via tab capture.

Heads-up: sampling streamed music may be restricted by the service's terms
and by copyright — fine for private noodling, but cleared samples are your
responsibility if you release anything.

## Platform support and the iOS decision

Sample **transfer** needs Web MIDI with SysEx, which is available in
Chrome/Edge on desktop and Chrome on Android, but **not in any iOS browser**
(all iOS browsers use WebKit, which does not implement Web MIDI).
**Recording and editing** (getUserMedia + Web Audio) work everywhere,
including iOS Safari.

**Decision: full sampler everywhere, transfer gated on capability, `.wav`
export as the universal exit path.**

- The Record / Edit / Library features are identical on every platform.
- The *Send to Circuit* actions are enabled only when
  `navigator.requestMIDIAccess` exists. On iOS the Circuit tab explains why
  transfer is unavailable and points at the two working paths:
  1. **Export `.wav`** from the Library (files are already in the Circuit's
     native format — 48 kHz/16-bit/mono) and load them with
     [Novation Components](https://components.novationmusic.com) on a computer.
  2. Open this app in Chrome on Android or desktop, where transfer works
     directly.
- The app never dead-ends: every sample can always be recorded, edited and
  exported regardless of browser.

## Sample format & limits

- Target format (verified against `CircuitFactorySamples.zip`):
  **WAV, 48 kHz, 16-bit PCM, mono**. Every recording is converted client-side
  (mixdown → linear resample → 16-bit encode) before export/transfer.
- Per-file limit: the protocol's WRITE_INIT carries the file size as 5 hex
  nibbles, capping one sample at **1,048,575 bytes ≈ 10.9 s**. The editor
  shows the converted size and blocks transfer beyond the cap.
- Total device memory: the Circuit Tracks has **60 s** of sample memory
  (Novation spec; the full factory pack uses ~31.6 s of it). The device
  rejects writes that don't fit — the transfer UI surfaces that as a failed
  transfer.

## Transfer protocol

Implements the reverse-engineered Novation Components file protocol
(`docs/sysex-file-protocol.md`), ported from the Python reference
`src/circuit_tracks/ncs_transfer.py`:

- session open → directory handshake → directory listing (file type `0x05`,
  drum samples) so you see exactly what each slot contains before overwriting;
- WRITE_INIT / WRITE_DATA (8192-byte blocks, MSB-interleave encoded) /
  WRITE_FINISH (CRC32) / SET_FILENAME, waiting for the device ACK after every
  write message;
- overwriting a non-empty slot requires explicit in-app confirmation.

## Code layout

```
companion/
  index.html, css/style.css     app shell (no framework, no build step)
  js/app.js                     views and wiring
  js/audio/recorder.js          getUserMedia capture (AudioWorklet + fallback)
  js/audio/convert.js           pure conversion/edit ops (Node-testable)
  js/midi/protocol.js           pure SysEx protocol port (Node-testable)
  js/midi/transfer.js           Web MIDI transport, ACK handling, sequencing
  js/store.js                   IndexedDB library
  sw.js, manifest.webmanifest   PWA
  tests/                        node --test suites + golden vectors
  tools/generate_golden.py      regenerates vectors from the Python reference
  tools/generate_icons.py       regenerates the PWA icons
```

## Testing

```sh
node --test 'companion/tests/*.test.mjs'
```

- `protocol.test.mjs` verifies the JS protocol layer **byte-for-byte** against
  golden vectors generated by the Python reference implementation
  (MSB interleave, nibble encoding, block addressing, file IDs, CRC32, and
  every message of a complete 3-block write sequence).
  Regenerate vectors with `venv/bin/python companion/tools/generate_golden.py`.
- `convert.test.mjs` verifies audio conversion: stereo 44.1 kHz in →
  valid mono 48 kHz 16-bit WAV out, header layout identical to the factory
  samples, plus normalize/gain/fade/trim behaviour.

### Hardware verification (needs the device)

These steps can't be automated without a Circuit Tracks attached:

1. Connect the Circuit Tracks via USB, open the app in Chrome, tab **Circuit**,
   tap **Connect** and grant the SysEx permission.
2. The slot grid should populate with the device's current sample names
   (directory listing, file type `0x05`).
3. Record a short sample, then **Send** it to an *empty* slot; the progress
   bar should advance block-by-block and finish with a CRC + filename summary.
4. **Refresh** the slot grid — the new name should appear in the chosen slot.
5. On the hardware: select a drum track → Preset view → pick the slot and
   confirm the sample plays.
6. Optionally overwrite an occupied slot to confirm the confirmation flow.
