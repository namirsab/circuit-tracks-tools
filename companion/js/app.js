// Circuit Sampler — app shell and views.
// Record → Edit → Library → Transfer, mobile-first, no build step.

import { Recorder } from './audio/recorder.js';
import {
  convertToCircuitWav, mixdownToMono, normalize, applyGain, applyFades, trim,
  TARGET_SAMPLE_RATE,
} from './audio/convert.js';
import { detectTransients, sliceRanges } from './audio/slice.js';
import * as store from './store.js';
import { CircuitConnection, midiSupported, MAX_SAMPLE_BYTES } from './midi/transfer.js';

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const FAKE_INPUT = params.get('fakeinput') === '1';
const FORCE_NO_MIDI = params.get('nomidi') === '1';
const midiAvailable = midiSupported() && !FORCE_NO_MIDI;

// ---------------------------------------------------------------- helpers

let toastTimer = null;
function toast(msg, ms = 3000) {
  const el = $('toast');
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}

function confirmModal(text, okLabel = 'Overwrite') {
  return new Promise((resolve) => {
    const modal = $('confirm-modal');
    $('confirm-text').textContent = text;
    $('confirm-ok').textContent = okLabel;
    modal.hidden = false;
    const done = (result) => {
      modal.hidden = true;
      $('confirm-ok').onclick = null;
      $('confirm-cancel').onclick = null;
      resolve(result);
    };
    $('confirm-ok').onclick = () => done(true);
    $('confirm-cancel').onclick = () => done(false);
  });
}

function fmtTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = seconds - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, '0')}`;
}

function fmtBytes(n) {
  return n >= 1024 * 1024 ? `${(n / 1048576).toFixed(2)} MB` : `${(n / 1024).toFixed(0)} KB`;
}

function dbToLin(db) {
  return Math.pow(10, db / 20);
}

function fitCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  return dpr;
}

// ---------------------------------------------------------------- tabs

const views = { record: $('view-record'), library: $('view-library'), transfer: $('view-transfer') };
let currentTab = 'record';

function showTab(name) {
  currentTab = name;
  $('view-editor').hidden = true;
  for (const [key, el] of Object.entries(views)) el.hidden = key !== name;
  document.querySelectorAll('.tab').forEach((t) => {
    t.classList.toggle('active', t.dataset.tab === name);
  });
  if (name === 'library') renderLibrary();
  if (name === 'transfer') renderTransferView();
  if (name === 'record') initRecorderIfNeeded();
}

document.querySelectorAll('.tab').forEach((t) => {
  t.addEventListener('click', () => showTab(t.dataset.tab));
});

// ---------------------------------------------------------------- record view

const recorder = new Recorder();
let recorderReady = false;
let recStartTime = 0;
let meterRaf = 0;

async function refreshInputDevices() {
  const select = $('input-select');
  const previous = select.value;
  select.innerHTML = '';
  if (FAKE_INPUT) {
    const opt = document.createElement('option');
    opt.value = 'fake';
    opt.textContent = 'Test tone (fake input)';
    select.appendChild(opt);
  }
  try {
    const devices = await Recorder.listInputDevices();
    devices.forEach((d, i) => {
      const opt = document.createElement('option');
      opt.value = d.deviceId;
      opt.textContent = d.label || `Microphone ${i + 1}`;
      select.appendChild(opt);
    });
  } catch { /* no device access yet */ }
  if (Recorder.displayCaptureSupported) {
    const opt = document.createElement('option');
    opt.value = '__display';
    opt.textContent = 'Tab / system audio (screen share)';
    select.appendChild(opt);
  }
  if (!select.options.length) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = 'Default microphone';
    select.appendChild(opt);
  }
  if (previous) select.value = previous;
}

async function initRecorderIfNeeded() {
  if (recorderReady) return;
  try {
    await refreshInputDevices();
    await recorder.init(FAKE_INPUT ? 'fake' : $('input-select').value || undefined);
    recorderReady = true;
    // Labels become available after permission is granted
    await refreshInputDevices();
    $('rec-hint').textContent = recorder.fake
      ? 'Fake test input active — tap to record'
      : 'Tap to record';
    startMeterLoop();
  } catch (err) {
    $('rec-hint').textContent = `Microphone unavailable: ${err.message}`;
  }
}

$('input-refresh').addEventListener('click', refreshInputDevices);

$('input-select').addEventListener('change', async () => {
  if (recorder.recording) return;
  recorderReady = false;
  cancelAnimationFrame(meterRaf);
  try {
    await recorder.init($('input-select').value || undefined);
    recorderReady = true;
    recorder.onDisplayEnded = () => {
      recorderReady = false;
      $('rec-hint').textContent = 'Screen share ended — pick an input to continue';
    };
    $('rec-hint').textContent = recorder.isDisplay
      ? 'Capturing shared audio — play something, then tap to record'
      : 'Tap to record';
    startMeterLoop();
  } catch (err) {
    $('rec-hint').textContent = `Input unavailable: ${err.message}`;
  }
});

function startMeterLoop() {
  cancelAnimationFrame(meterRaf);
  const canvas = $('live-wave');
  const ctx2d = canvas.getContext('2d');
  const draw = () => {
    meterRaf = requestAnimationFrame(draw);
    if (!recorder.analyser) return;
    $('level-bar').style.width = `${Math.min(100, recorder.getLevel() * 130)}%`;

    const dpr = window.devicePixelRatio || 1;
    if (!canvas.width) fitCanvas(canvas);
    const { width: w, height: h } = canvas;
    ctx2d.clearRect(0, 0, w, h);
    const wave = recorder.getWaveform();
    if (!wave.length) return;
    ctx2d.strokeStyle = recorder.recording ? '#ef4444' : '#2dd4bf';
    ctx2d.lineWidth = 2 * dpr;
    ctx2d.beginPath();
    for (let x = 0; x < w; x++) {
      const v = wave[Math.floor((x / w) * wave.length)];
      const y = h / 2 - v * (h / 2) * 0.9;
      if (x === 0) ctx2d.moveTo(x, y);
      else ctx2d.lineTo(x, y);
    }
    ctx2d.stroke();

    if (recorder.recording) {
      $('rec-time').textContent = fmtTime((performance.now() - recStartTime) / 1000);
    }
  };
  draw();
}

$('rec-button').addEventListener('click', async () => {
  await initRecorderIfNeeded();
  if (!recorderReady) return;

  if (!recorder.recording) {
    recStartTime = performance.now();
    await recorder.startCapture();
    $('rec-button').classList.add('recording');
    $('rec-button').setAttribute('aria-label', 'Stop recording');
    $('rec-hint').textContent = 'Recording… tap to stop';
  } else {
    const { samples, sampleRate } = recorder.stopCapture();
    $('rec-button').classList.remove('recording');
    $('rec-button').setAttribute('aria-label', 'Start recording');
    $('rec-hint').textContent = 'Tap to record';
    $('rec-time').textContent = '0:00.0';
    if (samples.length < sampleRate * 0.02) {
      toast('Recording too short');
      return;
    }
    const name = `Sample ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
    const id = await store.saveSample(name, samples, sampleRate);
    toast('Saved to library');
    openEditor(id);
  }
});

// ---------------------------------------------------------------- library

async function renderLibrary() {
  const list = $('library-list');
  const items = await store.listSamples();
  $('library-empty').hidden = items.length > 0;
  list.innerHTML = '';
  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'library-item';
    const date = new Date(item.createdAt).toLocaleDateString([], { month: 'short', day: 'numeric' });
    li.innerHTML = `
      <div class="title-row">
        <span class="name"></span>
        <span class="meta">${item.duration.toFixed(2)} s · ${date}</span>
      </div>
      <div class="actions">
        <button class="btn act-edit">Edit</button>
        <button class="btn act-export">Export</button>
        <button class="btn act-send" ${midiAvailable ? '' : 'disabled'}>Send</button>
        <button class="btn danger act-delete">Delete</button>
      </div>`;
    li.querySelector('.name').textContent = item.name;
    li.querySelector('.act-edit').addEventListener('click', () => openEditor(item.id));
    li.querySelector('.act-export').addEventListener('click', () => exportSample(item.id));
    li.querySelector('.act-send').addEventListener('click', () => beginSend(item.id));
    li.querySelector('.act-delete').addEventListener('click', async () => {
      if (await confirmModal(`Delete “${item.name}”? This can’t be undone.`, 'Delete')) {
        await store.deleteSample(item.id);
        renderLibrary();
      }
    });
    list.appendChild(li);
  }
}

