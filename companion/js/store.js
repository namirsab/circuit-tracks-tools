// IndexedDB sample library. Stores the original recording (Float32 samples
// at the capture rate) plus non-destructive edit params, so edits stay
// revisable across reloads. No server involved.

const DB_NAME = 'circuit-companion';
const DB_VERSION = 1;
const STORE = 'samples';

let dbPromise = null;

function openDb() {
  if (!dbPromise) {
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          const store = db.createObjectStore(STORE, { keyPath: 'id' });
          store.createIndex('createdAt', 'createdAt');
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }
  return dbPromise;
}

function tx(db, mode) {
  return db.transaction(STORE, mode).objectStore(STORE);
}

function reqPromise(req) {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export function defaultParams(sampleCount) {
  return {
    trimStart: 0,
    trimEnd: sampleCount,
    gainDb: 0,
    normalize: false,
    fadeInMs: 0,
    fadeOutMs: 5, // short fade-out by default to kill end clicks
  };
}

/** Save a new recording. samples: Float32Array, rate: capture sample rate. */
export async function saveSample(name, samples, sampleRate) {
  const db = await openDb();
  const record = {
    id: crypto.randomUUID(),
    name,
    createdAt: Date.now(),
    sampleRate,
    duration: samples.length / sampleRate,
    data: samples.buffer.slice(samples.byteOffset, samples.byteOffset + samples.byteLength),
    params: defaultParams(samples.length),
  };
  await reqPromise(tx(db, 'readwrite').put(record));
  return record.id;
}

export async function updateSample(id, fields) {
  const db = await openDb();
  const record = await reqPromise(tx(db, 'readonly').get(id));
  if (!record) throw new Error(`Sample ${id} not found`);
  Object.assign(record, fields);
  await reqPromise(tx(db, 'readwrite').put(record));
  return record;
}

export async function getSample(id) {
  const db = await openDb();
  const record = await reqPromise(tx(db, 'readonly').get(id));
  if (record) record.samples = new Float32Array(record.data);
  return record;
}

/** List all samples, newest first, without the bulky audio data. */
export async function listSamples() {
  const db = await openDb();
  const all = await reqPromise(tx(db, 'readonly').getAll());
  return all
    .map(({ data, ...meta }) => meta)
    .sort((a, b) => b.createdAt - a.createdAt);
}

export async function deleteSample(id) {
  const db = await openDb();
  await reqPromise(tx(db, 'readwrite').delete(id));
}
