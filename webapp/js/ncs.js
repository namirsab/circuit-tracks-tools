// .ncs project file parser — ported from src/circuit_tracks/ncs_parser.py.
// See docs/ncs-format.md for the full reverse-engineered specification.

export const NCS_FILE_SIZE = 160780;
const STEPS = 32;
const NOTES_PER_STEP = 6;
const SYNTH_STEP_SIZE = 28;
const SYNTH_STEP_DATA_SIZE = STEPS * SYNTH_STEP_SIZE; // 896
const DRUM_STEP_REGION_SIZE = 16 + 4 * 32; // 144
const SETTINGS_SIZE = 40;
const TIMING_OFFSET = 0x34;
const SC_BASE = 0x38; // scenes region ends at SC_BASE+684 = 740, exactly where block 0's step data begins
const TAIL_OFFSET = 0x26cfc;
const SYNTH_BLOCKS = 16;
const DRUM_BLOCK_START = 16;
const DRUM_BLOCK_END = 48;
const AUTOMATION_REGION_SIZE = 2304;
export const DRUM_AUTOMATION_REGION_SIZE = 1520; // 8 lanes would need 1536: the pan lane is cut short
export const LANE_POSITIONS = 192; // 6 micro ticks × 32 steps per automation lane

const METADATA_OFFSETS = [
  0x664, 0x130c, 0x1fb4, 0x2c5c, 0x3904, 0x45ac, 0x5254, 0x5efc,
  0x6ba4, 0x784c, 0x84f4, 0x919c, 0x9e44, 0xaaec, 0xb794, 0xc43c,
  0xcdf4, 0xd49c, 0xdb44, 0xe1ec, 0xe894, 0xef3c, 0xf5e4, 0xfc8c,
  0x10334, 0x109dc, 0x11084, 0x1172c, 0x11dd4, 0x1247c, 0x12b24, 0x131cc,
  0x13874, 0x13f1c, 0x145c4, 0x14c6c, 0x15314, 0x159bc, 0x16064, 0x1670c,
  0x16db4, 0x1745c, 0x17b04, 0x181ac, 0x18854, 0x18efc, 0x195a4, 0x19c4c,
  0x1a5fc, 0x1b2a4, 0x1bf4c, 0x1cbf4, 0x1d89c, 0x1e544, 0x1f1ec, 0x1fe94,
  0x20b3c, 0x217e4, 0x2248c, 0x23134, 0x23ddc, 0x24a84, 0x2572c, 0x263d4,
];

// Tail-relative offsets
const T_SCALE_ROOT = 16, T_SCALE_TYPE = 17, T_DELAY_PRESET = 18, T_REVERB_PRESET = 19;
const T_SYNTH1_PATCH = 24, T_SYNTH2_PATCH = 364, PATCH_SIZE = 340;
const T_DRUM_CONFIGS = 704;
const T_REVERB_SENDS = 748, T_REVERB_PARAMS = 756;
const T_DELAY_SENDS = 764, T_DELAY_PARAMS = 772;
const T_FX_BYPASS = 779;
const T_SIDECHAIN = [780, 785, 790, 795]; // S1, S2, M1, M2
const T_MIXER_LEVELS = 800, T_MIXER_PANS = 804;

// Lane order is the hardware's (it is load-bearing for the region arithmetic).
export const DRUM_AUTOMATION_PARAMS = ['pitch', 'decay', 'distortion', 'eq', 'reverb_send', 'delay_send', 'level', 'pan'];
export const MIXER_AUTOMATION_PARAMS = { 8: 'reverb_send', 9: 'delay_send', 10: 'level', 11: 'pan' };

const isDrumBlock = (b) => b >= DRUM_BLOCK_START && b < DRUM_BLOCK_END;
const stepDataStart = (b) =>
  METADATA_OFFSETS[b] - (isDrumBlock(b) ? DRUM_STEP_REGION_SIZE : SYNTH_STEP_DATA_SIZE);