// Import an audio file (mp3/m4a/wav/…) into the library. decodeAudioData
// resamples to the context rate; we mix down to mono here so imports flow
// through the exact same edit/convert pipeline as recordings.
async function importAudioFile(file) {
  const arrayBuffer = await file.arrayBuffer();
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  let buffer;
  try {
    buffer = await ctx.decodeAudioData(arrayBuffer);
  } catch {
    throw new Error(`Couldn't decode "${file.name}" — unsupported audio format`);
  } finally {
    ctx.close().catch(() => {});
  }
  const channels = [];
  for (let c = 0; c < buffer.numberOfChannels; c++) channels.push(buffer.getChannelData(c));
  const mono = mixdownToMono(channels);
  const name = file.name.replace(/\.[^.]+$/, '').slice(0, 24) || 'Imported';
  return store.saveSample(name, mono, buffer.sampleRate);
}

$('library-import').addEventListener('click', () => $('import-file').click());
$('import-file').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  try {
    const id = await importAudioFile(file);
    toast(`Imported ${file.name}`);
    openEditor(id);
  } catch (err) {
    toast(err.message, 5000);
  }
});

/** Apply the non-destructive edit chain to a stored record. */
function processRecord(record) {
  const p = record.params;
  let s = trim(record.samples, p.trimStart, p.trimEnd);
  if (p.normalize) s = normalize(s);
  if (p.gainDb) s = applyGain(s, dbToLin(p.gainDb));
  const toSamples = (ms) => Math.round((ms / 1000) * record.sampleRate);
  s = applyFades(s, toSamples(p.fadeInMs), toSamples(p.fadeOutMs));
  return s;
}

async function renderRecordToWav(id) {
  const record = await store.getSample(id);
  const processed = processRecord(record);
  return { record, ...convertToCircuitWav([processed], record.sampleRate) };
}

