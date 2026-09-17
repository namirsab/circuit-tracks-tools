# Spec: Circuit Voice — German voice control for Circuit Tracks

Status: draft for review, 2026-09-16. Decisions marked **locked** were taken
with the project owner during the research session; everything else is a
proposal.

## 1. Objective

### Problem

The MCP server is currently showcased through Claude Code with push-to-talk.
That works for developers, but for the people this project wants to reach it
is the wrong shape: Claude Code is slow to respond, carries dozens of unrelated
tools and a large system context, speaks English by default, and needs a
terminal, a keyboard and eyesight. A person who cannot use the pads and knobs of
a Circuit Tracks needs a focused agent that listens in German, answers in
German, acts within a second or two, and says what it did.

### What we are building

`circuit-voice`: a small Python program that runs next to the Circuit Tracks,
holds one Gemini Live audio session, exposes a curated subset of the
`circuit-tracks-mcp` tools to the model, and turns spoken German into MIDI on
the device. The main workflow is: build music in the MCP server's internal
sequencer by voice, perform and shape it live, and at the end save it to a
project slot on the device. Composition is a separate step behind the voice
loop, run by a composer model that may differ from the live model, so the
conversation stays fast while the heavy song JSON is produced elsewhere. The
agent also performs projects already stored on the device, and it reports
state back in speech throughout.

### Users (**locked**)

| Profile | Situation | What the agent must do for them |
| --- | --- | --- |
| Motor impairments (primary) | Can speak clearly; cannot press pads, turn knobs or hold a device. May use a single switch or foot pedal. | Full command coverage of live performance by voice; push-to-talk on any switch that acts as a key; never require the screen. |
| Blind or low vision (secondary) | Can press pads and turn knobs but cannot read the LEDs, the grid state or the small display. | Spoken readback of state (tempo, project, patches, what changed) and spoken confirmation of every action; audible cues for listening state. |

Speech impairments (dysarthria, slow speech) are not a v1 target but the timing
settings below are designed so that v1 can be tuned for them without code
changes.

### Workflows