// Chain entries (scene tracks, scene chain, pattern chains) are all
// [start, end, 0, 0]. Verified against every factory project: start <= end
// holds everywhere, and project_1's scene chain reads [0, 15] = all 16
// scenes, matching the hardware display.
function parseChainEntry(d, off) {
  return { start: d[off], end: d[off + 1], byte2: d[off + 2], byte3: d[off + 3] };
}

function parseSettings(d, off) {
  return {
    playbackEnd: d[off],
    playbackStart: d[off + 1],
    syncRate: d[off + 2],
    direction: d[off + 3], // 0=Fwd 1=Rev 2=PingPong 3=Random
  };
}

function parseSynthStep(d, off) {
  const notes = [];
  for (let i = 0; i < NOTES_PER_STEP; i++) {
    const n = off + 4 + i * 4;
    notes.push({ note: d[n], gate: d[n + 1], delay: d[n + 2], velocity: d[n + 3] });
  }
  return { mask: d[off], probability: d[off + 1], notes };
}

function parseLocks(preData, slots, numSteps) {
  // Automation region: lane-major, 192 positions per slot (6 micro × 32).
  // Locks are keyed by fractional step position (e.g. 3, 3.5, 3.833) so
  // smooth knob recordings keep their full micro resolution.
  const total = LANE_POSITIONS;
  const perStep = numSteps > 0 ? Math.floor(total / numSteps) : 6;
  const out = {};
  for (const [slot, key] of slots) {
    const base = slot * total;
    if (base >= preData.length) break;
    const locks = {};
    let any = false;
    for (let flat = 0; flat < total && base + flat < preData.length; flat++) {
      const v = preData[base + flat];
      if (v === 0xff) continue;
      // Positions past numSteps × perStep are kept too: the hardware leaves
      // automation recorded at a longer pattern length in place (inert until
      // the pattern grows again).
      const step = Math.floor(flat / perStep);
      const sub = flat % perStep;
      const pos = sub === 0 ? step : Math.round((step + sub / perStep) * 1000) / 1000;
      locks[pos] = v;
      any = true;
    }
    if (any) out[key] = locks;
  }
  return out;
}