async function exportSample(id) {
  const { record, wav } = await renderRecordToWav(id);
  const blob = new Blob([wav], { type: 'audio/wav' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${record.name.replace(/[^\w\- ]+/g, '').trim() || 'sample'}.wav`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  toast(`Exported ${a.download} (48 kHz / 16-bit / mono)`);
}

// ---------------------------------------------------------------- editor

const edit = {
  id: null,
  record: null,
  viewStart: 0,
  viewEnd: 0,
  playing: null, // { ctx, source }
  onsets: null, // absolute sample indices of detected slice starts
};

async function openEditor(id) {
  edit.id = id;
  edit.record = await store.getSample(id);
  edit.viewStart = 0;
  edit.viewEnd = edit.record.samples.length;
  clearSlices();
  for (const el of Object.values(views)) el.hidden = true;
  $('view-editor').hidden = false;
  $('editor-name').value = edit.record.name;
  $('gain-slider').value = edit.record.params.gainDb;
  $('gain-value').textContent = `${edit.record.params.gainDb} dB`;
  $('normalize-check').checked = edit.record.params.normalize;
  $('fade-in').value = String(edit.record.params.fadeInMs);
  $('fade-out').value = String(edit.record.params.fadeOutMs);
  requestAnimationFrame(() => { fitCanvas($('edit-wave')); drawEditor(); });
}

function closeEditor() {
  stopAudition();
  edit.id = null;
  edit.record = null;
  showTab(currentTab);
}

function editorStats() {
  const p = edit.record.params;
  const lengthSamples = Math.max(0, p.trimEnd - p.trimStart);
  const seconds = lengthSamples / edit.record.sampleRate;
  const outSamples = Math.round(seconds * TARGET_SAMPLE_RATE);
  const bytes = 44 + outSamples * 2;
  const over = bytes > MAX_SAMPLE_BYTES;
  $('edit-stats').innerHTML =
    `Trimmed: <strong>${seconds.toFixed(2)} s</strong> → ` +
    `${fmtBytes(bytes)} as 48 kHz/16-bit mono WAV` +
    (over
      ? ` — <strong style="color:#f87171">too long to transfer (max ≈ ${(MAX_SAMPLE_BYTES / 96000).toFixed(1)} s)</strong>`
      : '');
  return { over };
}

function drawEditor() {
  const canvas = $('edit-wave');
  const ctx2d = canvas.getContext('2d');
  if (!canvas.width) fitCanvas(canvas);
  const { width: w, height: h } = canvas;
  const dpr = window.devicePixelRatio || 1;
  const { samples } = edit.record;
  const p = edit.record.params;
  const span = edit.viewEnd - edit.viewStart;

  ctx2d.clearRect(0, 0, w, h);

  // peak waveform
  ctx2d.fillStyle = '#2dd4bf';
  const mid = h / 2;
  for (let x = 0; x < w; x++) {
    const s0 = Math.floor(edit.viewStart + (x / w) * span);
    const s1 = Math.floor(edit.viewStart + ((x + 1) / w) * span);
    let min = 0; let max = 0;
    for (let i = s0; i < Math.max(s1, s0 + 1) && i < samples.length; i++) {
      if (samples[i] < min) min = samples[i];
      if (samples[i] > max) max = samples[i];
    }
    const y0 = mid - max * mid * 0.92;
    const y1 = mid - min * mid * 0.92;
    ctx2d.fillRect(x, y0, 1, Math.max(1, y1 - y0));
  }

  // shade outside the trim region
  const xOf = (sample) => ((sample - edit.viewStart) / span) * w;
  const xs = xOf(p.trimStart);
  const xe = xOf(p.trimEnd);
  ctx2d.fillStyle = 'rgba(15,17,21,0.72)';
  if (xs > 0) ctx2d.fillRect(0, 0, Math.max(0, xs), h);
  if (xe < w) ctx2d.fillRect(Math.min(w, xe), 0, w - xe, h);

  // slice markers
  if (edit.onsets) {
    ctx2d.fillStyle = '#fbbf24';
    ctx2d.font = `${11 * dpr}px sans-serif`;
    edit.onsets.forEach((onset, i) => {
      const x = xOf(onset);
      if (x < 0 || x > w) return;
      ctx2d.fillRect(x - 0.75 * dpr, 0, 1.5 * dpr, h);
      ctx2d.fillText(String(i + 1), x + 3 * dpr, 12 * dpr);
    });
  }

  // trim handles
  for (const [x, kind] of [[xs, 'start'], [xe, 'end']]) {
    if (x < -20 || x > w + 20) continue;
    ctx2d.fillStyle = '#e8ecf1';
    ctx2d.fillRect(x - 1.5 * dpr, 0, 3 * dpr, h);
    ctx2d.beginPath();
    ctx2d.arc(x, kind === 'start' ? 14 * dpr : h - 14 * dpr, 11 * dpr, 0, Math.PI * 2);
    ctx2d.fill();
  }

  editorStats();
}

// --- trim-handle dragging + panning ---
(() => {
  const canvas = $('edit-wave');
  let drag = null; // 'start' | 'end' | 'pan'
  let panStartX = 0;
  let panStartView = 0;
  let moved = false;

  const sampleAt = (clientX) => {
    const rect = canvas.getBoundingClientRect();
    const frac = (clientX - rect.left) / rect.width;
    return edit.viewStart + frac * (edit.viewEnd - edit.viewStart);
  };

  canvas.addEventListener('pointerdown', (e) => {
    if (!edit.record) return;
    canvas.setPointerCapture(e.pointerId);
    moved = false;
    const rect = canvas.getBoundingClientRect();
    const span = edit.viewEnd - edit.viewStart;
    const xOf = (s) => ((s - edit.viewStart) / span) * rect.width;
    const x = e.clientX - rect.left;
    const p = edit.record.params;
    const grab = 26; // px grab radius — generous for touch
    if (Math.abs(x - xOf(p.trimStart)) < grab) drag = 'start';
    else if (Math.abs(x - xOf(p.trimEnd)) < grab) drag = 'end';
    else {
      drag = 'pan';
      panStartX = e.clientX;
      panStartView = edit.viewStart;
    }
  });

  canvas.addEventListener('pointermove', (e) => {
    if (!drag || !edit.record) return;
    if (Math.abs(e.clientX - panStartX) > 6 || drag !== 'pan') moved = true;
    const p = edit.record.params;
    const total = edit.record.samples.length;
    if (drag === 'start') {
      p.trimStart = Math.max(0, Math.min(sampleAt(e.clientX), p.trimEnd - 32));
    } else if (drag === 'end') {
      p.trimEnd = Math.min(total, Math.max(sampleAt(e.clientX), p.trimStart + 32));
    } else {
      const rect = canvas.getBoundingClientRect();
      const span = edit.viewEnd - edit.viewStart;
      const dx = ((panStartX - e.clientX) / rect.width) * span;
      const start = Math.max(0, Math.min(panStartView + dx, total - span));
      edit.viewStart = start;
      edit.viewEnd = start + span;
    }
    drawEditor();
  });

  canvas.addEventListener('pointerup', (e) => {
    // A tap (no drag) between slice markers auditions that slice
    if (drag === 'pan' && !moved && edit.onsets && edit.onsets.length) {
      const pos = sampleAt(e.clientX);
      const ranges = sliceRanges(edit.onsets, Math.ceil(edit.record.params.trimEnd));
      const hit = ranges.find((r) => pos >= r.start && pos < r.end);
      if (hit) playSamples(edit.record.samples.slice(hit.start, hit.end), edit.record.sampleRate);
    }
    drag = null;
  });
  canvas.addEventListener('pointercancel', () => { drag = null; });
})();

function zoom(factor) {
  const total = edit.record.samples.length;
  const p = edit.record.params;
  const center = (p.trimStart + p.trimEnd) / 2;
  let span = (edit.viewEnd - edit.viewStart) * factor;
  span = Math.max(256, Math.min(span, total));
  let start = center - span / 2;
  start = Math.max(0, Math.min(start, total - span));
  edit.viewStart = start;
  edit.viewEnd = start + span;
  drawEditor();
}
$('zoom-in').addEventListener('click', () => zoom(0.5));
$('zoom-out').addEventListener('click', () => zoom(2));

function stopAudition() {
  if (edit.playing) {
    try { edit.playing.source.stop(); } catch { /* ended */ }
    edit.playing.ctx.close().catch(() => {});
    edit.playing = null;
    $('audition').innerHTML = '&#x25b6; Play';
  }
}

function playSamples(samples, sampleRate) {
  stopAudition();
  if (!samples.length) return;
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const buffer = ctx.createBuffer(1, samples.length, sampleRate);
  buffer.getChannelData(0).set(samples);
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(ctx.destination);
  source.onended = stopAudition;
  source.start();
  edit.playing = { ctx, source };
}

// --- slicing ---

function sliceThreshold() {
  // Slider 5..60 (right = more slices) → detection threshold 0.60..0.05
  return (65 - parseInt($('slice-sens').value, 10)) / 100;
}

function runSliceDetection() {
  const p = edit.record.params;
  const region = edit.record.samples.subarray(
    Math.floor(p.trimStart), Math.ceil(p.trimEnd),
  );
  const onsets = detectTransients(region, edit.record.sampleRate, {
    threshold: sliceThreshold(),
  });
  edit.onsets = onsets.map((o) => o + Math.floor(p.trimStart));
  const n = edit.onsets.length;
  $('slice-count').textContent = n ? `— ${n} found` : '— none found';
  $('slice-save').textContent = `Save ${n} slice${n === 1 ? '' : 's'}`;
  $('slice-save').hidden = n === 0;
  $('slice-clear').hidden = false;
  $('slice-sens-wrap').hidden = false;
  $('slice-detect').textContent = 'Re-detect';
  drawEditor();
}

function clearSlices() {
  edit.onsets = null;
  $('slice-count').textContent = '';
  $('slice-save').hidden = true;
  $('slice-clear').hidden = true;
  $('slice-sens-wrap').hidden = true;
  $('slice-detect').textContent = 'Detect slices';
  if (edit.record) drawEditor();
}

$('slice-detect').addEventListener('click', runSliceDetection);
$('slice-clear').addEventListener('click', clearSlices);
$('slice-sens').addEventListener('input', () => {
  if (edit.onsets) runSliceDetection();
});

$('slice-save').addEventListener('click', async () => {
  const p = edit.record.params;
  const ranges = sliceRanges(edit.onsets, Math.ceil(p.trimEnd));
  const baseName = ($('editor-name').value.trim() || edit.record.name).slice(0, 20);
  for (let i = 0; i < ranges.length; i++) {
    const { start, end } = ranges[i];
    await store.saveSample(
      `${baseName} ${i + 1}`,
      edit.record.samples.slice(start, end),
      edit.record.sampleRate,
    );
  }
  toast(`Saved ${ranges.length} slices to the library`);
  clearSlices();
});

$('audition').addEventListener('click', () => {
  if (edit.playing) { stopAudition(); return; }
  const processed = processRecord(edit.record);
  if (!processed.length) return;
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const buffer = ctx.createBuffer(1, processed.length, edit.record.sampleRate);
  buffer.getChannelData(0).set(processed);
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(ctx.destination);
  source.onended = stopAudition;
  source.start();
  edit.playing = { ctx, source };
  $('audition').innerHTML = '&#x25a0; Stop';
});

$('gain-slider').addEventListener('input', (e) => {
  edit.record.params.gainDb = parseFloat(e.target.value);
  $('gain-value').textContent = `${edit.record.params.gainDb} dB`;
  drawEditor();
});
$('normalize-check').addEventListener('change', (e) => {
  edit.record.params.normalize = e.target.checked;
  drawEditor();
});
$('fade-in').addEventListener('change', (e) => {
  edit.record.params.fadeInMs = parseInt(e.target.value, 10);
});
$('fade-out').addEventListener('change', (e) => {
  edit.record.params.fadeOutMs = parseInt(e.target.value, 10);
});

$('editor-save').addEventListener('click', async () => {
  await store.updateSample(edit.id, {
    name: $('editor-name').value.trim() || edit.record.name,
    params: edit.record.params,
  });
  toast('Saved');
  closeEditor();
});
$('editor-back').addEventListener('click', closeEditor);
$('editor-export').addEventListener('click', async () => {
  await store.updateSample(edit.id, {
    name: $('editor-name').value.trim() || edit.record.name,
    params: edit.record.params,
  });
  exportSample(edit.id);
});
$('editor-send').addEventListener('click', async () => {
  const { over } = editorStats();
  if (over) { toast('Sample too long to transfer — trim it first'); return; }
  await store.updateSample(edit.id, {
    name: $('editor-name').value.trim() || edit.record.name,
    params: edit.record.params,
  });
  beginSend(edit.id);
});
$('editor-delete').addEventListener('click', async () => {
  if (await confirmModal(`Delete “${edit.record.name}”? This can’t be undone.`, 'Delete')) {
    await store.deleteSample(edit.id);
    closeEditor();
    showTab('library');
  }
});

// ---------------------------------------------------------------- transfer

const circuit = midiAvailable ? new CircuitConnection() : null;
let slotEntries = null; // last directory listing
let pendingSend = null; // sample id queued for sending

function renderTransferView() {
  $('midi-unsupported').hidden = midiAvailable;
  $('midi-panel').hidden = !midiAvailable;
  if (!midiAvailable) return;
  updateMidiStatus();
  renderPendingSend();
}

function updateMidiStatus() {
  const badge = $('midi-badge');
  if (circuit && circuit.connected) {
    $('midi-status').textContent = `Connected: ${circuit.portName}`;
    $('midi-connect').textContent = 'Reconnect';
    badge.textContent = 'MIDI ✓';
    badge.hidden = false;
  } else {
    $('midi-status').textContent = 'Not connected';
    $('midi-connect').textContent = 'Connect';
    badge.hidden = true;
  }
}

async function renderPendingSend() {
  const box = $('transfer-sample');
  if (!pendingSend) { box.hidden = true; return; }
  const record = await store.getSample(pendingSend);
  if (!record) { pendingSend = null; box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = '';
  const strong = document.createElement('strong');
  strong.textContent = record.name;
  box.append('Ready to send: ', strong, ' — now tap a destination slot below.');
}

function beginSend(id) {
  pendingSend = id;
  showTab('transfer');
  if (circuit && circuit.connected && !slotEntries) refreshSlots();
}

$('midi-connect').addEventListener('click', async () => {
  try {
    $('midi-status').textContent = 'Requesting MIDI access…';
    await circuit.connect();
    circuit.onStateChange = updateMidiStatus;
    updateMidiStatus();
    toast(`Connected to ${circuit.portName}`);
    await refreshSlots();
  } catch (err) {
    updateMidiStatus();
    $('midi-status').textContent = err.message;
  }
});

$('slots-refresh').addEventListener('click', refreshSlots);

async function refreshSlots() {
  if (!circuit || !circuit.connected) return;
  $('slots-wrap').hidden = false;
  const grid = $('slot-grid');
  grid.innerHTML = '<p class="hint">Reading sample slots from the device…</p>';
  try {
    slotEntries = await circuit.listSampleSlots();
  } catch (err) {
    grid.innerHTML = '';
    toast(`Slot listing failed: ${err.message}`);
    slotEntries = [];
  }
  renderSlotGrid();
}

function renderSlotGrid() {
  const grid = $('slot-grid');
  grid.innerHTML = '';
  const bySlot = new Map((slotEntries || []).map((e) => [e.slot, e.filename]));
  for (let slot = 0; slot < 64; slot++) {
    const name = bySlot.get(slot) || '';
    const btn = document.createElement('button');
    btn.className = `slot ${name ? '' : 'empty'}`;
    btn.setAttribute('role', 'option');
    const displayName = name.replace(/^\d+_/, '').replace(/\.wav$/i, '');
    btn.innerHTML = `<span class="slot-num">${slot + 1}</span><span class="slot-name"></span>`;
    btn.querySelector('.slot-name').textContent = name ? displayName : 'empty';
    btn.addEventListener('click', () => sendToSlot(slot, name));
    grid.appendChild(btn);
  }
}

async function sendToSlot(slot, existingName) {
  if (!pendingSend) {
    toast('Pick a sample first: Library → Send');
    return;
  }
  if (existingName) {
    const ok = await confirmModal(
      `Slot ${slot + 1} already contains “${existingName}”. Overwrite it?`,
    );
    if (!ok) return;
  }
  const { record, wav, seconds } = await renderRecordToWav(pendingSend);
  if (wav.length > MAX_SAMPLE_BYTES) {
    toast(`Sample too long (${seconds.toFixed(1)} s) — max ≈ ${(MAX_SAMPLE_BYTES / 96000).toFixed(1)} s`);
    return;
  }
  const safeName = record.name.replace(/[^\x20-\x7e]+/g, '').replace(/[\\/:*?"<>|]+/g, '').trim() || 'Sample';
  const deviceFilename = `${String(slot).padStart(2, '0')}_${safeName}.wav`;

  const progressWrap = $('transfer-progress');
  const resultBox = $('transfer-result');
  resultBox.hidden = true;
  progressWrap.hidden = false;
  $('progress-bar').style.width = '0%';
  $('progress-text').textContent = 'Starting transfer…';

  try {
    const result = await circuit.sendSample(slot, wav, deviceFilename, (sent, total) => {
      $('progress-bar').style.width = `${Math.round((sent / total) * 100)}%`;
      $('progress-text').textContent = `Sending ${fmtBytes(sent)} / ${fmtBytes(total)}`;
    });
    progressWrap.hidden = true;
    resultBox.hidden = false;
    resultBox.className = 'notice ok';
    resultBox.innerHTML = '';
    const strong = document.createElement('strong');
    strong.textContent = `Sent “${record.name}” to slot ${slot + 1}`;
    resultBox.append(strong, ` — ${fmtBytes(result.bytesSent)} in ${result.blocks} blocks, CRC ${result.crc32}, saved as ${result.filename}.`);
    pendingSend = null;
    renderPendingSend();
    await refreshSlots();
  } catch (err) {
    progressWrap.hidden = true;
    resultBox.hidden = false;
    resultBox.className = 'notice err';
    resultBox.textContent = `Transfer failed: ${err.message}. The device may need a power cycle if it stops responding.`;
  }
}

// ---------------------------------------------------------------- boot

if ('serviceWorker' in navigator && location.protocol !== 'file:') {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}

window.addEventListener('resize', () => {
  if (edit.record) { fitCanvas($('edit-wave')); drawEditor(); }
});

showTab('record');
