// Smartex AI — Service Worker
// Provides basic offline support and asset caching for PWA install.

const CACHE = 'smartex-v1';
const SHELL = ['/'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  // Only cache GET requests, skip API calls
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.pathname.startsWith('/chat') ||
      url.pathname.startsWith('/models') ||
      url.pathname.startsWith('/history') ||
      url.pathname.startsWith('/login') ||
      url.pathname.startsWith('/register') ||
      url.pathname.startsWith('/admin')) return;

  e.respondWith(
    fetch(e.request)
      .then(res => {
        // Cache a copy of successful navigation/static responses
        if (res && res.status === 200 && (
          e.request.destination === 'document' ||
          e.request.destination === 'style' ||
          e.request.destination === 'script' ||
          e.request.destination === 'font'
        )) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});