export function parseNCS(arrayBuffer) {
  const d = new Uint8Array(arrayBuffer);
  if (d.length !== NCS_FILE_SIZE) {
    throw new Error(`Invalid NCS file size: ${d.length} (expected ${NCS_FILE_SIZE})`);
  }
  const magic = String.fromCharCode(d[0], d[1], d[2], d[3]);
  if (magic !== 'USER') throw new Error(`Invalid NCS signature: ${magic}`);

  const dv = new DataView(arrayBuffer);
  const name = new TextDecoder('ascii').decode(d.subarray(16, 48)).replace(/[\s\0]+$/, '');

  const proj = {
    name,
    color: dv.getUint32(12, true),
    tempo: d[TIMING_OFFSET],
    swing: d[TIMING_OFFSET + 1],
    swingSyncRate: d[TIMING_OFFSET + 2],
    scenes: [],
    sceneChain: null,
    patternChains: [],
    // patterns[trackId][patternIdx] — trackId order: S1,S2,M1,M2,D1,D2,D3,D4
    patterns: [],
  };

  // Scenes: 16 × 40 bytes — 8-byte header (byte 0 = used flag) + 8 chain
  // entries. Track order inside a scene: S1,S2,M1,M2,D1..D4
  for (let s = 0; s < 16; s++) {
    const base = SC_BASE + s * 40;
    const chains = [];
    for (let t = 0; t < 8; t++) chains.push(parseChainEntry(d, base + 8 + t * 4));
    proj.scenes.push({ flags: d[base], trackChains: chains });
  }
  // The scene chain uses the same [start, end, 0, 0] layout as pattern chains.
  const scEntry = parseChainEntry(d, SC_BASE + 648);
  proj.sceneChain = { start: scEntry.start, end: scEntry.end };
  for (let t = 0; t < 8; t++) {
    proj.patternChains.push(parseChainEntry(d, SC_BASE + 652 + t * 4));
  }

  // Pattern blocks. Block layout: 0-7 S1, 8-15 S2, 16-47 D1..D4, 48-55 M1, 56-63 M2.
  const blocks = [];
  const d4End = SC_BASE + 652 + 8 * 4;
  for (let b = 0; b < 64; b++) {
    const meta = METADATA_OFFSETS[b];
    const prevEnd = b === 0 ? d4End : METADATA_OFFSETS[b - 1] + SETTINGS_SIZE;
    const start = stepDataStart(b);
    const preData = d.subarray(prevEnd, start);
    const settings = parseSettings(d, meta);

    if (isDrumBlock(b)) {
      const rowBase = start + 16;
      const steps = [];
      for (let i = 0; i < STEPS; i++) {
        // The "active" byte is a 6-bit micro-hit mask, bit m = tick m
        // (1 = plain on-beat hit, 63 = six-hit roll, 9 = ticks 1 and 4).
        const mask = d[rowBase + 96 + i] & 0x3f;
        steps.push({
          active: mask !== 0,
          micro: mask !== 0 && mask !== 1
            ? Array.from({ length: 6 }, (_, m) => ((mask >> m) & 1) !== 0)
            : null,
          velocity: d[rowBase + i],
          probability: d[rowBase + 32 + i],
          drumChoice: d[rowBase + 64 + i],
        });
      }
      blocks.push({ kind: 'drum', steps, settings, preData, rawHeader: d.subarray(start, start + 16) });
    } else {
      const steps = [];
      for (let i = 0; i < STEPS; i++) steps.push(parseSynthStep(d, start + i * SYNTH_STEP_SIZE));
      blocks.push({ kind: 'synth', steps, settings, preData });
    }
  }

  // Automation for block N lives in block N+1's preData.
  for (let b = 0; b < 63; b++) {
    const blk = blocks[b];
    const pre = blocks[b + 1].preData;
    const numSteps = blk.settings.playbackEnd + 1;
    if (blk.kind === 'drum') {
      if (pre.length !== DRUM_AUTOMATION_REGION_SIZE) continue;
      blk.paramLocks = parseLocks(pre, DRUM_AUTOMATION_PARAMS.map((p, i) => [i, p]), numSteps);
    } else {
      if (pre.length !== AUTOMATION_REGION_SIZE) continue;
      const slots = [];
      for (let m = 1; m <= 8; m++) slots.push([m - 1, `macro${m}`]);
      for (const [slot, key] of Object.entries(MIXER_AUTOMATION_PARAMS)) slots.push([Number(slot), key]);
      blk.paramLocks = parseLocks(pre, slots, numSteps);
    }
    delete blk.preData;
  }
  delete blocks[63].preData;

  // Re-group blocks into per-track pattern arrays in UI track order.
  const blockRange = {
    0: [0, 8], 1: [8, 16], 2: [48, 56], 3: [56, 64],
    4: [16, 24], 5: [24, 32], 6: [32, 40], 7: [40, 48],
  };
  for (let t = 0; t < 8; t++) {
    const [lo, hi] = blockRange[t];
    proj.patterns.push(blocks.slice(lo, hi));
  }

  // Tail
  const t = d.subarray(TAIL_OFFSET);
  proj.scaleRoot = t[T_SCALE_ROOT];
  proj.scaleType = t[T_SCALE_TYPE];
  proj.delayPreset = t[T_DELAY_PRESET];
  proj.reverbPreset = t[T_REVERB_PRESET];
  proj.synth1Patch = t.slice(T_SYNTH1_PATCH, T_SYNTH1_PATCH + PATCH_SIZE);
  proj.synth2Patch = t.slice(T_SYNTH2_PATCH, T_SYNTH2_PATCH + PATCH_SIZE);

  proj.drumConfigs = [];
  for (let i = 0; i < 4; i++) {
    const o = T_DRUM_CONFIGS + i * 11;
    proj.drumConfigs.push({
      patchSelect: t[o], level: t[o + 1], pitch: t[o + 2], decay: t[o + 3],
      distortion: t[o + 4], eq: t[o + 5], pan: t[o + 6],
      reverbSend: t[o + 8], delaySend: t[o + 9],
    });
  }

  proj.fx = {
    reverbSends: Array.from(t.subarray(T_REVERB_SENDS, T_REVERB_SENDS + 8)),
    reverbType: t[T_REVERB_PARAMS],
    reverbDecay: t[T_REVERB_PARAMS + 1],
    reverbDamping: t[T_REVERB_PARAMS + 2],
    delaySends: Array.from(t.subarray(T_DELAY_SENDS, T_DELAY_SENDS + 8)),
    delayTime: t[T_DELAY_PARAMS],
    delaySync: t[T_DELAY_PARAMS + 1],
    delayFeedback: t[T_DELAY_PARAMS + 2],
    delayWidth: t[T_DELAY_PARAMS + 3],
    delayLrRatio: t[T_DELAY_PARAMS + 4],
    delaySlew: t[T_DELAY_PARAMS + 5],
    fxBypass: t[T_FX_BYPASS] !== 0,
  };

  // Sidechain: params for S1,S2,M1,M2 + preset indices split between
  // drum block 0's raw header (S1/S2) and the tail preamble (M1/M2).
  const drum0Hdr = blocks[DRUM_BLOCK_START].rawHeader;
  const scPresets = [drum0Hdr[3], drum0Hdr[11], t[3], t[11]];
  proj.sidechain = T_SIDECHAIN.map((o, i) => ({
    preset: scPresets[i],
    source: t[o], attack: t[o + 1], hold: t[o + 2], decay: t[o + 3], depth: t[o + 4],
  }));

  proj.mixerLevels = Array.from(t.subarray(T_MIXER_LEVELS, T_MIXER_LEVELS + 4)); // S1,S2,M1,M2
  proj.mixerPans = Array.from(t.subarray(T_MIXER_PANS, T_MIXER_PANS + 4));

  return proj;
}

