// Builds the device panel DOM, mirroring the Circuit Tracks Top View
// (user guide p.15): Master Volume, staggered macro knobs, Master Filter;
// utility row; Preset + track buttons + Patterns; pad grid flanked by the
// step buttons (left) and Mixer/FX/Record/Play (right).
import { TRACKS, TRACK_COLORS } from '../constants.js';

const MACRO_LEGENDS = [
  '1 Oscillator', '2 Osc Mod', '3 Amp Env', '4 Filter Env',
  '5 Filter Freq', '6 Resonance', '7 Modulation', '8 FX',
];

export function buildPanel(root) {
  root.innerHTML = '';
  const el = (tag, cls, parent, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    parent?.appendChild(e);
    return e;
  };

  const device = el('div', 'device', root);
  device.id = 'device';

  // --- Brand row with embedded LCD readout (web affordance) ---
  const brand = el('div', 'brand-row', device);
  const brandName = el('div', 'brand', brand);
  brandName.innerHTML = '<b>WEB</b> TRACKS';
  brandName.title = 'Unofficial fan project — not affiliated with Novation or Focusrite';
  const lcd = el('div', 'lcd', brand);
  el('span', 'lcd-name', lcd).id = 'lcd-name';
  el('span', 'lcd-bpm', lcd).id = 'lcd-bpm';
  el('span', 'lcd-view', lcd).id = 'lcd-view';
  el('span', 'lcd-msg', lcd).id = 'lcd-msg';
  const keysBtn = el('button', 'keys-btn', brand, '⌨ ?');
  keysBtn.id = 'btn-keys';
  keysBtn.title = 'Keyboard mapping (/)';
  const sidebarBtn = el('button', 'keys-btn', brand, '◧');
  sidebarBtn.id = 'btn-sidebar';
  sidebarBtn.title = 'Show/hide status sidebar';

  // --- Knob row: volume, staggered macros, filter ---
  const knobRow = el('div', 'knob-row', device);
  const mkKnob = (parent, id, label, cls = '') => {
    const wrap = el('div', `knob-wrap ${cls}`, parent);
    const knob = el('div', 'knob', wrap);
    knob.id = id;
    el('div', 'knob-grip', knob); // knurled rim — rotates with the value
    el('div', 'knob-indicator', knob);
    // LED window in the gap of the panel arc (macros light in the track
    // colour, Master Filter in blue; Master Volume has none).
    if (!cls.includes('volume')) el('div', 'knob-led', wrap);
    el('div', 'knob-label', wrap, label);
    return knob;
  };

  mkKnob(knobRow, 'knob-volume', 'Master Volume', 'master volume-knob');
  const macroBox = el('div', 'macro-knobs', knobRow);
  const macroKnobs = [];
  for (let i = 0; i < 8; i++) {
    // Hardware staggers the knobs: even-numbered knobs sit higher.
    macroKnobs.push(mkKnob(macroBox, `knob-macro-${i}`, MACRO_LEGENDS[i], i % 2 ? 'macro-hi' : 'macro-lo'));
  }
  mkKnob(knobRow, 'knob-filter', 'Master Filter', 'master filter-knob');

  // --- Utility row ---
  const mkBtn = (parent, id, label, shiftLabel, cls = 'fn-btn') => {
    const b = el('button', cls, parent);
    b.id = id;
    if (shiftLabel) el('span', 'shift-legend', b, shiftLabel);
    el('span', 'btn-label', b, label);
    return b;
  };

  const util = el('div', 'util-row', device);
  mkBtn(util, 'btn-scales', 'Scales', null);
  mkBtn(util, 'btn-down', '▼', null, 'fn-btn arrow-btn');
  mkBtn(util, 'btn-up', '▲', null, 'fn-btn arrow-btn');
  mkBtn(util, 'btn-page', '1-16\n17-32', null);
  mkBtn(util, 'btn-tempo', 'Tempo\nSwing', 'Tap');
  mkBtn(util, 'btn-clear', 'Clear', 'Click');
  mkBtn(util, 'btn-duplicate', 'Duplicate', 'Mutate');
  mkBtn(util, 'btn-save', 'Save', null);
  mkBtn(util, 'btn-projects', 'Projects', 'Packs');
  mkBtn(util, 'btn-shift', 'Shift', null, 'fn-btn shift-btn');

  // --- Preset / tracks / Patterns row ---
  const trackRow = el('div', 'track-row', device);
  mkBtn(trackRow, 'btn-preset', 'Preset', null, 'fn-btn preset-btn');
  const trackBox = el('div', 'track-btns', trackRow);
  const trackButtons = [];
  for (const t of TRACKS) {
    const b = el('button', 'track-btn', trackBox, t.name);
    b.id = `track-btn-${t.id}`;
    b.style.setProperty('--track-color', TRACK_COLORS[t.id]);
    b.dataset.track = t.id;
    trackButtons.push(b);
  }
  mkBtn(trackRow, 'btn-patterns', 'Patterns', 'View Lock', 'fn-btn patterns-btn');

  // --- Main area: step buttons | pads | mixer/fx/transport ---
  const main = el('div', 'main-area', device);

  const left = el('div', 'side-buttons', main);
  mkBtn(left, 'btn-note', 'Note', 'Expand');
  mkBtn(left, 'btn-velocity', 'Velocity', 'Fixed');
  mkBtn(left, 'btn-gate', 'Gate', 'Micro Step');
  mkBtn(left, 'btn-patternSettings', 'Pattern\nSettings', 'Probability');

  const padGrid = el('div', 'pad-grid', main);
  padGrid.id = 'pad-grid';
  const pads = [];
  for (let i = 0; i < 32; i++) {
    const p = el('div', 'pad', padGrid);
    p.dataset.pad = i;
    el('span', 'pad-label', p);
    pads.push(p);
  }

  const right = el('div', 'side-buttons', main);
  mkBtn(right, 'btn-mixer', 'Mixer', null);
  mkBtn(right, 'btn-fx', 'FX', 'Side Chain');
  mkBtn(right, 'btn-rec', '●', 'Rec Quantise', 'fn-btn transport-btn rec-btn');
  mkBtn(right, 'btn-play', '▶', null, 'fn-btn transport-btn play-btn');

  return { device, pads, macroKnobs, trackButtons };
}

