// Sample-accurate sequencer. A worker timer wakes the scheduler ~every 25 ms;
// all audio events are scheduled on the AudioContext clock with a lookahead
// window, so actual timing is governed by the audio hardware clock, not JS
// timers.
import { SYNC_RATE_BEATS, PROBABILITY_LEVELS } from './constants.js';
import { ncsToMidi } from './scales.js';

const LOOKAHEAD = 0.15; // seconds scheduled ahead of the audio clock
const TICK_MS = 25;

function makeTimerWorker(callback, ms) {
  const src = `let id=null;onmessage=(e)=>{if(e.data==='start'){id=setInterval(()=>postMessage('tick'),${ms});}else if(e.data==='stop'&&id){clearInterval(id);id=null;}};`;
  const worker = new Worker(URL.createObjectURL(new Blob([src], { type: 'text/javascript' })));
  worker.onmessage = () => callback();
  return worker;
}

export class Sequencer {
  constructor(engine, synthTracks, drumEngine, app) {
    this.engine = engine;
    this.synthTracks = synthTracks; // [s1, s2, m1, m2]
    this.drums = drumEngine;
    this.app = app; // provides .project and .ui
    this.playing = false;
    this.bpm = 120;
    this.swing = 50;
    this.trackState = [];
    this.visualEvents = []; // {time, trackId, ...} for playhead/note display
    this.automationQueue = []; // timed macro moves (setMacro can't schedule ahead)
    this.sceneState = null;
    this.tapTimes = [];
    this.clickEnabled = false;
    this.clickLevel = 100;
    this.nextClickTime = 0;
    this.stoppedPositions = null;
    this.worker = makeTimerWorker(() => this.schedule(), TICK_MS);
  }

  get project() { return this.app.project; }

  setBpm(bpm) {
    this.bpm = Math.max(40, Math.min(240, Math.round(bpm)));
    this.project.tempo = this.bpm;
    this.engine.bpm = this.bpm;
    this.engine.delay.setBpm(this.bpm);
    // Tempo-synced LFO rates follow the BPM.
    for (const st of this.synthTracks) st.applyLfos?.();
    this.app.onTempoChanged?.();
  }

  setSwing(swing) {
    this.swing = Math.max(20, Math.min(80, Math.round(swing)));
    this.project.swing = this.swing;
  }

  // Tap tempo (guide p.86): at least 3 taps, BPM averaged over the last 5.
  tapTempo() {
    const now = performance.now();
    this.tapTimes = this.tapTimes.filter((t) => now - t < 3000);
    this.tapTimes.push(now);
    if (this.tapTimes.length >= 3) {
      const recent = this.tapTimes.slice(-5);
      const diffs = [];
      for (let i = 1; i < recent.length; i++) diffs.push(recent[i] - recent[i - 1]);
      const avg = diffs.reduce((a, b) => a + b, 0) / diffs.length;
      this.setBpm(60000 / avg);
    }
  }

  // Click track (metronome): a quarter-note tick (Shift+Clear toggles).
  scheduleClick(horizon) {
    if (!this.clickEnabled) return;
    const beatDur = 60 / this.bpm;
    while (this.nextClickTime < horizon) {
      const t = this.nextClickTime;
      const ctx = this.engine.ctx;
      const osc = ctx.createOscillator();
      const g = ctx.createGain();
      osc.frequency.value = 1500;
      const lvl = Math.pow(this.clickLevel / 127, 1.5) * 0.4;
      g.gain.setValueAtTime(lvl, t);
      g.gain.exponentialRampToValueAtTime(0.001, t + 0.03);
      osc.connect(g);
      g.connect(this.engine.masterIn);
      osc.start(t);
      osc.stop(t + 0.05);
      this.nextClickTime += beatDur;
    }
  }

  // The step currently sounding on a track (most recent scheduled step whose
  // time has passed) — used to place recorded knob automation.
  currentSoundingStep(trackId) {
    const ts = this.trackState[trackId];
    if (!ts) return null;
    const now = this.engine.now();
    let best = null;
    for (const h of ts.history) {
      if (h.time <= now && (!best || h.time > best.time)) best = h;
    }
    best = best ?? ts.history[0] ?? null;
    if (!best) return null;
    // Micro tick within the step (0-5), for micro-resolution knob recording.
    const pat = this.project.patterns[trackId][best.patIdx];
    const stepDur = this.stepDuration(pat.settings);
    const tick = Math.max(0, Math.min(5, Math.floor((now - best.time) / (stepDur / 6))));
    return { ...best, tick };
  }

