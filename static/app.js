/* ── nsight — client JS ─────────────────────────────────────────── */

(function () {
  'use strict';

  /* ── Sidebar toggle (mobile) ─────────────────────────────────── */
  const hamburger = document.querySelector('.hamburger');
  const overlay   = document.querySelector('.sidebar-overlay');

  function toggleSidebar() {
    document.body.classList.toggle('sidebar-open');
  }

  function closeSidebar() {
    document.body.classList.remove('sidebar-open');
  }

  if (hamburger) hamburger.addEventListener('click', toggleSidebar);
  if (overlay)   overlay.addEventListener('click', closeSidebar);

  /* close sidebar on Escape */
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeSidebar();
  });

  /* ── Swipe navigation (mobile) ───────────────────────────────── */
  if ('ontouchstart' in window) {
    var pages = ['/', '/health', '/sleep', '/recovery', '/training', '/nutrition', '/checkin', '/insights', '/correlations'];
    var current = pages.indexOf(window.location.pathname);
    if (current !== -1) {
      var startX, startY;
      document.addEventListener('touchstart', function (e) {
        startX = e.changedTouches[0].screenX;
        startY = e.changedTouches[0].screenY;
      }, {passive: true});

      document.addEventListener('touchend', function (e) {
        var dx = e.changedTouches[0].screenX - startX;
        var dy = e.changedTouches[0].screenY - startY;
        if (Math.abs(dx) < 75 || Math.abs(dy) > Math.abs(dx) * 0.6) return;
        if (document.body.classList.contains('sidebar-open')) return;
        var next = dx < 0
          ? (current + 1) % pages.length
          : (current - 1 + pages.length) % pages.length;
        window.location.href = pages[next];
      }, {passive: true});
    }
  }

  /* ── PWA service worker ──────────────────────────────────────── */
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(function () {});
  }
})();