// ---------- Serialization ----------

function writeChainEntry(d, off, e) {
  d[off] = e.start ?? 0;
  d[off + 1] = e.end ?? 0;
  d[off + 2] = e.byte2 ?? 0;
  d[off + 3] = e.byte3 ?? 0;
}

// Inverse of parseLocks: 0xff-fill the lanes, then place each lock at its
// lane-major position (192 positions per lane; fractional step keys map
// back to their micro positions).
function writeLocks(d, regionStart, regionSize, slots, paramLocks, numSteps) {
  const total = LANE_POSITIONS;
  const perStep = numSteps > 0 ? Math.floor(total / numSteps) : 6;
  for (const [slot, key] of slots) {
    const base = slot * total;
    if (base >= regionSize) break;
    d.fill(0xff, regionStart + base, regionStart + Math.min(base + total, regionSize));
    const locks = paramLocks?.[key];
    if (!locks) continue;
    for (const [pos, value] of Object.entries(locks)) {
      const off = base + Math.round(Number(pos) * perStep);
      if (off >= base && off < base + total && off < regionSize) d[regionStart + off] = value;
    }
  }
}

// Serialize the webapp project model into a complete .ncs file. All modeled
// fields are written over a copy of `baseBytes` (the originally loaded file,
// or a template), which supplies the unmodeled regions. With
// `freshScenes: true` (template base) the scenes/chains region is zeroed
// first, as in a new project — otherwise template leftovers like scene
// headers and scene-chain metadata select the wrong pattern on hardware.
export function serializeNCS(proj, baseBytes, { freshScenes = false } = {}) {
  const d = new Uint8Array(NCS_FILE_SIZE);
  d.set(new Uint8Array(baseBytes).subarray(0, NCS_FILE_SIZE));
  const dv = new DataView(d.buffer);
  // Zero scene blocks + state + scene chain, preserving the template's byte
  // at SC_BASE (scene 1 header byte 0, value 1 in factory files).
  if (freshScenes) d.fill(0, SC_BASE + 1, SC_BASE + 652);

  // Header: name + colour
  const name = (proj.name || 'Web Project').slice(0, 32).padEnd(32, ' ');
  for (let i = 0; i < 32; i++) d[16 + i] = name.charCodeAt(i) & 0x7f;
  dv.setUint32(12, proj.color ?? 0, true);

  // Timing
  d[TIMING_OFFSET] = proj.tempo ?? 120;
  d[TIMING_OFFSET + 1] = proj.swing ?? 50;
  d[TIMING_OFFSET + 2] = proj.swingSyncRate ?? 0;

  // Scenes + chains
  for (let s = 0; s < 16; s++) {
    const base = SC_BASE + s * 40;
    const scene = proj.scenes?.[s];
    if (!scene?.trackChains) continue;
    if (scene.flags !== undefined) d[base] = scene.flags;
    for (let t = 0; t < 8; t++) writeChainEntry(d, base + 8 + t * 4, scene.trackChains[t] ?? {});
  }
  const sc = proj.sceneChain ?? { start: 0, end: 0 };
  writeChainEntry(d, SC_BASE + 648, sc);
  for (let t = 0; t < 8; t++) writeChainEntry(d, SC_BASE + 652 + t * 4, proj.patternChains?.[t] ?? {});

  // Pattern blocks (UI track order -> block ranges)
  const blockRange = {
    0: [0, 8], 1: [8, 16], 2: [48, 56], 3: [56, 64],
    4: [16, 24], 5: [24, 32], 6: [32, 40], 7: [40, 48],
  };
  const blockOf = new Array(64);
  for (let t = 0; t < 8; t++) {
    const [lo] = blockRange[t];
    for (let p = 0; p < 8; p++) blockOf[lo + p] = proj.patterns[t][p];
  }

  for (let b = 0; b < 64; b++) {
    const pat = blockOf[b];
    if (!pat) continue;
    const meta = METADATA_OFFSETS[b];
    const start = stepDataStart(b);

    if (isDrumBlock(b)) {
      const rowBase = start + 16;
      for (let i = 0; i < STEPS; i++) {
        const s = pat.steps[i] ?? {};
        d[rowBase + i] = s.velocity ?? 0;
        d[rowBase + 32 + i] = s.probability ?? 7;
        d[rowBase + 64 + i] = s.drumChoice ?? 0xff;
        d[rowBase + 96 + i] = !s.active ? 0
          : s.micro?.some(Boolean)
            ? s.micro.reduce((m, on, t) => m | (on ? 1 << t : 0), 0)
            : 1;
      }
    } else {
      for (let i = 0; i < STEPS; i++) {
        const off = start + i * SYNTH_STEP_SIZE;
        const s = pat.steps[i] ?? {};
        d[off] = s.mask ?? 0;
        d[off + 1] = s.probability ?? 7;
        d[off + 2] = 0; d[off + 3] = 0;
        for (let n = 0; n < NOTES_PER_STEP; n++) {
          const note = s.notes?.[n] ?? {};
          const o = off + 4 + n * 4;
          d[o] = note.note ?? 0;
          d[o + 1] = note.gate ?? 0;
          d[o + 2] = note.delay ?? 0;
          d[o + 3] = note.velocity ?? 96;
        }
      }
    }
    const st = pat.settings ?? {};
    d[meta] = st.playbackEnd ?? 15;
    d[meta + 1] = st.playbackStart ?? 0;
    d[meta + 2] = st.syncRate ?? 3;
    d[meta + 3] = st.direction ?? 0;
  }

  // Automation regions: block N's locks live after its settings, in the gap
  // before block N+1's step data (after block 63: up to the tail).
  for (let b = 0; b < 64; b++) {
    const pat = blockOf[b];
    if (!pat) continue;
    const regionStart = METADATA_OFFSETS[b] + SETTINGS_SIZE;
    const regionEnd = b < 63 ? stepDataStart(b + 1) : TAIL_OFFSET;
    const size = regionEnd - regionStart;
    const expected = isDrumBlock(b) ? DRUM_AUTOMATION_REGION_SIZE : AUTOMATION_REGION_SIZE;
    if (size < expected) continue; // mirror the parser: not an automation region
    const numSteps = (pat.settings?.playbackEnd ?? 15) + 1;
    let slots;
    if (isDrumBlock(b)) {
      slots = DRUM_AUTOMATION_PARAMS.map((p, i) => [i, p]);
    } else {
      slots = [];
      for (let m = 1; m <= 8; m++) slots.push([m - 1, `macro${m}`]);
      for (const [slot, key] of Object.entries(MIXER_AUTOMATION_PARAMS)) slots.push([Number(slot), key]);
    }
    writeLocks(d, regionStart, expected, slots, pat.paramLocks, numSteps);
  }

  // Tail
  const tOff = TAIL_OFFSET;
  d[tOff + T_SCALE_ROOT] = proj.scaleRoot ?? 0;
  d[tOff + T_SCALE_TYPE] = proj.scaleType ?? 0;
  d[tOff + T_DELAY_PRESET] = proj.delayPreset ?? 0;
  d[tOff + T_REVERB_PRESET] = proj.reverbPreset ?? 0;
  if (proj.synth1Patch?.length === PATCH_SIZE) d.set(proj.synth1Patch, tOff + T_SYNTH1_PATCH);
  if (proj.synth2Patch?.length === PATCH_SIZE) d.set(proj.synth2Patch, tOff + T_SYNTH2_PATCH);

  for (let i = 0; i < 4; i++) {
    const cfg = proj.drumConfigs?.[i];
    if (!cfg) continue;
    const o = tOff + T_DRUM_CONFIGS + i * 11;
    d[o] = cfg.patchSelect ?? 0;
    d[o + 1] = cfg.level ?? 100;
    d[o + 2] = cfg.pitch ?? 64;
    d[o + 3] = cfg.decay ?? 127;
    d[o + 4] = cfg.distortion ?? 0;
    d[o + 5] = cfg.eq ?? 64;
    d[o + 6] = cfg.pan ?? 64;
    d[o + 8] = cfg.reverbSend ?? 0;
    d[o + 9] = cfg.delaySend ?? 0;
  }

  const fx = proj.fx ?? {};
  for (let i = 0; i < 8; i++) {
    d[tOff + T_REVERB_SENDS + i] = fx.reverbSends?.[i] ?? 0;
    d[tOff + T_DELAY_SENDS + i] = fx.delaySends?.[i] ?? 0;
  }
  d[tOff + T_REVERB_PARAMS] = fx.reverbType ?? 0;
  d[tOff + T_REVERB_PARAMS + 1] = fx.reverbDecay ?? 64;
  d[tOff + T_REVERB_PARAMS + 2] = fx.reverbDamping ?? 64;
  d[tOff + T_DELAY_PARAMS] = fx.delayTime ?? 0;
  d[tOff + T_DELAY_PARAMS + 1] = fx.delaySync ?? 12;
  d[tOff + T_DELAY_PARAMS + 2] = fx.delayFeedback ?? 64;
  d[tOff + T_DELAY_PARAMS + 3] = fx.delayWidth ?? 127;
  d[tOff + T_DELAY_PARAMS + 4] = fx.delayLrRatio ?? 0;
  d[tOff + T_DELAY_PARAMS + 5] = fx.delaySlew ?? 0;
  d[tOff + T_FX_BYPASS] = fx.fxBypass ? 1 : 0;

  // Sidechain: params in the tail; preset indices split between drum
  // block 0's header (S1/S2) and the tail preamble (M1/M2).
  const drum0Start = stepDataStart(DRUM_BLOCK_START);
  (proj.sidechain ?? []).forEach((s, i) => {
    if (!s) return;
    const o = tOff + T_SIDECHAIN[i];
    d[o] = s.source ?? 0;
    d[o + 1] = s.attack ?? 0;
    d[o + 2] = s.hold ?? 0;
    d[o + 3] = s.decay ?? 0;
    d[o + 4] = s.depth ?? 0;
    const preset = s.preset ?? 0;
    if (i === 0) d[drum0Start + 3] = preset;
    else if (i === 1) d[drum0Start + 11] = preset;
    else if (i === 2) d[tOff + 3] = preset;
    else d[tOff + 11] = preset;
  });

  for (let i = 0; i < 4; i++) {
    d[tOff + T_MIXER_LEVELS + i] = proj.mixerLevels?.[i] ?? 100;
    d[tOff + T_MIXER_PANS + i] = proj.mixerPans?.[i] ?? 64;
  }

  return d;
}