  // Hardware behaviour: plain pattern select queues at the end of the current
  // pattern (handled at wrap); Shift+select switches immediately, preserving
  // the step position.
  switchPatternNow(trackId, patIdx) {
    if (!this.playing) return;
    const ts = this.trackState[trackId];
    ts.chain = [patIdx];
    ts.chainPos = 0;
    ts.patIdx = patIdx;
    const s = this.project.patterns[trackId][patIdx].settings;
    if (ts.step < s.playbackStart || ts.step > s.playbackEnd) ts.step = s.playbackStart;
    ts.tieConsumed.clear();
  }

  // Chain for a track: explicit pattern chain if set, else the UI-selected pattern.
  chainFor(trackId) {
    const chain = this.project.patternChains[trackId];
    if (chain && (chain.start !== 0 || chain.end !== 0)) return this.range(chain.start, chain.end);
    return [this.app.ui.currentPattern[trackId]];
  }

  // Selecting a scene loads its stored chains into the live pattern chains
  // (guide p.81: an all-zero entry means pattern 1).
  applySceneChains(sceneIdx) {
    const scene = this.project.scenes[sceneIdx];
    if (!scene) return;
    for (let t = 0; t < 8; t++) {
      const c = scene.trackChains[t] ?? { start: 0, end: 0 };
      this.project.patternChains[t] = { start: c.start ?? 0, end: c.end ?? 0 };
      this.app.ui.currentPattern[t] = Math.max(0, Math.min(7, c.start ?? 0));
    }
  }

  // Queue a scene (or scene chain) while playing: it takes over at the end
  // of the Drum 1 pattern currently playing (guide p.84, "Queuing Scenes").
  queueSceneChain(start, end) {
    if (!this.playing) return;
    const chain = this.range(start, end, 15);
    this.sceneState = { chain, pos: -1, current: -1, switchTime: this.nextDrum1Boundary() };
  }

  nextDrum1Boundary() {
    const ts = this.trackState[4]; // Drum 1
    const s = this.project.patterns[4][ts.patIdx].settings;
    const stepDur = this.stepDuration(s);
    const remaining = s.direction === 1
      ? ts.step - s.playbackStart + 1
      : s.playbackEnd - ts.step + 1;
    return ts.nextTime + Math.max(1, remaining) * stepDur;
  }

  range(start, end, max = 7) {
    // Clamp defensively against malformed chain bytes.
    start = Math.max(0, Math.min(max, start));
    end = Math.max(0, Math.min(max, end));
    if (end < start) end = start;
    const out = [];
    for (let i = start; i <= end; i++) out.push(i);
    return out;
  }

  start(resume = false) {
    if (this.playing) return;
    this.playing = true;
    this.automationQueue = [];
    const t0 = this.engine.now() + 0.08;
    this.startTime = t0;
    this.nextClickTime = t0;
    this.trackState = [];

    // Scene-chain arrangement playback when the project defines one. The
    // first scene's chains load before track states are built; a single
    // selected scene needs no scene machinery (its chains are already live).
    const scChain = this.project.sceneChain ?? { start: 0, end: 0 };
    const scRange = this.range(scChain.start ?? 0, scChain.end ?? 0, 15);
    if (scRange.length > 1) {
      this.applySceneChains(scRange[0]);
      this.sceneState = { chain: scRange, pos: 0, current: scRange[0], switchTime: 0 };
      this.app.onSceneChanged?.(scRange[0]);
    } else {
      this.sceneState = null;
    }

    for (let t = 0; t < 8; t++) {
      const chain = this.chainFor(t);
      const pat = this.project.patterns[t][chain[0]];
      this.trackState.push({
        chain,
        chainPos: 0,
        patIdx: chain[0],
        step: pat.settings.direction === 1 ? pat.settings.playbackEnd : pat.settings.playbackStart,
        dir: 1,
        stepCounter: 0,
        nextTime: t0,
        tieConsumed: new Map(), // key -> count of future triggers to suppress
        history: [],
      });
    }
    // Shift+Play: continue from where the sequencer last stopped.
    if (resume && this.stoppedPositions) {
      this.stoppedPositions.forEach((pos, t) => {
        if (pos && this.trackState[t].chain.includes(pos.patIdx)) {
          this.trackState[t].patIdx = pos.patIdx;
          this.trackState[t].step = pos.step;
        }
      });
    }
    if (this.sceneState) this.sceneState.switchTime = t0 + this.sceneDuration();
    this.worker.postMessage('start');
    this.app.onTransport?.(true);
  }

