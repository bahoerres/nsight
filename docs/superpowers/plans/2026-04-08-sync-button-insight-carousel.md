# Sync Button + Insight Carousel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a sidebar data sync button and a homepage insight carousel with regeneration, giving the user manual control over data refresh and insight generation.

**Architecture:** Flask API endpoints trigger existing ingest/insight scripts via subprocess. Frontend uses fetch + polling for async status. Carousel is server-rendered with JS visibility toggling. Shared toast component for all notifications.

**Tech Stack:** Flask, subprocess, Jinja2, vanilla JS, CSS

---

### Task 1: Add `--no-insights` flag to nsight-ingest

**Files:**
- Modify: `nsight-ingest:88-104`

- [ ] **Step 1: Add flag parsing and conditional skip**

At the top of `nsight-ingest`, after `set -euo pipefail`, add flag parsing. Then wrap the insight generation block (lines 88-104) in a conditional:

```bash
# After line 6 (set -euo pipefail), add:
NO_INSIGHTS=false
for arg in "$@"; do
  case "$arg" in
    --no-insights) NO_INSIGHTS=true ;;
  esac
done
```

Then wrap lines 88-104:

```bash
if [ "$NO_INSIGHTS" = "false" ]; then
  # Correlation insight (weekly, runs on Mondays)
  if [ "$(date +%u)" = "1" ]; then
    log "--- Correlation insight ---"
    if "$VENV" "$NSIGHT_DIR/generate_correlation_insight.py" >>"$LOG_FILE" 2>&1; then
      log "Correlation insight: OK"
    else
      log "Correlation insight: FAILED (exit $?)"
    fi
  fi

  # Generate daily insight (runs after data is fresh)
  log "--- Daily insight ---"
  if "$VENV" "$NSIGHT_DIR/generate_insights.py" --daily >>"$LOG_FILE" 2>&1; then
    log "Daily insight: OK"
  else
    log "Daily insight: FAILED (exit $?)"
  fi
else
  log "--- Skipping insights (--no-insights) ---"
fi
```

- [ ] **Step 2: Test manually**

Run: `bash nsight-ingest --no-insights 2>&1 | tail -5`
Expected: Should see "Skipping insights (--no-insights)" in output, no insight generation step.

- [ ] **Step 3: Commit**

```bash
git add nsight-ingest
git commit -m "feat: add --no-insights flag to nsight-ingest"
```

---

### Task 2: Simplify systemd timer to single daily run

**Files:**
- Modify: `systemd/nsight-ingest.timer`

- [ ] **Step 1: Replace the 4 OnCalendar lines with one**

Replace the full `[Timer]` section:

```ini
[Timer]
OnCalendar=*-*-* 10:00:00
Persistent=true
```

- [ ] **Step 2: Commit**

```bash
git add systemd/nsight-ingest.timer
git commit -m "chore: simplify ingest timer to single daily run at 10:00 UTC"
```

---

### Task 3: API endpoints for ingest and insight generation

**Files:**
- Modify: `app.py` (add imports and 4 new routes after the existing routes, before the `if __name__` block)

- [ ] **Step 1: Add subprocess, json, time, threading imports**

At the top of `app.py`, add to the existing imports:

```python
import subprocess
import time
import threading
```

Add `jsonify` to the Flask import line:

```python
from flask import Flask, render_template, request, send_from_directory, jsonify
```

- [ ] **Step 2: Add lockfile helpers and ingest trigger endpoint**

Add after the correlations route (after line ~2338) and before the `if __name__` block:

