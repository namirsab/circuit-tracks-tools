# Circuit Voice

German voice control for the Novation Circuit Tracks. A Gemini Live audio
session drives a curated set of the `circuit-tracks-mcp` tools so that people
who cannot use the pads and knobs, or cannot see the device, can build music
step by step, perform it, and save it to the device.

Spec and design decisions: [`docs/specs/voice-agent.md`](../docs/specs/voice-agent.md).

## What you can say (German)

| Family | Examples |
| --- | --- |
| Songs | "Neuer Song mit 100 BPM", "Lade den Song Nachtfahrt", "Welche Songs gibt es?" |
| Build step by step | "Kick auf 1, 5, 9, 13", "Snare auf 5 und 13", "Schreib C3, E3, G3 auf Synth eins", "Hi-Hats auf jedem Schritt, jeder zweite leiser", "Schritt 5 auf der Kick lauter", "Lösch die Hi-Hats" |
| Ideas (the agent asks first, takes seconds) | "Bau mir eine groovige Bassline auf Synth zwei", "Design mir einen warmen Pad-Sound", "Bau mir einen Hip-Hop-Song bei 90 BPM" |
| Perform | "Start", "Stopp", "Tempo auf 118", "etwas schneller", "Schalte die Kick stumm", "Drums wieder an", "Nach dem Intro den Chorus" |
| Sounds | "Filter auf Synth eins auf", "mehr Resonanz", "Makro 5 auf 80", "Mehr Hall auf den Drums", "Synth eins leiser", "Filter langsam in Bewegung bringen" |
| Patches | "Welche Patches gibt es?", "Gibt es einen Bass-Patch?", "Nimm den Patch Bassix auf Synth zwei", "Lies die Patch-Namen ein" |
| Drum samples | "Welche Drum-Samples gibt es?", "Nimm für Drum zwei ein Snare-Sample" (audible after saving) |
| Device projects | "Spiel Projekt drei", "Tempo auf 100", "Hol Projekt 5 rein" |
| Save | "Speicher das in Slot 32 als Test" (asks once), "Speicher den Patch in Slot 40" (asks once) |
| State and help | "Was läuft gerade?", "Rückgängig", "Was kann ich sagen?", "kurz antworten" |

Numbers are spoken the way the device shows them: projects, patches and slots
1 to 64, steps 1 to 32, C3 is the middle C.

## Install

```sh
# from the repo root, inside venv/
pip install -e ".[mcp]" && pip install -e "./voice[dev]"
echo 'GEMINI_API_KEY=...' >> .env      # .env is gitignored
```

## Run

```sh
set -a; source .env; set +a
venv/bin/circuit-voice --key-source terminal --ptt-key space --songs-dir ~/circuit-songs
```

Keep the terminal window focused, press Space once to talk (rising tone),
press it again when you are done (falling tone), wait for the answer. The
Circuit Tracks must be connected over USB and switched on.

### Listening modes

| Mode | Flag | When |
| --- | --- | --- |
| Push-to-talk (hold) | `--mode ptt --ptt-key alt_r` | Needs a global hotkey (see permissions) |
| Toggle (press, press) | `--mode toggle` or `--key-source terminal` | The reliable default with speakers in the room |
| Open microphone | `--mode open` | Headphones only; the mic is muted while the agent speaks |

### macOS permissions

- **Microphone**: macOS asks the first time.
- **Global hotkey** (`--key-source global`, the default when you pass `--mode ptt`): macOS must trust
  the process under System Settings → Privacy & Security → Accessibility. The
  permission is attributed to the Python interpreter, not the terminal; if the
  tone never comes, use `--key-source terminal` instead, which needs nothing.

### Switches and pedals

Anything that acts as a USB or Bluetooth keyboard works: map it to the key you
pass with `--ptt-key` (for example `f13` or `space`). Tested candidates and
prices are listed in the spec under "Switch hardware".

## Useful flags

```
--songs-dir DIR       folder of song JSON files loadable by name
--song FILE           load a song at start
--patch-cache FILE    cached patch names (default ~/.circuit-voice/patches.json)
--composer-model ID   text model for ideas (default gemini-3.8-flash)
--tools control       hide the idea tools (no model jobs at all)
--silence-ms N        end-of-speech silence for open mode (500-3000)
--max-minutes N       end the session after N minutes
--log FILE / --no-log events log (JSON lines: transcripts, tools, latencies; never audio)
--dry-run "Text"      one text turn, prints the tool calls, exits
--list-devices        audio devices
```

## How it behaves

- Exact commands run instantly (under a second from key release to MIDI).
- Ideas (a bassline, a sound, a whole song) go to a text model; the agent
  says how long it takes and asks before starting.
- Saving to the device stops the sequencer, captures the synths' current
  patches and the live drum and mixer values, and writes the project. A
  project change while sounds were changed asks once.
- Every action is spoken back with the resulting value. "Rückgängig" reverts
  the last parameter, mute or step change.

## Test

```sh
pytest voice/tests                      # no network, no devices
ruff check voice
CIRCUIT_VOICE_EVAL=1 pytest voice/tests/eval -q -s   # German evals, needs GEMINI_API_KEY
```