  stop() {
    if (!this.playing) return;
    this.playing = false;
    this.stoppedPositions = this.trackState.map((ts) => ({ patIdx: ts.patIdx, step: ts.step }));
    this.worker.postMessage('stop');
    const now = this.engine.now();
    for (const st of this.synthTracks) st.allNotesOff(now);
    this.visualEvents = [];
    this.automationQueue = [];
    this.app.onTransport?.(false);
  }

  toggle() { this.playing ? this.stop() : this.start(); }

  stepDuration(settings) {
    return SYNC_RATE_BEATS[settings.syncRate ?? 3] * (60 / this.bpm);
  }

  sceneDuration() {
    // A scene lasts as long as its longest track chain (one full pass).
    let max = 0;
    for (let t = 0; t < 8; t++) {
      const chain = this.chainFor(t);
      let dur = 0;
      for (const p of chain) {
        const s = this.project.patterns[t][p].settings;
        dur += (s.playbackEnd - s.playbackStart + 1) * this.stepDuration(s);
      }
      max = Math.max(max, dur);
    }
    return max || 1;
  }

  // Fire queued macro moves whose time has come (worker-tick resolution;
  // setMacro's own ~20ms smoothing absorbs the jitter).
  drainAutomation() {
    if (!this.automationQueue.length) return;
    const now = this.engine.now();
    const rest = [];
    for (const e of this.automationQueue) {
      if (e.time <= now + 0.005) {
        this.synthTracks[e.trackId]?.setMacro(e.idx, e.value);
        if (this.app.ui.currentTrack === e.trackId) this.app.updateKnobs?.();
      } else {
        rest.push(e);
      }
    }
    this.automationQueue = rest;
  }

  schedule() {
    if (!this.playing) return;
    this.drainAutomation();
    const horizon = this.engine.now() + LOOKAHEAD;
    this.scheduleClick(horizon);

    // Tracks never schedule past a pending scene switch; at the switch, every
    // track restarts exactly on the scene boundary so they stay phase-locked.
    let guard = 0;
    while (guard++ < 8) {
      const limit = this.sceneState ? Math.min(horizon, this.sceneState.switchTime) : horizon;
      for (let t = 0; t < 8; t++) {
        const ts = this.trackState[t];
        while (ts.nextTime < limit - 1e-6) {
          // A bad event must never freeze the transport: log and advance.
          try {
            this.scheduleStep(t, ts);
          } catch (err) {
            console.warn(`scheduleStep failed (track ${t}, step ${ts.step}):`, err);
          }
          this.advance(t, ts);
        }
      }
      if (!this.sceneState || this.sceneState.switchTime > horizon) break;
      const sc = this.sceneState;
      const at = sc.switchTime;
      sc.pos = (sc.pos + 1) % sc.chain.length;
      sc.current = sc.chain[sc.pos];
      this.applySceneChains(sc.current);
      for (let t = 0; t < 8; t++) this.resetTrackToChain(t, at);
      this.app.onSceneChanged?.(sc.current);
      if (sc.chain.length > 1) {
        sc.switchTime += this.sceneDuration();
      } else {
        // A single queued scene: its chains are live now; no more switches.
        this.sceneState = null;
      }
    }
  }

  resetTrackToChain(trackId, time) {
    const ts = this.trackState[trackId];
    ts.chain = this.chainFor(trackId);
    ts.chainPos = 0;
    ts.patIdx = ts.chain[0];
    const pat = this.project.patterns[trackId][ts.patIdx];
    ts.step = pat.settings.direction === 1 ? pat.settings.playbackEnd : pat.settings.playbackStart;
    ts.dir = 1;
    ts.tieConsumed.clear();
    ts.nextTime = time;
  }

  swingOffset(ts, stepDur) {
    if (ts.stepCounter % 2 === 1) return ((this.swing - 50) / 50) * stepDur * 0.5;
    return 0;
  }

