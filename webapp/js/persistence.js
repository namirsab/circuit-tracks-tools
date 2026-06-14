// Browser-side session persistence (IndexedDB).
//
// The whole workspace normally lives in memory only, so an accidental reload
// throws it away. This stores two things so the app can offer to restore the
// last session on load:
//   - 'working': the live project as lossless .ncs bytes (same bytes Save and
//     Export produce), plus its bank slot index and name.
//   - 'pack':    the full pack — saved bank slots, drum samples and patches —
//     mirrored as raw buffers keyed by their pack-relative url, alongside the
//     pack index, so it restores straight through applyPackIndex().
//
// IndexedDB is used (not localStorage) because the snapshot is several MB of
// binary buffers, which structured-clone into IndexedDB natively. Everything
// degrades gracefully: if IndexedDB is unavailable (private mode, older
// browser) the methods no-op and the app runs exactly as before.

const DB_NAME = 'circuit-webtracks';
const STORE = 'kv';
const DB_VERSION = 1;

export class Persistence {
  constructor() {
    this.db = null;
    this.available = typeof indexedDB !== 'undefined';
  }

  async open() {
    if (!this.available || this.db) return this.db;
    this.db = await new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE);
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    }).catch((err) => {
      console.warn('Persistence: IndexedDB unavailable —', err?.message);
      this.available = false;
      return null;
    });
    return this.db;
  }

  // Run one IndexedDB request to its completion. Resolves with its result (or
  // null), and never rejects — a failed store must not break the app.
  _request(mode, run) {
    if (!this.db) return Promise.resolve(null);
    return new Promise((resolve) => {
      let req;
      try {
        req = run(this.db.transaction(STORE, mode).objectStore(STORE));
      } catch (err) {
        console.warn('Persistence:', err?.message);
        return resolve(null);
      }
      req.onsuccess = () => resolve(req.result ?? null);
      req.onerror = () => { console.warn('Persistence:', req.error?.message); resolve(null); };
    });
  }

  saveWorking(rec) { return this._request('readwrite', (s) => s.put(rec, 'working')); }

  savePack(rec) { return this._request('readwrite', (s) => s.put(rec, 'pack')); }

  async loadAll() {
    const [working, pack] = await Promise.all([
      this._request('readonly', (s) => s.get('working')),
      this._request('readonly', (s) => s.get('pack')),
    ]);
    return { working, pack };
  }

  clear() { return this._request('readwrite', (s) => s.clear()); }
}

// On-load prompt: restore the previous session or start fresh from the bundled
// pack. Resolves true to restore, false to start fresh. Mirrors the welcome
// overlay's show/hide pattern (#restore-overlay + .visible).
export function showRestorePrompt() {
  const root = document.getElementById('restore-overlay');
  if (!root) return Promise.resolve(false);
  root.innerHTML = `
    <div class="welcome-panel restore-panel">
      <div class="welcome-head">
        <span class="logo-bars"><i></i><i></i><i></i><i></i></span>
        <span class="logo-text">web<b>tracks</b></span>
      </div>
      <p class="welcome-tagline">You have a saved session from last time —
        projects, samples and your in-progress track. Pick up where you left off?</p>
      <div class="restore-actions">
        <button id="restore-yes" class="welcome-go">Restore session</button>
        <button id="restore-no" class="restore-fresh">Start fresh</button>
      </div>
    </div>`;
  // Don't stack on top of the first-run welcome overlay.
  document.getElementById('welcome-overlay')?.classList.remove('visible');
  return new Promise((resolve) => {
    const done = (v) => { root.classList.remove('visible'); resolve(v); };
    root.querySelector('#restore-yes').addEventListener('click', () => done(true));
    root.querySelector('#restore-no').addEventListener('click', () => done(false));
    root.classList.add('visible');
  });
}
