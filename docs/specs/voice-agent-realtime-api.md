# Prototype B: Native Realtime Audio API (OpenAI Realtime or Gemini Live)

## Purpose
Measure latency and German-language accuracy for a voice-controlled circuit-tracks
agent built on a vendor's native audio-in/audio-out realtime API, which collapses
STT + LLM + TTS into a single bidirectional streaming session. This is one of two
comparable prototypes (see
[voice-agent-tool-runner.md](voice-agent-tool-runner.md)) built to decide the
production architecture for the accessibility voice agent.

## Background
Target users have limited/no arm mobility and control the agent via a single
switch-accessible button. See project memory `project_voice_to_notes` for prior
related work (record_melody/transcribe tools) and the session discussion that
preceded this spec for full context on the staged-vs-persist and barge-in
requirements — this prototype deliberately narrows scope to a single measurable
round trip and does not implement those yet.

The main hypothesis this prototype tests: a native realtime audio API removes a
pipeline stage (no separate STT call) and has built-in interruption/turn-taking
handling, which may better fit the one-button barge-in UX with less integration
work — at the cost of tying the agent to that vendor's tool-calling maturity and
model quality for this domain.

## Vendor choice
Pick **one** of the following for this prototype run (don't build both — that's
effectively a third prototype). Default to OpenAI Realtime API unless German
quality testing points the other way:
- **OpenAI Realtime API** — mature interruption handling, function calling
  support, widely documented.
- **Gemini Live API** — alternative if German transcription/generation quality
  or pricing looks better; verify current function-calling support before
  committing.

Record which vendor was used in the Results section — the numbers are not
transferable between them.

## Scope (deliberately minimal)
- **One round trip only**: button press → stream audio → one function/tool
  call → spoken confirmation. No multi-turn context beyond what the API session
  keeps by default, no staged/preview state, no persist-on-command step. Native
  barge-in handling may be exercised informally but is not the primary measured
  criterion here (that's a later-phase UX test, not this latency/accuracy
  comparison).
- **Trigger**: keyboard key (e.g. spacebar) press-and-hold stands in for the
  physical AT switch. Press opens/unmutes the audio stream, release closes it.
- **Tool set** (fixed, same across both prototypes, for a fair comparison):
  - `connect` — establish the MIDI connection to the Circuit Tracks. Called once
    at session start, not per-turn; not part of the measured round trip.
  - `list_midi_ports` — available to the model as a fallback if `connect`
    fails without a port name (e.g. multiple MIDI devices present), so it can
    ask the user or retry with the right port. Not expected to be called in
    the happy path.
  - `set_bpm`
  - `set_pattern`
  - `set_synth_params`
  - `play_notes`
- **Language**: German only for this test.
- **Test phrases** (identical to Prototype A, for a fair comparison):
  1. Vague/open-ended, e.g. "Mach den Beat schneller" (make the beat faster —
     requires the model to infer a concrete `set_bpm` delta or value).
  2. Precise, e.g. "Setz das Tempo auf 120 BPM" (set tempo to 120 BPM — direct
     mapping to `set_bpm`).

## Out of scope
- Real AT switch/HID hardware integration.
- Staged/preview song state and explicit persist step.
- Full circuit-tracks tool surface.
- Formal barge-in/interrupt latency measurement (note qualitative impressions
  only, if it comes up naturally).
- Production error handling / retry logic beyond what's needed to get a clean
  latency measurement.

## Architecture

```
(once, at startup) connect (+ list_midi_ports fallback) → MIDI link established
        ↓
[hold spacebar] → open realtime audio session, stream mic audio
        ↓ (vendor handles VAD/turn-detection internally)
   Realtime API (audio in) → (audio out)
     - session instructions: narrow, circuit-tracks domain only
     - tools: connect, list_midi_ports, set_bpm, set_pattern, set_synth_params, play_notes
        ↓ function call event received
        ↓ execute tool call against circuit-tracks MCP server (staging TBD later)
        ↓ send function result back into session
        ↓ model generates spoken confirmation directly as audio
   audio playback (streamed from session)
```

## Prerequisites (must be set up before implementation starts)
- [ ] API key/account for the chosen vendor (OpenAI Realtime API or Gemini Live
      API), with realtime/audio access enabled — verify this isn't behind a
      separate waitlist or tier from standard API access.
- [ ] Confirm current function-calling support and syntax for the realtime
      session type (this differs from the vendor's standard chat completion
      tool-calling format).
- [ ] Local mic input + audio playback working in the prototype's runtime
      environment, with support for the vendor's required audio format/sample
      rate for the realtime session.
- [ ] circuit-tracks MCP server running and reachable (existing `connect` tool),
      with the Circuit Tracks hardware powered on and its MIDI port known ahead
      of time so `connect` succeeds on the first call in the happy path.

## What to build
1. A standalone script (not inside Claude Code) using the vendor's realtime SDK:
   - Open a session with system instructions scoped to circuit-tracks + German.
   - Register the 4 fixed tools as session-level function definitions.
   - Stream mic audio in on key-hold; stream response audio out on release.
   - Handle the function-call event by executing against circuit-tracks MCP
     tools and returning the result into the session per that vendor's protocol.
2. Instrumentation: timestamp at (a) key release / end of audio stream in,
   (b) function-call event received, (c) function result sent back to session,
   (d) first response audio byte played. Log all four per trial.
3. A small harness to run both test phrases N times each (suggest N=10) and
   compute median/p95 latency per stage and end-to-end.

## Success criteria
Both latency and accuracy are tracked as co-equal criteria — this prototype is
not "successful" or "failed" on its own, it produces numbers to compare against
Prototype A.

- **Latency**: end-to-end time from key-release to first audio played back,
  broken down by stage where the API exposes timing (function-call detection /
  tool execution / response audio start), median and p95 across trials.
- **Accuracy**:
  - Did the model correctly understand the German phrase without a separate
    visible transcript to check against? (may need to request a text
    transcript alongside audio, if the API supports it, purely for grading)
  - Did it call the correct tool with correct/reasonable parameters for both
    the vague and precise phrasing?
  - Was the spoken confirmation correct, understandable, and in German?

## Open questions to resolve during implementation
- Does the realtime session's built-in turn-detection (VAD) work well with a
  push-to-talk model, or does it fight against the explicit button-press
  boundary (e.g. cutting off short pauses mid-sentence)?
- How much implementation complexity is added by the different function-calling
  event protocol compared to the standard chat completions tool-calling shape?
- Does the collapsed single-stage pipeline actually beat Prototype A's
  multi-stage latency in practice, or does the realtime session have its own
  overhead (e.g. connection setup, internal buffering) that erodes the
  theoretical advantage?
- Is per-minute realtime API pricing viable at expected usage volumes, compared
  to Prototype A's pay-per-stage pricing?

## Results
_To be filled in after running the prototype._