  scheduleStep(trackId, ts) {
    const pattern = this.project.patterns[trackId][ts.patIdx];
    const stepDur = this.stepDuration(pattern.settings);
    const time = ts.nextTime + this.swingOffset(ts, stepDur);
    const step = pattern.steps[ts.step];
    if (!step) return;

    this.visualEvents.push({ type: 'step', time, trackId, step: ts.step, patIdx: ts.patIdx });
    // Keep a short scheduling history for live-record quantisation.
    ts.history.push({ step: ts.step, time, patIdx: ts.patIdx });
    if (ts.history.length > 16) ts.history.shift();

    const muted = this.app.ui.mutes[trackId];

    if (pattern.kind === 'drum') {
      // Collect this step's locks at micro resolution (fractional keys are
      // sub-step positions of smooth knob recordings).
      const locks = {};
      const subAuto = new Map(); // frac -> {param: value}
      for (const [param, lockMap] of Object.entries(pattern.paramLocks ?? {})) {
        for (const pos of Object.keys(lockMap)) {
          const p = Number(pos);
          if (p < ts.step || p >= ts.step + 1) continue;
          const frac = p - ts.step;
          if (frac === 0) locks[param] = lockMap[pos];
          let m = subAuto.get(frac);
          if (!m) subAuto.set(frac, (m = {}));
          m[param] = lockMap[pos];
        }
      }
      // Track-level automation applies at each locked position, hit or not.
      for (const [frac, params] of subAuto) {
        this.drums.applyStepAutomation(trackId - 4, params, time + frac * stepDur);
      }
      if (step.active && !muted) {
        const prob = PROBABILITY_LEVELS[Math.min(7, step.probability)] ?? 1;
        if (Math.random() <= prob) {
          const sample = step.drumChoice === 0xff
            ? this.project.drumConfigs[trackId - 4].patchSelect
            : step.drumChoice;
          // Drum micro steps (guide p.66): hits may sit on any of the six
          // ticks, including duplicates on several ticks. No micro array
          // means a single hit on the beat.
          const micro = step.micro?.some(Boolean) ? step.micro : [true, false, false, false, false, false];
          micro.forEach((on, m) => {
            if (!on) return;
            const hitTime = time + (m / 6) * stepDur;
            this.drums.play(trackId - 4, hitTime, step.velocity, step.drumChoice, Object.keys(locks).length ? locks : null);
            this.visualEvents.push({ type: 'drumhit', time: hitTime, trackId, sample });
          });
        }
      }
      return;
    }

    // Synth/MIDI step. Automation locks apply whether or not the step has
    // notes (knob movement playback), at micro resolution: fractional lock
    // keys are sub-step positions of smooth knob recordings.
    const locks = pattern.paramLocks ?? {};
    const synth = this.synthTracks[trackId];
    const ch = this.engine.tracks[trackId];
    const mixSetters = {
      level: (v, at) => ch.setLevel(v, at),
      pan: (v, at) => ch.setPan(v, at),
      reverb_send: (v, at) => ch.setReverbSend(v, at),
      delay_send: (v, at) => ch.setDelaySend(v, at),
    };
    let macroOverrides = null;
    for (const [param, lockMap] of Object.entries(locks)) {
      const macroMatch = /^macro([1-8])$/.exec(param);
      if (!mixSetters[param] && !macroMatch) continue;
      for (const pos of Object.keys(lockMap)) {
        const p = Number(pos);
        if (p < ts.step || p >= ts.step + 1) continue;
        const v = lockMap[pos];
        const at = time + (p - ts.step) * stepDur;
        if (mixSetters[param]) {
          mixSetters[param](v, at);
        } else {
          // Replay the knob movement: the macro moves and stays until the
          // next locked position, affecting sustained voices too. setMacro
          // can't be scheduled ahead, so it goes through the timed queue.
          const idx = Number(macroMatch[1]) - 1;
          if (p === ts.step) {
            macroOverrides = macroOverrides ?? {};
            macroOverrides[idx] = v;
          }
          this.automationQueue.push({ time: at, trackId, idx, value: v });
        }
      }
    }

    if (step.mask === 0) return;
    const prob = PROBABILITY_LEVELS[Math.min(7, step.probability)] ?? 1;
    if (Math.random() > prob) return;

    if (muted || !synth) return;

    const { scaleRoot, scaleType } = this.project;
    for (let slot = 0; slot < 6; slot++) {
      if (!(step.mask & (1 << slot))) continue;
      const key = `${ts.patIdx}:${ts.step}:${slot}`;
      const consumed = ts.tieConsumed.get(key) ?? 0;
      if (consumed > 0) {
        // This trigger is covered by an earlier tied note — no retrigger.
        if (consumed === 1) ts.tieConsumed.delete(key);
        else ts.tieConsumed.set(key, consumed - 1);
        continue;
      }
      const note = step.notes[slot];
      if (note.note === 0) continue;

      const gateTicks = note.gate & 0x7f;
      let tie = (note.gate & 0x80) !== 0;
      let durSteps = Math.max(gateTicks, 1) / 6;

      // Tie-forward: extend through subsequent steps that re-trigger the same
      // note, accumulating the full distance and consuming each suppressed
      // trigger (counted, so a self-tied drone covers many loop passes).
      let scanStep = ts.step;
      let cumDist = 0;
      let guard = 0;
      while (tie && guard++ < 64) {
        const next = this.findNextActiveStep(pattern, scanStep, note.note);
        if (!next) break;
        cumDist += this.stepDistance(pattern, scanStep, next.stepIdx);
        const nKey = `${ts.patIdx}:${next.stepIdx}:${next.slot}`;
        ts.tieConsumed.set(nKey, (ts.tieConsumed.get(nKey) ?? 0) + 1);
        const nextNote = pattern.steps[next.stepIdx].notes[next.slot];
        durSteps = cumDist + Math.max(nextNote.gate & 0x7f, 1) / 6;
        tie = (nextNote.gate & 0x80) !== 0;
        scanStep = next.stepIdx;
      }

      const noteTime = time + (note.delay / 6) * stepDur;
      const durSec = Math.max(0.03, durSteps * stepDur * 0.98);
      const midi = ncsToMidi(note.note, scaleRoot, scaleType);
      synth.noteOn(noteTime, midi, note.velocity, durSec, macroOverrides);
      this.visualEvents.push({ type: 'note', time: noteTime, trackId, midi, dur: durSec });
    }
  }