**A. Build step by step, perform, save (primary).** The song lives in the MCP
server's internal sequencer, which plays the Circuit's synths and drums over
MIDI and sends clock so the device display follows. Priority set by the owner
on 2026-09-16: exact, incremental building by voice ("C3 E3 G3 auf Synth 1
in Pattern 1", "Kick auf 1, 5, 9, 13") and control of the device matter more
than one-shot composition; the composer is the secondary path.

1. Start from nothing with an empty song ("Neuer Song, 120 BPM"), from a
   saved song file ("Lade den Song Nachtfahrt"), or, secondary, from a brief
   for the composer ("Bau mir einen langsamen Techno-Beat in Moll").
2. Build precisely with direct tools: `write_notes` (note names or numbers
   spread over steps on a synth track), `write_drums` (hits on named steps),
   `clear_track`, `new_song`. These are deterministic MCP calls, instant, no
   model in between. The composer step (`compose`/`revise`) handles fuzzy
   requests and loads its result with `load_song` / `set_track`.
3. Perform: start, stop, tempo, mute and unmute tracks, queue patterns, shape
   sounds and FX, ask what is playing. All of this is real state the internal
   engine reports, which is exactly the readback blind users need.
4. Revise ("Mach die Hi-Hats leiser", "Füg eine Bassline dazu") through the
   composer step without stopping the music where possible.
5. Save ("Speicher das in Slot fünf als Nachtfahrt") with
   `export_song_to_project` after one confirmation, then optionally switch the
   device to that slot and play it from the hardware.

**B. Perform stored projects.** Select a project on the device, start and stop
its transport, change tempo through our clock, shape sounds, mute via levels.
No composition involved; readback comes from the agent's ledger because the
device does not report its state.

Only one playback source runs at a time; the agent stops one before starting
the other (see "Playback source and conflicts").

### User stories with acceptance criteria

All utterances are examples; the model must handle free phrasing, not a
grammar. Times are measured from end of speech to the first MIDI message.

1. **Start performing.** "Spiel Projekt drei." → `select_project` then
   `transport("start")`; music within 1.5 s (p50); agent answers "Projekt drei
   läuft, 120 BPM."
2. **Stop.** "Stopp." / "Halt." / "Aus." → `transport("stop")` within 1 s;
   short confirmation.
3. **Tempo.** "Etwas schneller." / "Tempo auf 118." → clock tempo changes
   without restarting the sequencer; agent says the new BPM.
4. **Sound shaping.** "Mach den Filter auf Synth eins auf." /
   "Mehr Resonanz." / "Dreh Makro fünf auf achtzig." → `set_synth_params` or
   `set_macro` with a sensible delta when no number is given (default step 20
   on a 0–127 scale, remembered per parameter); agent confirms with the value.
5. **FX and mixer.** "Mehr Hall auf den Drums." / "Delay auf Synth zwei aus." /
   "Synth eins leiser." → `set_project_params`; confirmation.
6. **Patches.** "Nimm Patch zwölf auf Synth zwei." → `select_patch`; the agent
   reads the patch name back via `get_synth_patch` when asked "Wie heißt der?".
7. **Mute.** "Schalte die Drums stumm." / "Drums wieder an." → virtual mute
   (see tools); confirmation.
8. **Readback (blind users).** "Was läuft gerade?" → spoken summary. In
   workflow A it comes from `get_sequencer_status` (running, pattern, step,
   BPM, mutes, queue, patterns defined); in workflow B from the agent's ledger
   (project, running or stopped, BPM, muted tracks, last change). No SysEx
   read is needed for this.
9. **Undo.** "Rückgängig." → the last parameter change is reverted when the
   previous value is known; otherwise the agent says it cannot.
10. **Help.** "Was kann ich sagen?" → a short spoken list of command families,
    not a manual.
11. **Listening control.** Press-and-hold switch to talk (default), toggle
    mode, or open microphone; audible cue on listen start and stop; the agent
    never acts on audio captured while not listening.
12. **Long session.** 30 minutes of use with connection drops handled
    invisibly; the state ledger and conversation survive reconnects.
13. **Safety.** Changing project after the agent has changed sounds in this
    session asks once ("Projekt wechseln? Die Änderungen an den Sounds gehen
    verloren."), because the device discards unsaved edits. Without such
    changes it switches straight away; while playing it uses the device's
    queued change so the switch lands on the pattern boundary.
14. **Compose.** "Bau mir einen langsamen Techno-Beat in Moll, 118 BPM." → the
    agent says it is building ("Ich baue das, einen Moment.") and stays
    responsive; the composer step returns a valid song within about 30 s; the
    agent loads it, starts it if asked, and summarises: name, tempo, patterns,
    which tracks play.
15. **Revise.** "Die Hi-Hats leiser." / "Füg eine Bassline auf Synth zwei
    dazu." / "Mach den Chorus doppelt so lang." → the composer step changes
    only what was asked, as new patterns or track edits while the music keeps
    running where possible; the agent says what changed.
16. **Load a saved song.** "Lade den Song Nachtfahrt." / "Welche Songs gibt
    es?" → songs from the local songs folder are listed and loaded into the
    internal sequencer without composition.
17. **Save to the device.** "Speicher das in Slot fünf als Nachtfahrt." → the
    agent confirms once ("Slot fünf wird überschrieben. Speichern?"), exports
    with patches embedded, and reports success; "Spiel es vom Gerät" then
    switches the device to that slot (workflow B).

### Non-goals for v1

- Composing inside the voice loop: the live model never writes song JSON
  itself; it delegates to the composer step.
- Patch design from scratch (`create_synth_patch`), parameter morphs, drum
  sample names.
- Web Tracks as a target (designed for, not built; see roadmap).
- Wake word or always-on operation without headphones.
- A graphical UI beyond a terminal status view.
- Writing patches or raw project files to the device (`save_synth_patch`,
  `send_project_file`); `export_song_to_project` is the only storage write.

## 2. Research findings

### Model choice (**locked**: Gemini 3.8 Live via the google-genai SDK)

| Option | Fit | Notes |
| --- | --- | --- |
| **Gemini 3.8 Live** (`gemini-3.8-live`, stable Sept 2026) | Best | Native audio in and out over one WebSocket; barge-in; tool calling (async by default, `behavior: BLOCKING` available); 70 languages auto-detected, German via system instruction (native audio models do not take a language code); configurable VAD (`startOfSpeechSensitivity`, `endOfSpeechSensitivity`, `prefixPaddingMs`, `silenceDurationMs`) and manual `activityStart`/`activityEnd` for push-to-talk; input and output transcription; 131k input context; ephemeral tokens for browser clients later. Audio ≈ $0.005/min in, $0.018/min out; text $0.75/$4.50 per 1M; free tier exists. Extended-thinking variant available if tool selection needs more reasoning, at a latency cost. |
| OpenAI `gpt-realtime` / `gpt-realtime-2.1` | Possible | 60-minute sessions; remote MCP servers executed by the API itself (Streamable HTTP or SSE only, so it reaches the Agent Link relay but not a local stdio server without a tunnel); 32k context; audio ≈ $32/$64 per 1M tokens, roughly ten times Gemini. Good fallback if German quality on Gemini disappoints. |
| Cascaded pipeline (STT → text LLM → TTS) | Fallback | Best tool-calling models and full control over each stage, but three vendors, 2–3× latency, and interruption handling must be built by hand. Only if native audio recognition of German or of impaired speech proves insufficient. |
| Pipecat framework | Not for v1 | Provider abstraction, `MCPClient` (stdio/HTTP) and WebRTC transports come free, but it is a heavy dependency and hides the VAD and timing controls this project needs to tune. Revisit if a second provider is added. |

### Gemini Live facts that shape the design

- **Session limits.** Audio-only sessions are capped at 15 minutes unless
  context window compression is enabled; the WebSocket itself lives about 10
  minutes and announces its end with a `GoAway` message carrying `timeLeft`.
  Session resumption handles (`SessionResumptionUpdate`) stay valid for 2 hours.
  Both must be on from day one.
- **Tool responses are manual.** The SDK converts an `mcp.ClientSession` into
  function declarations for `live.connect`, but the caller must still route
  `toolCall` messages and call `send_tool_response`. The standard SDK adapter
  also rewrites `inputSchema` through `Schema.from_json_schema`, which does not
  preserve `$ref`/`$defs`. Fifteen hardware tools use those, including
  `load_song` (18.9k characters alone; all 46 tools ≈ 56k characters ≈ 14k
  tokens). Decision: the bridge builds its own `FunctionDeclaration` per curated
  tool and passes `inputSchema` through `parameters_json_schema`, which the API
  accepts as full JSON Schema.
- **Interruptions.** `serverContent.interrupted` marks a cut-off answer; the
  harness must flush queued playback immediately.
- **Audio format.** 16-bit PCM, 16 kHz mono in; 24 kHz out.
- **Language.** No `languageCode` on native audio models; the system
  instruction fixes German and the model follows the speaker's language
  otherwise, which gives English as a free fallback.
- **Asynchronous tools.** Function declarations default to `NON_BLOCKING` on
  3.8 Live; the tool response carries `scheduling` (`INTERRUPT`, `WHEN_IDLE`,
  `SILENT`). This is how the composer step fits a voice loop: the live model
  says it is working, the user can still say "Stopp" or "leiser", and the
  finished result interrupts with a summary. Fast tools stay `BLOCKING` so
  confirmations report real results.

### Composer model

Composition is a separate step with its own model, chosen for output quality
on a 17k-character JSON schema rather than for latency. It receives the German
brief or revision, the current song JSON when revising, the song JSON schema
from `get_song_json_schema()`, the `song_format` and `best_practices`
sections of the parameter reference, and the house rules already learned on
this project (same pattern length everywhere, 32 steps with micro-steps for
automation, always include `sounds`, add layers as new patterns rather than
overwriting). It returns structured JSON validated by `SongSchema`; validation
errors go back to the composer for up to two retries before the agent reports
failure in one sentence.

| Option | Default? | Notes |
| --- | --- | --- |
| Gemini 3.8 text model via `generate_content` with `response_json_schema` | Yes (Flash by default, Pro behind `--composer-model` for quality) | Same SDK and key as the live session; structured output enforces the schema shape. |
| Claude via the Anthropic SDK | Optional (`--composer-model anthropic:<model>`) | The current Claude Code workflow already proves Claude composes well with these exact tools; useful comparison and fallback. |
| `gemini-3.8-live-extended-thinking` inside the voice loop | No | Keeps everything in one session but puts 18k characters of schema and a long generation on the latency path; rejected for v1. |

The composer is a plain function `compose(brief, current_song) -> Song` behind a
small interface, so the model is a flag, not an architecture decision.

### MCP client facts

- `mcp` 2.x ships `mcp.client.Client`, which accepts an in-process
  `MCPServer` instance, a `Transport`, or a URL string. The voice agent can run
  the hardware server in-process (import `circuit_mcp.server.mcp`), which
  removes a subprocess hop, or point at a Streamable HTTP URL such as an Agent
  Link relay session for Web Tracks later with no bridge changes.
- The hardware server's `set_bpm`, `mute_track`, `queue_patterns` and
  `get_sequencer_status` act on the server's **internal** pattern engine (songs
  defined by `set_pattern`/`load_song`), not on the project playing on the
  device. Device-level control is `transport`, `select_project`,
  `select_patch`, `set_synth_params`, `set_drum_params`, `set_project_params`,
  `set_macro`, `play_notes`, `play_drum`, plus SysEx reads (`get_synth_patch`,
  `read_project`). v1 uses both: the internal engine for workflow A, the
  device-level tools for workflow B, and the sound-shaping tools in both.
- `export_song_to_project(slot, name)` writes the loaded song as an `.ncs`
  project over SysEx into slot 0–63 with the `sounds` patches embedded; the
  name is cut to 16 characters. Without an explicit slot it falls back to the
  slot last passed to `select_project`; the bridge always passes the slot
  explicitly. `read_project(slot)` pulls a device project back into the same
  song format and marks it as the current song, which allows "hol Projekt
  drei rein und ändere die Drums" later.
- `transport("start")` without a BPM reads the project tempo from the device
  via a full project SysEx dump, which costs noticeable time on the first
  start. The agent should cache the tempo per project.
- Tempo changes while the device runs from our clock need
  `ClockGenerator.set_bpm`, which exists in the library but has no MCP tool;
  `start_clock` on a running clock stops and restarts the clock thread (safe,
  but a short gap in the clock). Approved fix: a `set_clock_bpm` tool,
  library 0.3.1.
- `start_sequencer(pattern, bpm=120.0)` defaults to 120 BPM and overrides the
  tempo that `load_song` set on the engine. The bridge always passes the
  loaded song's BPM and its first pattern.
- Project changes on the device: while the sequencer runs, a project change
  waits for the current pattern to end (program change 64–127, "queued") and
  keeps the current tempo; loaded while stopped, the project brings its saved
  tempo. The agent therefore invalidates its cached BPM after a project change
  while stopped, and prefers queued changes while playing so the music never
  stumbles.

### Accessibility findings applied

- A mixed-methods study of voice assistants with 16 motor-, language- and
  cognitively impaired users (Masina et al., JMIR 2020) found 41% of failures
  were phrasing errors on long utterances and 41% timing errors (users did not
  wait for the system). Recommendations: adjustable timing, salient feedback
  after the listen cue, short familiar words. → configurable silence duration
  and sensitivity, distinct earcons, short German command vocabulary in the
  help text, no forced pauses.
- MeowCrophone (Goller, Fraser, Graßl, ITiCSE 2026), voice control of Scratch
  for children with motor disabilities: push-to-talk and toggle-to-talk were
  more reliable than always-listening; confirmations for critical actions,
  undo, explicit start/stop commands, visible "Recording" state; German
  baseline recognition was far worse than English on classic ASR (27% vs 65%)
  before post-processing. → PTT default, confirmation policy, undo ledger, and
  a German utterance eval set as a hard gate before calling German "fluent".
- Music-specific hazard: the Circuit plays through speakers while the user
  speaks. Open-mic mode with server VAD will trigger on the music and the model
  will hear it. → PTT default; open-mic mode warns to use headphones and enables
  Gemini's proactive audio so the model ignores audio not addressed to it.

## 3. Architecture

```
  USB switch / foot pedal / key      microphone                 headphones / speaker
              │                          │                                ▲
              ▼                          ▼                                │
   ┌──────────────────┐     ┌───────────────────────┐        ┌────────────┴─────────┐
   │ listen controller │────▶│ audio in (sounddevice) │        │ audio out (24 kHz)   │
   │ PTT / toggle / VAD│     │ 16 kHz PCM chunks     │        │ flush on interrupted │
   └──────────────────┘     └───────────┬───────────┘        └────────────▲─────────┘
                                        ▼                                 │
                             ┌──────────────────────────────────────────┐ │
                             │ session manager  (google-genai, WSS)      │─┘
                             │ resumption · compression · GoAway · retry │
                             └───────────────┬──────────────────────────┘
                                  toolCall   │   ▲ send_tool_response
                                             ▼   │
                             ┌──────────────────────────────────────────┐
                             │ tool bridge                              │
                             │ curated cards → FunctionDeclaration      │
                             │ 1-based ↔ 0-based · virtual tools        │
                             │ confirmation policy · state ledger · undo│
                             └───────┬───────────────────┬──────────────┘
                                     │ compose / revise  │
                                     ▼                   │
                             ┌──────────────────┐        │
                             │ composer step    │        │
                             │ separate model,  │        │
                             │ SongSchema JSON, │        │
                             │ validate + retry │        │
                             └────────┬─────────┘        │
                                      │ load_song / set_track
                                      ▼                  ▼  mcp.client.Client
                             ┌──────────────────────────────────────────┐
                             │ circuit_mcp.server.mcp (in-process)      │
                             │ or stdio subprocess or Streamable HTTP   │
                             └───────────────┬──────────────────────────┘
                                             ▼ MIDI (mido / rtmidi)
                                       Circuit Tracks
```

### Modules

| Module | Responsibility | Depends on |
| --- | --- | --- |
| `audio` | Capture 16 kHz mono int16 frames; play 24 kHz PCM; flush; device selection; earcons. | sounddevice, numpy |
| `listen` | Listening state machine: PTT hold, toggle, open mic; emits `activityStart`/`activityEnd`; global hotkey via pynput; on-screen and audible cues. | audio |
| `session` | Owns the Live connection: config (German system instruction, voice, VAD, transcription, compression, resumption), reconnect on `GoAway`/errors, routes `toolCall` to the bridge, exposes an event stream (transcripts, tool calls, latencies). | google-genai, bridge |
| `bridge` | Curated tool cards → function declarations; argument normalisation (1-based numbers, German aliases → track ids); virtual tools (`compose`, `revise`, `load_saved_song`, `save_to_device`, `mute_track`, `get_state`, `undo_last`, `set_listening`); confirmation policy; state ledger with previous values; playback-source rule. | mcp client, composer |
| `composer` | `compose(brief, current_song) -> Song`: prompt assembly (schema, reference sections, house rules), structured-output call to the composer model, `SongSchema` validation with retry, revision as incremental `set_track` edits where possible; pluggable model behind one interface. | google-genai (optionally anthropic), circuit_tracks.song_schema |
| `mcp` | Connect to the server in-process (default), stdio, or Streamable HTTP; `list_tools`, `call_tool`, timeouts. | mcp, circuit-tracks-tools[mcp] |
| `ui` | Terminal status: listening state in large type, last transcript, last action, latency; `--quiet` for none. | session events |
| `cli` | Flags and env → config dataclass; runs the loop. | all |

No cycles: `cli → session → bridge → mcp`, `bridge → composer → mcp`,
`cli → listen → audio`, `ui` consumes events only.

### Session lifecycle

1. Connect to MIDI (auto-detect the port containing "Circuit Tracks", override
   with `--midi-port`), then to the MCP server, then open the Live session
   with `response_modalities=["AUDIO"]`, `context_window_compression`
   (sliding window), `session_resumption`, `input_audio_transcription` and
   `output_audio_transcription` enabled.
2. Greet in German, briefly ("Bereit. Halte die Taste und sprich.").
3. Loop: forward mic frames while listening; play model audio; on `toolCall`
   run the bridge and send the response; on `interrupted` flush playback.
4. On `GoAway` or socket close: reconnect with the last resumption handle,
   replay nothing (the server keeps the conversation), keep the local ledger.
5. On unrecoverable failure: say so in German, keep MIDI connected, allow the
   operator to retry with one key.

### Listening modes

| Mode | Gemini config | When to use |
| --- | --- | --- |
| `ptt` (default) | `automaticActivityDetection.disabled=true`; send `activityStart` on press, stream audio, `activityEnd` on release. | Loud room, speakers on, single-switch users. |
| `toggle` | Same as `ptt`, one press starts and the next stops; auto-stop after `--max-utterance` seconds (default 180). | Users who cannot hold a switch. |
| `open` | Server VAD with `endOfSpeechSensitivity=LOW`, `silenceDurationMs` from `--silence-ms` (default 1200, range 500–3000), `proactivity.proactiveAudio=true`. Prints and speaks a headphones warning once. | Headphones, quiet room, users who cannot operate any switch. |

Earcons: rising two-tone on listen start, falling on stop, short tick on tool
success, buzz on error. All audio cues have a text twin in the terminal.

### Switch hardware

The first users press a keyboard key. A dedicated button is being sourced; the
candidates below all enumerate as a USB or Bluetooth keyboard on macOS, so the
`pynput` global-hotkey path in `listen.py` serves every one of them and the
README only needs a "map your switch to the PTT key" section.

| Device | Type | Setup on the Mac | Rough price | Why it is on the list |
| --- | --- | --- | --- | --- |
| iKKEGOL single USB foot pedal | USB pedal | Driverless HID; ships mapped to a key, remappable | 18–26 USD | Cheapest sane test device; also works as a hand button on a table |
| PCsensor 1–3 key pedals | USB pedal | Mapping stored in the pedal; mapping software is Windows-only, then driverless | 25–40 USD | Multi-key variants can drive PTT plus a stop key |
| Elgato Stream Deck Pedal | USB, 3 pedals | Needs the Stream Deck app running to emit keys | ≈ 80 EUR | Well built; one more app to keep open |
| AbleNet Hitch 2 + any 3.5 mm switch (Big Red, Jelly Bean) | USB switch interface | Plug and play | 100–150 USD plus switch | The standard assistive-tech route; users often already own a switch that fits their body |
| AbleNet Blue2 FT | Bluetooth, 2 switches, proximity option | Pairs as a keyboard; preset to space, enter, 1–4 | ≈ 270 GBP | Wireless and feather-touch for users who cannot apply force |

Default `--ptt-key` is the right Option key so that typing in another window
never triggers listening; pedals that cannot emit a modifier are mapped to
`f13` or `space` and the flag follows.

### Tool surface v1

Exposed to the model through cards in `circuit_voice/tool_cards.py`. A card
holds the MCP tool name, a voice-oriented description (English is fine for
the model; German aliases live in the system instruction), the argument
mapping, whether confirmation is required, the Gemini `behavior`
(`BLOCKING` for fast tools, `NON_BLOCKING` for the composer and SysEx reads),
and how the result is summarised for the ledger.

**Workflow A: internal sequencer (song built by the agent)**

| Spoken concept | Tool the model sees | Backed by | Notes |
| --- | --- | --- | --- |
| Empty song | `new_song(name, bpm)` | virtual → `load_song` with a blank 32-step pattern, chromatic scale, preset sounds | Starting point for step-by-step building. |
| Notes on a synth track | `write_notes(pattern, track, notes, start_step, spacing, gate, velocity)` | virtual → `set_track` + song JSON | "C3 E3 G3 auf Synth 1 in Pattern 1". Note names as the device labels them (C3 = MIDI 60). Appends by default, `replace=true` clears first. |
| Drum hits | `write_drums(pattern, track, steps, velocity)` | virtual → `set_track` + song JSON | "Kick auf 1, 5, 9, 13" (spoken 1-based steps). |
| Clear a track | `clear_track(pattern, track)` | virtual → `set_track` with no steps | |
| Build a song (secondary) | `compose(brief)` | virtual → composer step → `load_song` | NON_BLOCKING; agent announces work, result interrupts with a summary. |
| Change the song | `revise(instruction)` | virtual → composer step → `set_track` / `set_pattern` / `load_song` | Incremental edits while running where possible; new layers as new patterns plus `queue_patterns`. |
| Saved songs | `list_saved_songs()`, `load_saved_song(name)` | virtual over `--songs-dir` → `load_song` | Song JSON files, the same format Claude Code produced so far. |
| Play, stop | `start_sequencer(pattern?, bpm?)`, `stop_sequencer()` | same | Clock sent so the device display follows. |
| Tempo | `set_bpm(bpm)` | `set_bpm` (internal engine) | Deltas resolved by the model from status. |
| Mute | `mute_track(track, muted)` | `mute_track` (internal engine) | Real state, real readback. |
| Arrangement | `queue_patterns`, `set_song`, `clear_queue`, `list_patterns` | same | "Nach dem Chorus die Bridge." |
| Status | `get_sequencer_status()` | same | The readback source for blind users in this workflow. |
| Save | `save_to_device(slot, name)` | virtual → `export_song_to_project(slot-1, name[:16])` | Always confirms once; reports slot and name; offers to play it from the device. |

**Workflow B: projects stored on the device**

| Spoken concept | Tool the model sees | Backed by | Notes |
| --- | --- | --- | --- |
| Start, stop, continue | `transport(action, bpm?)` | `transport` | BPM cached per project after first read. |
| Tempo | `set_tempo(bpm)` | `set_clock_bpm` (library 0.3.1) | Only meaningful while our clock drives the device. |
| Project 1–64 | `select_project(number, immediate?)` | `select_project(number-1, queued=not immediate)` | Queued while running, instant when stopped or on "sofort". Confirm only if the ledger holds unsaved sound changes. |
| Mute | `mute_track(track, muted)` | virtual: level parameters with remembered previous value | Same tool name as in workflow A; the bridge routes by playback source. |
| Bring a project in | `import_project(number)` | `read_project(number-1)` | Makes a device project the current song so workflow A tools can revise and re-save it. NON_BLOCKING (SysEx). |

**Both workflows: sounds and readback**

| Spoken concept | Tool the model sees | Backed by | Notes |
| --- | --- | --- | --- |
| Patch 1–64 on synth 1–2 | `select_patch(synth, number)` | `select_patch` | Name read via `get_synth_patch` on request. |
| Synth sound | `set_synth_params(synth, params)` | `set_synth_params` | The card embeds the most-used 20 parameters with German hints. |
| Drum sound | `set_drum_params(drum, params)` | `set_drum_params` | |
| FX, mixer, master filter | `set_project_params(params)` | `set_project_params` | |
| Macros | `set_macro(synth, macro, value)` | `set_macro` | |
| Audition | `play_notes`, `play_drum` | same | |
| Patch name | `get_synth_patch(synth)` | same | Read-only, slow (SysEx), NON_BLOCKING. |
| State | `get_state()` | virtual: ledger plus `get_sequencer_status` when the internal engine is the source | |
| Undo | `undo_last()` | virtual: ledger | Parameter changes and mutes; not compositions (use `revise`). |
| Listening | `set_listening(mode)` | virtual | Switch modes by voice. |

Everything else in the hardware server stays hidden from the model. The
allowlist is data; the `--tools` flag selects a profile (`full` by default,
`perform` without the composer for demos where composition is not wanted).

### State ledger

A small in-memory record: playback source (`none`, `internal`, `device`),
current song name and whether it has been saved since the last change, target
slot if known, current device project, running flag, BPM, per-track mute with
saved levels (device mode), last N parameter changes with previous values
(when known), last tool result, composer job in flight. It feeds `get_state`,
`undo_last`, confirmation decisions and the spoken summaries. It is rebuilt
from scratch on process start and survives Live reconnects.

### Playback source and conflicts

The internal engine and the device transport both make the Circuit sound, and
running both at once produces a mess. Rule, enforced in the bridge and stated
in the prompt: one source at a time. Starting the internal sequencer while the
device transport runs first sends `transport("stop")`; starting the device
transport while the internal engine runs first calls `stop_sequencer`. The
agent says so in half a sentence ("Ich stoppe das Gerät und starte den
Song."). `save_to_device` stops the internal engine before writing: on
2026-09-16 a save during playback hung the device (project SysEx blocks
interleaved with clock and notes) until a hard restart. After a save, "Spiel
es vom Gerät" selects the slot and starts the device transport.

### Agent modes (added 2026-09-16 after the third hardware session)

Two modes, switchable by voice ("Steuermodus" / "Kreativmodus") and by
`--tools`, default `control`:

| Mode | Tools the model sees | Prompt rule |
| --- | --- | --- |
| `control` | Every direct, instant tool: notes, hits, tempo, mutes, macros, patches, projects, save, undo | Never invents music; answers "bau mir …" with a pointer to the creative mode or asks for exact notes and steps |
| `creative` | The same plus `generate_track`, `design_patch`, `compose`, `revise` | Says a model job takes a few seconds, stays responsive, summarises |

Switching rebuilds the declarations and reconnects the Live session with the
resumption handle, so the conversation continues.

### Milestones

Reprioritised on 2026-09-16 after the first hardware session: M1b (precise
editing) is required before M4; M2 (composer) is done but secondary.

| Milestone | Delivers | Acceptance |
| --- | --- | --- |
| **M0 Library 0.3.1** | `set_clock_bpm` tool, tests, changelog, release | `pytest` green; tempo change on a running clock audible on the device |
| **M1 Voice loop + internal sequencer + save** | `voice/` package: audio, PTT, session with resumption, bridge, ledger, workflow A tools without the composer, `--song`/`--songs-dir`, `save_to_device`, terminal UI | By voice: load a saved song, perform it (start, stop, tempo, mutes, queue, sound tweaks), ask what plays, save it to a named slot, hear it from the device. German eval set for these tools ≥ 90%. |
| **M1b Precise editing by voice** | `new_song`, `write_notes`, `write_drums`, `clear_track`; song JSON kept in sync so saves include the edits | "Neuer Song", "C3 E3 G3 auf Synth 1", "Kick auf 1, 5, 9, 13", start, save → the device plays exactly those notes |
| **M2 Composer step (secondary)** | `composer` module, `compose`/`revise` tools, NON_BLOCKING flow, validation and retries, composer eval on German briefs | "Bau mir …" produces a valid, playing song in ≤ 30 s p50; revisions change only what was asked; failure is reported in one sentence |
| **M3 Device projects** | Workflow B tools, BPM cache, virtual mute, playback-source rule, `import_project` | Select and perform stored projects by voice, switch between workflows without audio pile-ups |
| **M4 Hardening** | Accessibility checklist, README with switch mapping and permissions, events log review, CI job | All "must" items in Appendix B pass; setup ≤ 10 min by a helper |

M1 before M2 on purpose: the voice loop, timing and save path are validated
with existing song files before the composer adds a second model and its own
failure modes.

### German language design

- System instruction in German (draft in Appendix A): role, device vocabulary,
  numbering rules, brevity rules, confirmation rules, verbosity modes
  ("ausführlich" default, "kurz" on request), never invent state, always say
  what changed.
- Voice: any prebuilt voice (all are multilingual); default `Kore`,
  configurable with `--voice`.
- Verbosity: `ausführlich` speaks the resulting value after every action;
  `kurz` answers with one or two words. Both switchable by voice.
- Numbers: the user speaks 1-based; the bridge converts for project and patch.
  Parameter values stay 0–127 and the agent may also accept percentages
  ("auf achtzig Prozent") and map them.
- Follow the speaker: German is the default and the eval language; someone
  speaking English gets English answers without any flag. English music
  loanwords inside German sentences ("mach den Cutoff auf", "mehr Reverb") are
  expected and listed in the prompt.
- Audio output: system default device; `--output-device` overrides.

## 4. Tech stack

| Component | Choice | Version |
| --- | --- | --- |
| Language | Python | 3.11+ (matches the repo) |
| Model SDK | `google-genai` | ≥ 2.23 |
| Composer model | Gemini 3.8 text model through the same SDK with structured output (Flash default, Pro optional); `anthropic` SDK as an optional extra for Claude | — |
| MCP client | `mcp` | ≥ 2, < 3 |
| Tools | `circuit-tracks-tools[mcp]` | ≥ 0.3 (path dependency during development) |
| Audio I/O | `sounddevice` + `numpy` | 0.5.x (PortAudio) |
| Global hotkey | `pynput` | latest; needs macOS Accessibility permission, documented in README |
| Packaging | hatchling, own version and CHANGELOG under `voice/` (same pattern as `link/`) | |
| Tests | pytest, pytest-asyncio | |
| Lint | ruff (repo config, line length 120) | |

The API key comes from `GEMINI_API_KEY`; the program never logs it. No browser,
no server, no ephemeral tokens in v1.

## 5. Commands

```sh
# install (from repo root, inside venv/)
pip install -e ".[mcp]" && pip install -e "./voice[dev]"

# run against the hardware (in-process MCP server, PTT on the right Option key)
circuit-voice --ptt-key alt_r
# start with a song already loaded, and a folder the user can pick songs from by voice
circuit-voice --song docs/journal/ethereal-drift-song.json --songs-dir ~/circuit-songs
# composer model and tool profile
circuit-voice --composer-model gemini-3.8-pro          # or anthropic:<model>
circuit-voice --tools perform                          # no composer, demo mode
# other modes and knobs
circuit-voice --mode toggle --silence-ms 1500 --voice Kore --output-device "Headphones"
circuit-voice --mcp stdio:circuit-tracks-mcp            # subprocess instead of in-process
circuit-voice --mcp https://link.webtracks.namirsab.dev/mcp/<s>/<secret>   # Web Tracks (v2)
circuit-voice --list-devices                            # audio devices
circuit-voice --dry-run "Spiel Projekt drei"            # text in, tool calls out, no audio (uses API)

# test and lint
pytest voice/tests
CIRCUIT_VOICE_EVAL=1 pytest voice/tests/eval -q          # German utterance eval, needs GEMINI_API_KEY
ruff check voice && ruff format --check voice
```

## 6. Project structure

```
voice/
  pyproject.toml            → distribution "circuit-voice", console script circuit-voice
  README.md                 → setup, macOS permissions, switches and pedals, modes, German command families
  CHANGELOG.md              → independent of the library, the webapp and the relay
  circuit_voice/
    __init__.py             → __version__
    __main__.py / cli.py    → flags, env, config dataclass, entry point
    audio.py                → capture, playback, earcons, device listing
    listen.py               → PTT / toggle / open state machine, hotkey
    session.py              → Live connection, config, reconnect, event stream
    bridge.py               → cards → declarations, call routing, normalisation, source rule
    tool_cards.py           → the curated allowlist (data), tool profiles
    composer.py             → compose/revise: prompt assembly, structured output, validate, retry
    prompt_composer.py      → composer instructions and house rules
    songs.py                → saved-song folder: list, load, name matching
    ledger.py               → state ledger, undo
    mcp_client.py           → in-process / stdio / http connection
    prompt_de.py            → system instruction and German aliases
    ui.py                   → terminal status view
  tests/
    test_bridge.py          → declarations golden file, 1-based mapping, aliases, source rule
    test_composer.py        → prompt assembly, validation retry loop with a fake model, revision as set_track edits
    test_songs.py           → folder listing, fuzzy name match ("Nachtfahrt" vs "nachtfahrt.json")
    test_ledger.py          → mute save/restore, undo, summaries, unsaved flag
    test_listen.py          → state machine, activity signals, auto-stop
    test_session.py         → fake Live server: tool call round trip, GoAway/resume, interrupted flush
    test_end_to_end.py      → fake Live + in-process MCPServer + MockMidi → MIDI assertions
    fixtures/declarations.json
    eval/
      utterances_de.jsonl   → ~40 German utterances with expected tool + args
      briefs_de.jsonl       → ~15 German composition briefs and revisions with checkable properties
      test_eval.py          → opt-in, text-mode live session with the real tool set
      test_composer_eval.py → opt-in, real composer model: validity, rules, property checks
docs/specs/voice-agent.md   → this document
tasks/plan.md, tasks/todo.md → produced by the planning phase
```

The library change (`set_clock_bpm`) lives in `src/circuit_mcp/server.py` with
a test in `tests/test_mcp_server.py`, released as patch 0.3.1 of
`circuit-tracks-tools`.

## 7. Code style

Follow the repo's ruff config and the style of `link/webtracks_link/server.py`:
module docstring first, `from __future__ import annotations`, type hints,
dataclasses for config and state, async for I/O, plain functions over classes
unless state is owned. Tool cards are data, not code:

```python
"""Curated tools the voice agent exposes to the model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class ToolCard:
    name: str                       # name the model sees
    mcp_tool: str | None            # hardware tool, or None for virtual tools
    description: str                # voice-oriented, one or two sentences
    confirm_when: Callable[[dict[str, Any], "Ledger"], str | None] = lambda a, l: None
    normalise: Callable[[dict[str, Any]], dict[str, Any]] = lambda a: a
    summarise: Callable[[dict[str, Any], Any], str] = lambda a, r: str(r)
    read_only: bool = False
    schema_override: dict[str, Any] | None = None


def _one_based(key: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda args: {**args, key: int(args[key]) - 1}


SELECT_PROJECT = ToolCard(
    name="select_project",
    mcp_tool="select_project",
    description="Switch the Circuit Tracks to project `number` (1-64). Unsaved edits on the device are lost.",
    confirm_when=lambda a, ledger: "Projekt wechseln? Ungespeicherte Änderungen gehen verloren." if ledger.running else None,
    normalise=_one_based("number"),
    summarise=lambda a, r: f"Projekt {a['number']} gewählt",
    schema_override={"type": "object", "properties": {"number": {"type": "integer", "minimum": 1, "maximum": 64}}, "required": ["number"]},
)
```

Conventions: snake_case everywhere; German strings only in `prompt_de.py` and
in `summarise` results that are spoken; log lines in English; no bare
`except`; every tool call, transcript and latency goes through one structured
logger (`circuit_voice.events`) as JSON lines.

## 8. Testing strategy

| Level | What | How |
| --- | --- | --- |
| Unit | Bridge declarations, 1-based mapping, alias resolution, ledger save/restore/undo, listen state machine, PCM resampling and chunking | pytest, pure functions, a golden `declarations.json` that fails when a card changes |
| Integration | Session manager against a **fake Live server** (in-process WebSocket or injected async iterator) that scripts `toolCall`, `interrupted`, `GoAway` and `sessionResumptionUpdate` | pytest-asyncio; asserts `send_tool_response` payloads, reconnect with handle, playback flush |
| End to end (no network) | Fake Live + real `circuit_mcp.server.mcp` in-process + `MockMidi` from `tests/test_mcp_server.py` | Script "select_project(3) → transport(start)" and assert the exact MIDI messages |
| Model eval (opt-in) | German utterance set → expected tool and arguments | Text in, text out, real tool declarations, `CIRCUIT_VOICE_EVAL=1`; report accuracy; gate ≥ 90% before release |
| Composer eval (opt-in) | German briefs and revisions → songs | Real composer model; assert `SongSchema` validity, equal pattern lengths, `sounds` present, requested BPM and scale, revisions keep untouched tracks byte-identical; report first-try validity and wall time |
| Manual acceptance | Hardware, headphones and speakers, at least two native German speakers, one switch user if possible | Checklist in Appendix B; latency measured by the events log |
| CI | `voice` job in `.github/workflows/test.yml` running unit, integration and end-to-end tests with a PortAudio stub | No API key in CI; eval runs locally |

Coverage target: the bridge and ledger at 100% of branches (they decide what
hits the device); overall ≥ 85% excluding `audio.py` device code.

## 9. Boundaries

**Always**
- Keep the API key in the environment; never print, log or store it.
- Run `pytest voice/tests` and `ruff check voice` before every commit.
- Store no audio. Transcripts and tool logs stay local, in a file the user
  chose, off with `--no-log`.
- Convert 1-based spoken numbers in the bridge, never in the prompt alone.
- Speak a confirmation or the error after every tool call; never act silently.
- Confirm once, with slot and name, before `export_song_to_project`; it
  overwrites the slot.
- Validate every composer result with `SongSchema` before it reaches
  `load_song`; never load unvalidated JSON.
- Stop the other playback source before starting one.
- Default to push-to-talk; warn about speakers when switching to open mic.
- Keep `voice/` on its own version and changelog; never bump the library for
  voice changes.
- Follow the memory rules already in place for the hardware (no CC drum sample
  select, macros add to base values).

**Ask first**
- Adding or changing tools in `circuit-tracks-tools` beyond the approved
  `set_clock_bpm` tool (release 0.3.1).
- Adding dependencies beyond the stack table.
- Changing CI (`test.yml`) beyond adding the `voice` job.
- Exposing further tools that write to device storage (`save_synth_patch`,
  `send_project_file`, `set_drum_sample_names`); `export_song_to_project` is
  in scope behind confirmation.
- Changing the composer's house rules or dropping validation retries.
- Switching the default model or provider, or enabling extended thinking by
  default.
- Any browser or server component (ephemeral tokens, Web Tracks UI).

**Never**
- Commit secrets or the events log.
- Send tool calls for audio captured outside a listening window.
- Let the model call tools that are not in the allowlist, even if the MCP
  server exposes them.
- Save to a slot the user did not name, or without the confirmation.
- Let the live model emit song JSON itself.
- Remove or weaken the confirmation for project changes while running.
- Delete or skip failing tests to get green.

## 10. Success criteria

Measured on the hardware with the events log unless stated.

| # | Criterion | Target |
| --- | --- | --- |
| 1 | End of speech → first MIDI message, simple commands (stop, tempo, macro, mute) | p50 ≤ 1.5 s, p95 ≤ 3.0 s |
| 2 | End of speech → start of spoken reply | p50 ≤ 1.2 s |
| 3 | German utterance eval: correct tool and arguments | ≥ 90% of ~40 cases |
| 4 | Continuous session with music playing, PTT mode | 30 min without user-visible failure; reconnects logged only |
| 5 | Unconfirmed project change while running | 0 occurrences in eval and manual tests |
| 6 | Tool calls outside a listening window | 0 |
| 7 | Every state-changing call followed by a spoken confirmation | 100% in manual checklist |
| 8 | Setup by a helper on a fresh Mac | ≤ 10 minutes following README (permissions, device pick, first "Spiel Projekt eins") |
| 9 | Cost per hour of use at Gemini list prices, including composer calls | ≤ $2.00 (about 60 min audio in, ≤ 15 min audio out, tool text, ≤ 10 compositions) |
| 10 | Accessibility checklist (Appendix B) | All "must" items pass |
| 11 | Composer: brief → valid song loaded and playing | p50 ≤ 30 s, p95 ≤ 60 s; first-try `SongSchema` validity ≥ 80%, after retries ≥ 95% |
| 12 | Composer eval: requested tempo, scale and instrumentation present; revisions leave untouched tracks unchanged | ≥ 90% of briefs |
| 13 | Save path: voice → confirmation → project on the device that plays back from the hardware with the intended patches | 10 of 10 manual runs |
| 14 | Voice loop stays responsive during composition (stop, mute answered within the normal latency budget) | 100% in manual tests |

## 11. Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| German recognition or tool selection weaker than English on native audio | Core value fails | Eval set as a release gate; extended-thinking variant behind a flag; cascaded pipeline fallback documented |
| Music bleeding into the mic | False triggers, wrong commands | PTT default; open mic requires acknowledgement; proactive audio |
| WebSocket 10-minute lifetime | Dropped mid-performance | Resumption + compression from day one; integration test for `GoAway` |
| First `transport("start")` reads a full project dump | Slow first start | Cache BPM per project; pass BPM when known; measure |
| No device-level tempo tool | "Schneller" restarts playback | Approved: `set_clock_bpm` in library 0.3.1, first task of the plan |
| `start_sequencer` default BPM overrides the loaded song's tempo | Song plays at 120 instead of its tempo | Bridge passes the ledger's BPM and first pattern; end-to-end test asserts the clock interval |
| Model invents state ("läuft mit 120") | Blind users misled | `get_state` from ledger; prompt forbids guessing; summaries come from tool results |
| macOS Accessibility permission for global hotkeys | Setup friction | README with screenshots; terminal-focus fallback key |
| Circuit numbering (1-based on the device vs 0-based in MIDI) | Wrong project chosen | Verified in the user guide (slots 1–64, patches 1–64); bridge converts; golden test covers both ends of the range |
| Cost creep from long open-mic sessions | Surprise bills | Session budget flag `--max-minutes`, hourly estimate in the status view |
| Composer produces invalid or rule-breaking songs (mixed pattern lengths, missing `sounds`, wrong p-lock keys) | Silent bad exports, init patches on the device | `SongSchema` validation plus house-rule checks before `load_song`; retries with the error text; composer eval as a gate |
| Composition too slow for a conversation | User thinks the agent froze | NON_BLOCKING tool with spoken start and interrupt on completion; Flash default; time budget in the eval |
| Both playback sources running | Doubled notes, clock fights | Bridge-enforced source rule; end-to-end test for each switch direction |
| Overwriting the wrong project slot | Lost work on the device | Explicit slot from the user, 1-based conversion tested, one confirmation naming slot and name |

## 12. Resolved questions (2026-09-16, with the owner)

1. **Tempo while running.** Add a `set_clock_bpm(bpm)` tool to
   `circuit-tracks-tools` that calls `ClockGenerator.set_bpm`. (Planning
   found that `start_clock` already stops the old thread before starting a
   new one, so no idempotency fix is needed; it merely leaves a short gap.)
   Ships as library patch release 0.3.1 before the voice agent's first
   milestone. The voice tool `set_tempo` maps to it.
2. **Switch hardware.** A keyboard key for now; a USB or Bluetooth button is
   being sourced. Everything on the shortlist (section 3, "Switch hardware")
   presents as a keyboard, so one `pynput` code path covers all of them. The
   default key is the right Option key; the README shows how to map a pedal to
   it or to pass `--ptt-key`.
3. **Audio output.** System default output for now, `--output-device` as an
   optional override. Headphones versus shared speakers will be worked out with
   real users later; nothing in the design depends on it.
4. **Language.** German by default, and the model follows the speaker's
   language otherwise. English music terms ("Filter", "Reverb", "Delay",
   "Cutoff") are accepted inside German sentences. The eval set is German only.
5. **Numbering.** Verified in the user guide: projects are "64 memory slots,
   arranged as two pages of 32" and the factory demo sits in "Memory Slot 1";
   patches are shown as 1–64 ("Patch 1 (Pad 1 on Page 1)", "Patch 33 (Pad 1
   on Page 2)"). The programmer's reference maps program change 0–63 to an
   instant project change and 64–127 to a queued one; patches are program
   change 0–63. The bridge converts spoken 1–64 to 0–63.
6. **Scope correction: internal sequencer and save are in v1.** The owner's
   intended workflow is to build music in the MCP server's internal sequencer
   first and save it to the device at the end. The earlier "live control only"
   answer meant "no composition inside the voice loop", not "no composition".
   Composition is therefore a separate step (`compose`/`revise` behind
   NON_BLOCKING tools) that may use a different model than the live session,
   and `export_song_to_project` is exposed behind one confirmation. Milestones
   M0 to M4 order the work: internal sequencer and save first, composer
   second, device projects third.

## 13. Roadmap after v1

- **v1.1 Web Tracks**: `--mcp https://…` against an Agent Link session and a
  `webtracks` tool profile; the internal-sequencer workflow maps almost one to
  one because Web Tracks implements the same tool names. An in-browser variant
  with ephemeral tokens once it is worth it.
- **v1.2 Patch design by voice**: a `design_patch(brief)` step on the composer
  model that drives `create_synth_patch` and `save_synth_patch` behind
  confirmation.
- **v1.3 Speech-impairment tuning**: per-user profiles for silence duration,
  sensitivity, verbosity and confirmation thresholds; evaluation with users.
- **Later**: OpenAI Realtime provider behind the same session interface;
  wake word via an on-device model; morphs and sidechain automation by voice.

## Appendix A: draft system instruction (German)

```
Du bist die Stimme eines Novation Circuit Tracks. Du hilfst Menschen, die die
Pads und Regler nicht selbst bedienen können oder nicht sehen können, live
Musik zu machen. Du sprichst Deutsch, kurz und freundlich, ohne Fachjargon,
wenn es nicht nötig ist. Antworte in der Sprache, in der du angesprochen wirst,
Deutsch ist die Standardsprache.

Regeln:
- Handle sofort mit dem passenden Werkzeug, statt Fragen zu stellen, wenn der
  Wunsch klar ist. Frage nur nach, wenn eine Angabe fehlt, die du brauchst.
- Sage nach jeder Aktion kurz, was du getan hast, mit dem neuen Wert
  ("Tempo ist jetzt 118."). Erfinde keine Zustände: Was du nicht weißt, fragst
  du über get_state ab oder sagst, dass du es nicht weißt.
- Projekte und Patches sind von 1 bis 64 nummeriert, Synths sind 1 und 2,
  Drums 1 bis 4. Sprich diese Nummern so aus, wie Menschen zählen.
- Wörter, die Menschen benutzen: "Schlagzeug", "Drums", "Beat" = Drum-Spuren;
  "Bass" meistens Synth 2 (frage, wenn unklar); "Filter auf/zu" oder
  "Cutoff auf/zu" = filter_frequency höher/niedriger; "Hall" oder "Reverb" =
  reverb; "Echo" oder "Delay" = delay; "leiser/lauter" = level. Englische
  Fachwörter in deutschen Sätzen sind normal.
- Ohne Zahl änderst du in Schritten von etwa 20 auf der Skala 0 bis 127
  ("etwas" = 10, "viel" = 40). Prozentangaben rechnest du auf 0 bis 127 um.
- Vor einem Projektwechsel fragst du einmal nach, wenn in dieser Sitzung
  Sounds verändert wurden, weil diese Änderungen verloren gehen. Sonst
  wechselst du direkt. Während der Wiedergabe wechselt das Gerät am Ende des
  Patterns; "sofort" erzwingt den direkten Wechsel.
- "Rückgängig" nimmt die letzte Änderung zurück. "Kurz antworten" schaltet in
  den knappen Modus, "ausführlich" wieder zurück.
- Musik bauen oder ändern machst du nie selbst: Du rufst compose oder revise
  auf, sagst kurz, dass du daran arbeitest ("Ich baue das, einen Moment."),
  bleibst ansprechbar, und fasst danach zusammen, was entstanden ist: Name,
  Tempo, Patterns, welche Spuren spielen.
- Es läuft immer nur eine Quelle: entweder der interne Sequenzer oder das
  Gerät. Bevor du die andere startest, stoppst du die laufende und sagst es.
- Speichern auf das Gerät braucht einen Slot (1 bis 64) und einen Namen. Du
  fragst einmal nach ("Slot fünf wird überschrieben. Speichern?") und
  speicherst erst bei "ja". Danach bietest du an, es vom Gerät abzuspielen.
- Wenn ein Werkzeug einen Fehler meldet, sage in einem Satz, was nicht ging,
  und was die Person stattdessen sagen kann.
```

## Appendix B: accessibility acceptance checklist (v1)

Must:
- [ ] Every command works without looking at a screen or touching the device.
- [ ] Listening start and stop are audible and visible.
- [ ] Every state change is spoken back with the resulting value.
- [ ] Silence duration, sensitivity and utterance limit are configurable
      without code changes.
- [ ] Destructive action (project change while running) asks once, in German,
      and accepts "ja"/"nein".
- [ ] Undo exists for the last parameter change and says when it cannot.
- [ ] Saving to the device names slot and project and waits for "ja".
- [ ] While the composer works, the agent has said so and still answers
      "Stopp" and "leiser".
- [ ] Help lists command families in under 20 seconds of speech.
- [ ] A failed connection or tool error is spoken, not only logged.
- [ ] No audio is stored; transcripts can be disabled.

Should:
- [ ] Works with headphones on one output and the Circuit on another.
- [ ] Works with a single USB switch as the only input device.
- [ ] English speakers get English answers automatically.

## References

- Gemini Live API overview: https://ai.google.dev/gemini-api/docs/live
- Gemini 3.8 Live model card: https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live
- Live API capabilities (VAD, voices, transcription): https://ai.google.dev/gemini-api/docs/live-guide
- Live API tool use: https://ai.google.dev/gemini-api/docs/live-tools
- Live API session management: https://ai.google.dev/gemini-api/docs/live-session
- Ephemeral tokens (for the browser variant later): https://ai.google.dev/gemini-api/docs/ephemeral-tokens
- Gemini pricing: https://ai.google.dev/gemini-api/docs/pricing
- google-genai SDK MCP adapter source: https://github.com/googleapis/python-genai/blob/main/google/genai/_mcp_utils.py
- OpenAI Realtime with MCP: https://developers.openai.com/api/docs/guides/realtime-mcp
- gpt-realtime model: https://developers.openai.com/api/docs/models/gpt-realtime
- Pipecat MCPClient: https://docs.pipecat.ai/api-reference/server/utilities/mcp/mcp
- Masina et al., "Investigating the Accessibility of Voice Assistants With Impaired Users", JMIR 2020: https://pmc.ncbi.nlm.nih.gov/articles/PMC7547392/
- Goller, Fraser, Graßl, "Voice-Controlled Scratch for Children with (Motor) Disabilities", ITiCSE 2026: https://arxiv.org/abs/2603.28246
- Web Tracks agent tools and Agent Link relay: `docs/webmcp-plan.md`, `link/README.md`
