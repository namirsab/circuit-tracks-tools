// App-shell cache so the sampler works offline once visited.
// (Transfer obviously still needs the Circuit Tracks on USB.)
const CACHE = 'circuit-sampler-v3';
const SHELL = [
  '.',
  'index.html',
  'css/style.css',
  'js/app.js',
  'js/store.js',
  'js/audio/recorder.js',
  'js/audio/capture-worklet.js',
  'js/audio/convert.js',
  'js/audio/slice.js',
  'js/midi/protocol.js',
  'js/midi/transfer.js',
  'manifest.webmanifest',
  'icons/icon-192.png',
  'icons/icon-512.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))),
    ),
  );
  self.clients.claim();
});

// Network-first, cache fallback: updates flow through while staying offline-capable.
self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(e.request, { ignoreSearch: true })),
  );
});