  findNextActiveStep(pattern, fromStep, noteNumber) {
    const { playbackStart, playbackEnd } = pattern.settings;
    let s = fromStep;
    for (let i = 0; i < playbackEnd - playbackStart + 1; i++) {
      s = s + 1 > playbackEnd ? playbackStart : s + 1;
      const st = pattern.steps[s];
      if (st.mask === 0) continue;
      for (let slot = 0; slot < 6; slot++) {
        if ((st.mask & (1 << slot)) && st.notes[slot].note === noteNumber) {
          return { stepIdx: s, slot };
        }
      }
      return null; // next triggered step doesn't contain the note: tie ends
    }
    return null;
  }

  stepDistance(pattern, from, to) {
    const { playbackStart, playbackEnd } = pattern.settings;
    const len = playbackEnd - playbackStart + 1;
    let d = to - from;
    if (d <= 0) d += len;
    return d;
  }

  advance(trackId, ts) {
    const pattern = this.project.patterns[trackId][ts.patIdx];
    const s = pattern.settings;
    const stepDur = this.stepDuration(s);
    ts.nextTime += stepDur;
    ts.stepCounter++;

    const { playbackStart: lo, playbackEnd: hi } = s;
    let wrapped = false;
    switch (s.direction) {
      case 1: // reverse
        ts.step--;
        if (ts.step < lo) { ts.step = hi; wrapped = true; }
        break;
      case 2: // ping-pong
        ts.step += ts.dir;
        if (ts.step > hi) { ts.step = Math.max(lo, hi - 1); ts.dir = -1; wrapped = true; }
        else if (ts.step < lo) { ts.step = Math.min(hi, lo + 1); ts.dir = 1; wrapped = true; }
        break;
      case 3: // random
        ts.step = lo + Math.floor(Math.random() * (hi - lo + 1));
        ts.stepCounter % (hi - lo + 1) === 0 && (wrapped = true);
        break;
      default: // forward
        ts.step++;
        if (ts.step > hi) { ts.step = lo; wrapped = true; }
    }

    if (wrapped) {
      // Note: tieConsumed entries persist across the wrap so a tie spanning
      // the loop boundary suppresses the next loop's retrigger.
      if (ts.chain.length > 1) {
        ts.tieConsumed.clear();
        ts.chainPos = (ts.chainPos + 1) % ts.chain.length;
        ts.patIdx = ts.chain[ts.chainPos];
        const np = this.project.patterns[trackId][ts.patIdx].settings;
        ts.step = np.direction === 1 ? np.playbackEnd : np.playbackStart;
        ts.dir = 1;
      } else {
        // Pick up live chain edits (e.g. user selected another pattern).
        const chain = this.chainFor(trackId);
        if (chain.length !== ts.chain.length || chain[0] !== ts.chain[0]) {
          ts.chain = chain;
          ts.chainPos = 0;
          ts.patIdx = chain[0];
          const np = this.project.patterns[trackId][ts.patIdx].settings;
          ts.step = np.direction === 1 ? np.playbackEnd : np.playbackStart;
        }
      }
    }
  }