export function buildSidebar(root) {
  root.innerHTML = `
    <h2>Project</h2>
    <div class="status-grid">
      <span>Name</span><span id="status-name">—</span>
      <span>BPM</span><span id="status-bpm">120</span>
      <span>Swing</span><span id="status-swing">50</span>
      <span>Scale</span><span id="status-scale">C Natural Minor</span>
    </div>
    <h2>Synths</h2>
    <div class="status-grid">
      <span>Synth 1</span><span id="status-patch-0">Initial Patch</span>
      <span>Synth 2</span><span id="status-patch-1">Initial Patch</span>
    </div>
    <h2>FX <span id="status-fx-bypass" class="fx-state">ON</span></h2>
    <div class="status-grid">
      <span>Reverb</span><span id="status-reverb">—</span>
      <span>Delay</span><span id="status-delay">—</span>
    </div>
    <h2>Mixer</h2>
    <table class="mixer-table" id="mixer-table">
      <thead><tr><th></th><th>Lvl</th><th>Pan</th><th>Rev</th><th>Dly</th></tr></thead>
      <tbody></tbody>
    </table>
    <h2>Drums</h2>
    <table class="mixer-table" id="drum-table">
      <thead><tr><th></th><th>Sample</th><th>Pitch</th><th>Dec</th></tr></thead>
      <tbody></tbody>
    </table>
    <button id="btn-load-file" class="load-btn">Load .ncs / .syx file…</button>
    <input type="file" id="file-input" accept=".ncs,.syx" multiple style="display:none">
    <button id="btn-load-pack" class="load-btn">Load pack…</button>
    <input type="file" id="pack-file-input" accept=".circuittrackspack,.zip" style="display:none">
    <button id="btn-load-pack-folder" class="load-btn">Load pack folder…</button>
    <input type="file" id="pack-input" webkitdirectory style="display:none">
    <button id="btn-export-project" class="load-btn">Export project (.ncs)</button>
    <button id="btn-export-patches" class="load-btn">Export patches (.syx)</button>
    <button id="btn-export-pack" class="load-btn">Export pack (.circuittrackspack)</button>
    <div class="drop-hint">Drop a <b>.ncs</b> project, <b>.circuittrackspack</b>, or <b>.syx</b> anywhere<br>Pack folder = a Components export with <b>index.json</b></div>
  `;
}
