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

  /* ── PWA service worker ──────────────────────────────────────── */
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(function () {});
  }
})();