```python
# ── API: Data Sync & Insight Generation ─────────────────────────────


INGEST_LOCK = "/tmp/nsight-ingest.lock"
INSIGHT_LOCK = "/tmp/nsight-insight.lock"
NSIGHT_DIR = os.path.dirname(os.path.abspath(__file__))
COOLDOWN_SEC = 300  # 5 minutes


def _check_lock(lockfile):
    """Check if a lockfile exists and is recent. Returns (running, cooldown)."""
    if not os.path.exists(lockfile):
        return False, False
    try:
        mtime = os.path.getmtime(lockfile)
        age = time.time() - mtime
        with open(lockfile) as f:
            pid = int(f.read().strip())
        try:
            os.kill(pid, 0)
            return True, False
        except OSError:
            pass
        return False, age < COOLDOWN_SEC
    except (ValueError, FileNotFoundError):
        return False, False


def _write_lock(lockfile, pid):
    with open(lockfile, "w") as f:
        f.write(str(pid))


def _remove_lock(lockfile):
    try:
        os.remove(lockfile)
    except FileNotFoundError:
        pass


def _spawn_and_track(cmd, lockfile):
    """Spawn a subprocess, write its PID to lockfile, clean up when done."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _write_lock(lockfile, proc.pid)

    def _wait():
        proc.wait()
        _remove_lock(lockfile)

    threading.Thread(target=_wait, daemon=True).start()


@app.route("/api/ingest", methods=["POST"])
def api_ingest():
    running, cooldown = _check_lock(INGEST_LOCK)
    if running:
        return jsonify({"error": "already_running"}), 409
    if cooldown:
        return jsonify({"error": "cooldown"}), 409

    script = os.path.join(NSIGHT_DIR, "nsight-ingest")
    _spawn_and_track(["bash", script, "--no-insights"], INGEST_LOCK)
    return jsonify({"status": "started"}), 202
```

- [ ] **Step 3: Add the ingest status endpoint**

```python
@app.route("/api/ingest/status")
def api_ingest_status():
    running, _ = _check_lock(INGEST_LOCK)
    last_run = None
    if os.path.exists(INGEST_LOCK):
        last_run = datetime.fromtimestamp(
            os.path.getmtime(INGEST_LOCK), tz=LOCAL_TZ
        ).isoformat()
    return jsonify({"running": running, "last_run": last_run})
```

- [ ] **Step 4: Add the insight generation endpoint**

```python
@app.route("/api/generate-insight", methods=["POST"])
def api_generate_insight():
    data = request.get_json(force=True)
    insight_type = data.get("type", "daily")
    if insight_type not in ("daily", "weekly", "monthly"):
        return jsonify({"error": "invalid_type"}), 400

    running, cooldown = _check_lock(INSIGHT_LOCK)
    if running:
        return jsonify({"error": "already_running"}), 409
    if cooldown:
        return jsonify({"error": "cooldown"}), 409

    venv_python = os.path.join(NSIGHT_DIR, ".venv", "bin", "python")
    script = os.path.join(NSIGHT_DIR, "generate_insights.py")
    _spawn_and_track([venv_python, script, f"--{insight_type}", "--force"], INSIGHT_LOCK)
    return jsonify({"status": "started", "type": insight_type}), 202
```

- [ ] **Step 5: Add the insight fetch endpoint**

```python
@app.route("/api/insight/<insight_type>")
def api_insight(insight_type):
    if insight_type not in ("daily", "weekly", "monthly"):
        return jsonify({"error": "invalid_type"}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT date, type, content FROM insights WHERE type = %s ORDER BY date DESC LIMIT 1",
                (insight_type,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({"date": None, "type": insight_type, "content": None})

    return jsonify({
        "date": row["date"].strftime("%B %-d, %Y"),
        "type": row["type"],
        "content": row["content"],
    })
```

- [ ] **Step 6: Commit**

```bash
git add app.py
git commit -m "feat: add API endpoints for data sync and insight generation"
```

---

### Task 4: Toast notification component

**Files:**
- Modify: `static/style.css` (append toast styles)
- Modify: `static/app.js` (add toast function)
- Modify: `templates/base.html` (add toast element)

- [ ] **Step 1: Add toast CSS**

Append to `static/style.css`:

```css
/* ── Toast Notification ────────────────────────────────────────── */
.toast {
  position: fixed;
  bottom: 24px;
  right: 24px;
  z-index: 1000;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-btn);
  padding: 12px 20px;
  font-size: 0.85rem;
  color: var(--text-primary);
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.4);
  opacity: 0;
  transform: translateY(12px);
  transition: opacity 0.25s ease, transform 0.25s ease;
  pointer-events: none;
}

.toast.toast-visible {
  opacity: 1;
  transform: translateY(0);
  pointer-events: auto;
}

.toast-success { border-left: 3px solid var(--green); }
.toast-error   { border-left: 3px solid var(--red); }
.toast-info    { border-left: 3px solid var(--blue); }

@media (max-width: 768px) {
  .toast {
    left: 16px;
    right: 16px;
    bottom: 16px;
  }
}
```

- [ ] **Step 2: Add toast element to base.html**

In `templates/base.html`, just before the `<script>` tag on line 138, add:

```html
  <!-- Toast -->
  <div id="toast" class="toast"></div>
```

- [ ] **Step 3: Add toast JS function to app.js**

In `static/app.js`, inside the IIFE (before the closing `})();`), add:

```javascript
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
```

- [ ] **Step 4: Commit**

```bash
git add static/style.css static/app.js templates/base.html
git commit -m "feat: add shared toast notification component"
```

---

### Task 5: Sidebar sync button

**Files:**
- Modify: `templates/base.html` (add sync button to sidebar)
- Modify: `static/style.css` (sync button styles)
- Modify: `static/app.js` (sync click handler + polling)

- [ ] **Step 1: Add sync button to sidebar**

In `templates/base.html`, after the closing `</div>` of `.sidebar-nav` (line 126) and before the closing `</nav>` (line 127), add:

```html
    <button class="sidebar-sync" id="sync-btn" aria-label="Sync data" title="Sync data">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="23 4 23 10 17 10"/>
        <polyline points="1 20 1 14 7 14"/>
        <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10"/>
        <path d="M20.49 15a9 9 0 0 1-14.85 3.36L1 14"/>
      </svg>
    </button>
```

- [ ] **Step 2: Add sync button CSS**

Append to `static/style.css`:

```css
/* ── Sidebar Sync Button ───────────────────────────────────────── */
.sidebar-sync {
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: auto;
  padding: 16px 0;
  cursor: pointer;
  border-top: 1px solid var(--border-subtle);
}

.sidebar-sync svg {
  width: 20px;
  height: 20px;
  color: var(--text-secondary);
  opacity: 0.7;
  transition: opacity 0.15s ease, color 0.15s ease;
}

.sidebar-sync:hover svg {
  opacity: 1;
  color: var(--text-primary);
}

.sidebar-sync.syncing svg {
  animation: spin 1s linear infinite;
  color: var(--green);
  opacity: 1;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}
```

- [ ] **Step 3: Add sync JS to app.js**

In `static/app.js`, inside the IIFE (after the toast code, before closing `})();`):

```javascript
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
```

- [ ] **Step 4: Commit**

```bash
git add templates/base.html static/style.css static/app.js
git commit -m "feat: add sidebar sync button with polling and toast feedback"
```

---

### Task 6: Homepage insight carousel — backend data

**Files:**
- Modify: `app.py` (home route, add insight carousel queries)

- [ ] **Step 1: Replace insight_chips query with carousel data**

In `app.py`, in the `home()` function, replace the insight_chips block (lines 405-418) with:

```python
        # ── Insight carousel (latest daily, weekly, monthly) ───────
        carousel_insights = []
        with conn.cursor() as cur:
            for itype in ("daily", "weekly", "monthly"):
                cur.execute(
                    "SELECT date, type, content FROM insights WHERE type = %s ORDER BY date DESC LIMIT 1",
                    (itype,),
                )
                row = cur.fetchone()
                if row:
                    carousel_insights.append({
                        "type": row["type"],
                        "date": row["date"].strftime("%B %-d, %Y"),
                        "content": row["content"],
                        "label": {"daily": "Daily Insight", "weekly": "Weekly Summary", "monthly": "Monthly Review"}[row["type"]],
                    })
```

- [ ] **Step 2: Update render_template call**

In the `render_template` call for home.html (around line 343), add after the `trends=trends,` line:

