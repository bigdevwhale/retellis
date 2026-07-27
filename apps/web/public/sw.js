/* Minimal Stillside service worker — network-first for navigation,
   cache-first for same-origin static assets. Serwist integration is deferred;
   installability is preserved via manifest + icons even if this SW fails.

   IMPORTANT: /v1/* is NEVER cached or served from cache. API responses are
   dynamic and credential-scoped (session cookie). Caching them would replay a
   stale pre-login 401 for /v1/auth/me after sign-in and trap the user in a
   login redirect loop. /v1/* is passed straight through to the network with the
   page's credentials, so the SW never sees or stores it. */
const CACHE = 'companion-v2';

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(['/', '/manifest.webmanifest', '/icons/icon.svg']))
      .catch(() => {}),
  );
});

self.addEventListener('activate', (event) => {
  // Drop every old cache (e.g. companion-v1 which held a stale /v1/auth/me 401)
  // so a previous SW's cached API responses can never be replayed.
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // API + auth calls: never intercept, never cache. Let the browser issue them
  // directly so the session cookie rides along and responses stay fresh.
  if (url.pathname.startsWith('/v1/')) return;

  // Network-first for navigations so the latest HTML wins; fall back to cached shell offline.
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches
            .open(CACHE)
            .then((c) => c.put(req, copy))
            .catch(() => {});
          return res;
        })
        .catch(() => caches.match(req).then((r) => r || caches.match('/'))),
    );
    return;
  }

  // Cache-first for static assets — but only successful, same-origin basic
  // responses (never cache errors / 4xx / 5xx / opaque).
  event.respondWith(
    caches.match(req).then(
      (cached) =>
        cached ||
        fetch(req).then((res) => {
          if (res.ok && res.type === 'basic') {
            const copy = res.clone();
            caches
              .open(CACHE)
              .then((c) => c.put(req, copy))
              .catch(() => {});
          }
          return res;
        }),
    ),
  );
});
