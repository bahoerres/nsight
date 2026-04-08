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

  /* ── Toast notifications ────────────────────────────────────── */
  var toastTimer = null;

  window.showToast = function(message, type) {
    var el = document.getElementById('toast');
    if (!el) return;
    el.textContent = message;
    el.className = 'toast toast-' + (type || 'info') + ' toast-visible';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function() {
      el.classList.remove('toast-visible');
    }, 4000);
  };

  /* ── Sidebar sync button ────────────────────────────────────── */
  var syncBtn = document.getElementById('sync-btn');
  if (syncBtn) {
    syncBtn.addEventListener('click', function() {
      if (syncBtn.classList.contains('syncing')) return;
      syncBtn.classList.add('syncing');

      fetch('/api/ingest', { method: 'POST' })
        .then(function(res) {
          if (res.status === 409) {
            return res.json().then(function(data) {
              syncBtn.classList.remove('syncing');
              var msg = data.error === 'already_running'
                ? 'Sync already running'
                : 'Please wait — last sync was less than 5 minutes ago';
              showToast(msg, 'info');
            });
          }
          // 202 — poll for completion
          var pollId = setInterval(function() {
            fetch('/api/ingest/status')
              .then(function(r) { return r.json(); })
              .then(function(status) {
                if (!status.running) {
                  clearInterval(pollId);
                  syncBtn.classList.remove('syncing');
                  showToast('Sync complete', 'success');
                }
              });
          }, 2000);
        })
        .catch(function() {
          syncBtn.classList.remove('syncing');
          showToast('Sync failed', 'error');
        });
    });
  }
})();
