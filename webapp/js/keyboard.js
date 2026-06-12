// Keyboard input mirroring the physical pad layout: the four main keyboard
// rows map to the four pad rows. Function buttons map to logical keys.
export const PAD_KEYS = [
  ['1', '2', '3', '4', '5', '6', '7', '8'],
  ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i'],
  ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k'],
  ['z', 'x', 'c', 'v', 'b', 'n', 'm', ','],
];

export const FN_KEYS = {
  ' ': { id: 'btn-play', label: 'Play/Stop' },
  Enter: { id: 'btn-rec', label: 'Record' },
  o: { id: 'btn-note', label: 'Note view' },
  p: { id: 'btn-velocity', label: 'Velocity view' },
  '[': { id: 'btn-gate', label: 'Gate view' },
  ']': { id: null, label: 'Probability view', action: 'view-probability' },
  '9': { id: 'btn-patterns', label: 'Patterns view' },
  '0': { id: null, label: 'Pattern settings', action: 'view-patternSettings' },
  l: { id: 'btn-mixer', label: 'Mixer view' },
  ';': { id: 'btn-fx', label: 'FX view' },
  "'": { id: 'btn-scales', label: 'Scales view' },
  '\\': { id: null, label: 'Mutate', action: 'mutate' },
  '.': { id: 'btn-page', label: 'Step page' },
  '/': { id: 'btn-keys', label: 'Key overlay' },
  '`': { id: 'btn-preset', label: 'Preset view' },
  ArrowUp: { id: 'btn-up', label: '▲ (octave / page)' },
  ArrowDown: { id: 'btn-down', label: '▼ (octave / page)' },
  '-': { id: null, label: 'BPM −', action: 'bpm-down' },
  '=': { id: null, label: 'BPM +', action: 'bpm-up' },
};

const padKeyIndex = new Map();
PAD_KEYS.forEach((row, r) => row.forEach((k, c) => padKeyIndex.set(k, r * 8 + c)));

export class KeyboardInput {
  constructor(app) {
    this.app = app;
    this.down = new Set();

    window.addEventListener('keydown', (e) => {
      if (e.repeat) return;
      if (e.target.tagName === 'INPUT') return;
      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;

      if (e.key === 'Shift') {
        app.setShift(true);
        return;
      }

      // Shift+1..8 selects a track.
      if (app.ui.shift && /^[1-8]$/.test(key)) {
        app.selectTrack(Number(key) - 1);
        e.preventDefault();
        return;
      }

      if (padKeyIndex.has(key) && !app.ui.shift) {
        const pad = padKeyIndex.get(key);
        if (!this.down.has(key)) {
          this.down.add(key);
          app.padPressed(pad);
        }
        e.preventDefault();
        return;
      }

      const fn = FN_KEYS[e.key] ?? FN_KEYS[key];
      if (fn) {
        if (fn.action === 'bpm-down') app.seq.setBpm(app.seq.bpm - 1);
        else if (fn.action === 'bpm-up') app.seq.setBpm(app.seq.bpm + 1);
        else if (fn.action === 'mutate') { app.seq.mutate(app.ui.currentTrack); app.lcdMsg('Mutated'); app.views.render(); }
        else if (fn.action === 'view-probability') app.setView('probability');
        else if (fn.action === 'view-patternSettings') app.setView('patternSettings');
        else if (fn.id) document.getElementById(fn.id)?.click();
        e.preventDefault();
      }
    });

    window.addEventListener('keyup', (e) => {
      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      if (e.key === 'Shift') {
        app.setShift(false);
        return;
      }
      if (padKeyIndex.has(key) && this.down.has(key)) {
        this.down.delete(key);
        app.padReleased(padKeyIndex.get(key));
      }
    });

    window.addEventListener('blur', () => {
      for (const key of this.down) app.padReleased(padKeyIndex.get(key));
      this.down.clear();
      app.setShift(false);
    });
  }
}

export function buildKeyOverlay(root) {
  const rows = PAD_KEYS.map(
    (row) => `<div class="ko-row">${row.map((k) => `<span class="ko-key">${k.toUpperCase()}</span>`).join('')}</div>`,
  ).join('');
  const fns = Object.entries(FN_KEYS)
    .map(([k, v]) => `<span class="ko-fn"><b>${k === ' ' ? 'Space' : k}</b> ${v.label}</span>`)
    .join('');
  root.innerHTML = `
    <div class="ko-panel">
      <button class="ko-close" id="key-overlay-close" title="Close">×</button>
      <h3>Keyboard mapping <small>(/, Esc, or click outside to close)</small></h3>
      <p>The four keyboard rows press the four pad rows:</p>
      ${rows}
      <p><b>Shift+1–8</b> select track · hold <b>Shift</b> for shift functions
      (e.g. Shift+Space resumes, Shift+<b>[</b> opens Micro Step, Shift+<b>;</b> Side Chain)</p>
      <div class="ko-fns">${fns}</div>
    </div>`;
}
