const CACHE_NAME = 'nsight-v1';
const STATIC_ASSETS = [
  '/static/style.css',
  '/static/charts.js',
  '/static/app.js',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(STATIC_ASSETS)));
});

self.addEventListener('fetch', e => {
  if (e.request.url.includes('/static/')) {
    e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
  }
});