```python
        carousel_insights=carousel_insights,
```

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: query latest insights for homepage carousel"
```

---

### Task 7: Homepage insight carousel — template and JS

**Files:**
- Modify: `templates/home.html` (add carousel row inside scores card)
- Modify: `static/style.css` (carousel styles)

- [ ] **Step 1: Add carousel HTML inside scores-row-inner**

In `templates/home.html`, after the `.weight-mini` div (line 301) and before the closing `</div>` of `.scores-row-inner` (line 302), add:

```html
    {# ── Insight Carousel ─────────────────────────────────────── #}
    <div class="carousel-row">
      {% if carousel_insights %}
        <button class="carousel-arrow carousel-prev" aria-label="Previous insight">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="15 18 9 12 15 6"/>
          </svg>
        </button>

        {% for ins in carousel_insights %}
        <div class="carousel-slide{% if loop.first %} carousel-active{% endif %}" data-type="{{ ins.type }}">
          <div class="carousel-content md-content">{{ ins.content | md }}</div>
          <div class="carousel-footer">
            <span class="carousel-type-badge">{{ ins.label }}</span>
            <span class="carousel-date">{{ ins.date }}</span>
            <button class="carousel-regen" data-type="{{ ins.type }}" aria-label="Regenerate insight" title="Regenerate insight">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="23 4 23 10 17 10"/>
                <polyline points="1 20 1 14 7 14"/>
                <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10"/>
                <path d="M20.49 15a9 9 0 0 1-14.85 3.36L1 14"/>
              </svg>
            </button>
          </div>
        </div>
        {% endfor %}

        <button class="carousel-arrow carousel-next" aria-label="Next insight">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="9 18 15 12 9 6"/>
          </svg>
        </button>
      {% else %}
        <div class="carousel-empty">No insights yet — run a sync to get started.</div>
      {% endif %}
    </div>
```

- [ ] **Step 2: Add carousel CSS**

Append to `static/style.css`:

```css
/* ── Insight Carousel ──────────────────────────────────────────── */
.carousel-row {
  grid-column: 1 / -1;
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  border-top: 1px solid var(--border-subtle);
  padding: 16px 4px 8px;
  min-height: 60px;
}

.carousel-slide {
  display: none;
  flex: 1;
  min-width: 0;
}

.carousel-slide.carousel-active {
  display: block;
}

.carousel-content {
  font-size: 0.85rem;
  line-height: 1.55;
  color: var(--text-secondary);
  max-height: 4.65em;
  overflow: hidden;
}

.carousel-content p {
  margin: 0;
}

.carousel-footer {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
  font-size: 0.72rem;
}

.carousel-type-badge {
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--blue);
}

.carousel-date {
  color: var(--text-muted);
}

.carousel-regen {
  cursor: pointer;
  display: flex;
  align-items: center;
  margin-left: auto;
  color: var(--text-muted);
  transition: color 0.15s ease;
}

.carousel-regen:hover {
  color: var(--text-primary);
}

.carousel-regen.regenerating svg {
  animation: spin 1s linear infinite;
  color: var(--green);
}

.carousel-arrow {
  flex-shrink: 0;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  color: var(--text-muted);
  opacity: 0.5;
  transition: opacity 0.15s ease, background 0.15s ease;
}

.carousel-arrow:hover {
  opacity: 1;
  background: var(--bg-elevated);
}

.carousel-empty {
  grid-column: 1 / -1;
  text-align: center;
  font-size: 0.85rem;
  color: var(--text-muted);
  padding: 8px 0;
}
```

- [ ] **Step 3: Add carousel JS**

In `templates/home.html`, in the `{% block scripts %}` section, add inside the `DOMContentLoaded` callback (after the sparkline code, before closing `});`):

```javascript
  // ── Insight Carousel ─────────────────────────────────────────
  var slides = document.querySelectorAll('.carousel-slide');
  var currentSlide = 0;

  function showSlide(idx) {
    slides.forEach(function(s) { s.classList.remove('carousel-active'); });
    currentSlide = ((idx % slides.length) + slides.length) % slides.length;
    slides[currentSlide].classList.add('carousel-active');
  }

  var prevBtn = document.querySelector('.carousel-prev');
  var nextBtn = document.querySelector('.carousel-next');
  if (prevBtn) prevBtn.addEventListener('click', function() { showSlide(currentSlide - 1); });
  if (nextBtn) nextBtn.addEventListener('click', function() { showSlide(currentSlide + 1); });

  // ── Carousel regenerate buttons ──────────────────────────────
  document.querySelectorAll('.carousel-regen').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var type = btn.dataset.type;
      if (btn.classList.contains('regenerating')) return;
      btn.classList.add('regenerating');

      fetch('/api/generate-insight', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: type }),
      })
        .then(function(res) {
          if (res.status === 409) {
            btn.classList.remove('regenerating');
            showToast('Insight generation already running', 'info');
            return;
          }
          // Poll for completion, then reload to get server-rendered markdown
          var pollId = setInterval(function() {
            fetch('/api/ingest/status')
              .then(function() {
                // Check if insight process is done by checking lock
                fetch('/api/insight/' + type)
                  .then(function(r) { return r.json(); })
                  .then(function(data) {
                    if (data.content) {
                      clearInterval(pollId);
                      btn.classList.remove('regenerating');
                      showToast('Insight regenerated', 'success');
                      setTimeout(function() { location.reload(); }, 1000);
                    }
                  });
              });
          }, 3000);
        })
        .catch(function() {
          btn.classList.remove('regenerating');
          showToast('Failed to regenerate insight', 'error');
        });
    });
  });