  // Live recording: quantize to the scheduled step nearest to "now",
  // using the track's scheduling history (sample-accurate times).
  // Returns a handle so the gate can be set from the hold duration at
  // note-off (hardware: gate is assigned as you play, in 1/6-step ticks).
  recordNote(trackId, ncsNote, velocity = 96, sampleIdx = null) {
    if (!this.playing) return null;
    const ts = this.trackState[trackId];
    const now = this.engine.now();
    let best = null;
    for (const h of ts.history) {
      const d = Math.abs(h.time - now);
      if (!best || d < best.d) best = { ...h, d };
    }
    if (!best) return null;
    const pattern = this.project.patterns[trackId][best.patIdx];
    const step = best.step;
    // Non-quantised record (guide p.64): place the hit on the nearest of the
    // six micro steps instead of the step boundary.
    const tickDur = this.stepDuration(pattern.settings) / 6;
    const tick = this.app.ui.recQuantise
      ? 0
      : Math.max(0, Math.min(5, Math.round((now - best.time) / tickDur)));

    if (pattern.kind === 'drum') {
      const st = pattern.steps[step];
      const wasActive = st.active;
      st.active = true;
      st.velocity = velocity;
      if (!this.app.ui.recQuantise) {
        // A fresh non-quantised hit lands only on its tick; an existing
        // step keeps its beat hit and gains a duplicate.
        if (!st.micro) st.micro = wasActive
          ? [true, false, false, false, false, false]
          : [false, false, false, false, false, false];
        st.micro[tick] = true;
      }
      // Sample Flip (guide p.62): record the tapped sample to the step;
      // 0xFF means "use the track's active sample".
      if (sampleIdx != null) {
        const active = this.project.drumConfigs[trackId - 4]?.patchSelect;
        st.drumChoice = sampleIdx === active ? 0xff : sampleIdx;
      }
      this.app.onPatternEdited?.(trackId);
      return null;
    }
    const st = pattern.steps[step];
    for (let slot = 0; slot < 6; slot++) {
      if (!(st.mask & (1 << slot))) {
        st.mask |= 1 << slot;
        st.notes[slot] = { note: ncsNote, gate: 6, delay: tick, velocity };
        this.app.onPatternEdited?.(trackId);
        return {
          pattern, stepIdx: step, slot,
          // Wall-clock press time: steadier than the audio clock for
          // measuring a UI-driven hold duration.
          onTimeMs: performance.now(),
          stepDur: this.stepDuration(pattern.settings),
        };
      }
    }
    return null;
  }

  // Note-off for a live-recorded note: gate = actual hold duration,
  // quantized to micro-ticks (1-96 = up to 16 steps), like the hardware.
  finishRecordedNote(rec) {
    if (!rec) return;
    const heldSec = (performance.now() - rec.onTimeMs) / 1000;
    const ticks = Math.round(heldSec / (rec.stepDur / 6));
    const note = rec.pattern.steps[rec.stepIdx].notes[rec.slot];
    note.gate = (note.gate & 0x80) | Math.max(1, Math.min(96, ticks));
  }

  // Mutate: redistribute the current pattern's hits across random steps.
  mutate(trackId) {
    const patIdx = this.playing ? this.trackState[trackId].patIdx : this.app.ui.currentPattern[trackId];
    const pattern = this.project.patterns[trackId][patIdx];
    const { playbackStart: lo, playbackEnd: hi } = pattern.settings;
    const len = hi - lo + 1;
    const positions = [];
    for (let i = lo; i <= hi; i++) positions.push(i);
    for (let i = positions.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [positions[i], positions[j]] = [positions[j], positions[i]];
    }
    const slice = pattern.steps.slice(lo, hi + 1);
    const reordered = new Array(len);
    for (let i = 0; i < len; i++) reordered[positions[i] - lo] = slice[i];
    for (let i = 0; i < len; i++) pattern.steps[lo + i] = reordered[i];
    this.app.onPatternEdited?.(trackId);
  }

  // Drain visual events that are now audible (for playhead display).
  drainVisualEvents() {
    const now = this.engine.now();
    const ready = [];
    const rest = [];
    for (const e of this.visualEvents) (e.time <= now ? ready : rest).push(e);
    this.visualEvents = rest;
    return ready;
  }
}