```

- [ ] **Step 4: Commit**

```bash
git add templates/home.html static/style.css
git commit -m "feat: add insight carousel to homepage score row"
```

---

### Task 8: Insights archive page — regenerate button

**Files:**
- Modify: `templates/insights.html` (add regen button next to section header)
- Modify: `static/style.css` (regen button styles)

- [ ] **Step 1: Add regenerate button to insights page**

In `templates/insights.html`, replace the section header block (lines 336-346) with:

```html
<div class="insights-section-header">
  <span class="insights-section-title">
    {% if tab == 'daily' %}Daily Insights
    {% elif tab == 'weekly' %}Weekly Summaries
    {% else %}Monthly Reviews
    {% endif %}
  </span>
  {% if items %}
    <span class="insights-count">{{ items | length }}</span>
  {% endif %}
  <button class="insights-regen-btn" id="archive-regen" data-type="{{ tab }}" aria-label="Regenerate {{ tab }} insight" title="Regenerate {{ tab }} insight">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="23 4 23 10 17 10"/>
      <polyline points="1 20 1 14 7 14"/>
      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10"/>
      <path d="M20.49 15a9 9 0 0 1-14.85 3.36L1 14"/>
    </svg>
  </button>
</div>
```

- [ ] **Step 2: Add regen button styles**

Append to `static/style.css`:

```css
/* ── Insights Archive Regenerate Button ────────────────────────── */
.insights-regen-btn {
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-left: auto;
  padding: 6px;
  border-radius: var(--radius-btn);
  color: var(--text-muted);
  transition: color 0.15s ease, background 0.15s ease;
}

.insights-regen-btn:hover {
  color: var(--text-primary);
  background: var(--bg-elevated);
}

.insights-regen-btn.regenerating svg {
  animation: spin 1s linear infinite;
  color: var(--green);
}
```

- [ ] **Step 3: Add regen JS**

In `templates/insights.html`, add a `{% block scripts %}` section at the bottom (before `{% endblock %}` for content, or as a new block):

```html
{% block scripts %}
<script>
document.addEventListener('DOMContentLoaded', function() {
  var regenBtn = document.getElementById('archive-regen');
  if (regenBtn) {
    regenBtn.addEventListener('click', function() {
      var type = regenBtn.dataset.type;
      if (regenBtn.classList.contains('regenerating')) return;
      regenBtn.classList.add('regenerating');

      fetch('/api/generate-insight', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: type }),
      })
        .then(function(res) {
          if (res.status === 409) {
            regenBtn.classList.remove('regenerating');
            showToast('Insight generation already running', 'info');
            return;
          }
          // Poll then reload
          var pollId = setInterval(function() {
            fetch('/api/insight/' + type)
              .then(function(r) { return r.json(); })
              .then(function() {
                clearInterval(pollId);
                regenBtn.classList.remove('regenerating');
                showToast('Insight regenerated — reloading...', 'success');
                setTimeout(function() { location.reload(); }, 1500);
              });
          }, 3000);
        })
        .catch(function() {
          regenBtn.classList.remove('regenerating');
          showToast('Failed to regenerate insight', 'error');
        });
    });
  }
});
</script>
{% endblock %}
```

- [ ] **Step 4: Commit**

```bash
git add templates/insights.html static/style.css
git commit -m "feat: add regenerate button to insights archive page"
```

---

### Task 9: Integration test and deploy

**Files:**
- All modified files

- [ ] **Step 1: Manual integration test**

Test the full flow:
1. Click sidebar sync button — verify spinner, polling, toast on completion
2. Navigate to home — verify carousel shows daily insight, arrows cycle through types
3. Click regenerate on carousel — verify spinner, page reload with fresh content, toast
4. Navigate to insights archive — verify regenerate button spins and page reloads with new insight
5. Click sync while already syncing — verify 409 toast

- [ ] **Step 2: Test mobile layout**

Resize browser to mobile width:
1. Sidebar sync button should be visible when sidebar is open
2. Carousel arrows should be visible (always visible on mobile)
3. Toast should be full-width at bottom

- [ ] **Step 3: Push to deploy**

```bash
git push origin main
```
