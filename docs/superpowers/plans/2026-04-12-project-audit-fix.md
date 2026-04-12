# Project Audit Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the broken correlation pipeline, add a regenerate button, and clean up infrastructure drift on docker-top.

**Architecture:** Three code changes (fix key mismatch, add correlation to API, add regenerate button), then SSH to docker-top for infra cleanup (timer reload, ghost units, stale logs), then deploy and verify end-to-end.

**Tech Stack:** Python/Flask, Jinja2 templates, vanilla JS, systemd, SSH

**Spec:** `docs/superpowers/specs/2026-04-12-project-audit-fix-design.md`

---

### Task 1: Fix correlation insight generator bugs

**Files:**
- Modify: `generate_correlation_insight.py:70,76,123-134`

- [ ] **Step 1: Fix key mismatch on line 70 (significant findings)**

In `build_correlation_prompt()`, the significant findings format string references `f['lag']` which should be `f['lag_days']`. The p-value access is also wrong — it tries `f.get('p_corrected', f.get('p', 0))` but since `p_corrected` IS the correct key, simplify to `f['p_corrected']`.

Replace line 70:
```python
            lines.append(f"- {f['interpretation']} (r={f['r']:.3f}, p={f.get('p_corrected', f.get('p', 0)):.4f}, lag={f['lag']}d, n={f['n']})")
```
With:
```python
            lines.append(f"- {f['interpretation']} (r={f['r']:.3f}, p={f['p_corrected']:.4f}, lag={f['lag_days']}d, n={f['n']})")
```

- [ ] **Step 2: Fix key mismatch on line 76 (exploratory findings)**

Same issue. Replace line 76:
```python
            lines.append(f"- {f['interpretation']} (r={f['r']:.3f}, p={f.get('p', 0):.4f}, lag={f['lag']}d, n={f['n']})")
```
With:
```python
            lines.append(f"- {f['interpretation']} (r={f['r']:.3f}, p={f['p_corrected']:.4f}, lag={f['lag_days']}d, n={f['n']})")
```

Note: In the exploratory path, `run_correlations_for_display()` stores the raw p-value under the `p_corrected` key (see `app.py:2329`), so this key works for both paths.

- [ ] **Step 3: Remove stale CHECK constraint block (lines 123-134)**

This block dynamically recreates the insights type CHECK constraint with only 6 types, overwriting the correct 8-type constraint from schema.sql. The server already has the correct constraint. Remove the entire block.

Delete lines 123-134:
```python
    # Ensure 'correlation' type is allowed
    with conn.cursor() as cur:
        cur.execute("""
            DO $$
            BEGIN
                ALTER TABLE insights DROP CONSTRAINT IF EXISTS insights_type_check;
                ALTER TABLE insights ADD CONSTRAINT insights_type_check
                    CHECK (type IN ('daily', 'weekly', 'monthly', 'correlation', 'sleep', 'recovery'));
            EXCEPTION WHEN others THEN NULL;
            END $$;
        """)
    conn.commit()
```

- [ ] **Step 4: Verify the script parses cleanly**

Run:
```bash
python -c "import py_compile; py_compile.compile('generate_correlation_insight.py', doraise=True)"
```
Expected: No output (clean compile).

- [ ] **Step 5: Commit**

```bash
git add generate_correlation_insight.py
git commit -m "fix: correlation insight key mismatch and stale CHECK constraint

f['lag'] → f['lag_days'], f['p'] → f['p_corrected'] to match
run_correlations_for_display() output. Remove dynamic ALTER TABLE
that overwrote the correct 8-type constraint with a stale 6-type one."
```

---

### Task 2: Add correlation type to API endpoints

**Files:**
- Modify: `app.py:2457-2477` (generate-insight endpoint)
- Modify: `app.py:2480-2483` (insight fetch endpoint)

- [ ] **Step 1: Add correlation to `/api/generate-insight`**

The current allowed types list on line 2461 excludes correlation. Add it, and add routing logic for the correlation type which uses a different script than the other insight types.

Replace lines 2458-2477:
```python
def api_generate_insight():
    data = request.get_json(force=True)
    insight_type = data.get("type", "daily")
    if insight_type not in ("daily", "weekly", "monthly", "weekly_current", "monthly_current"):
        return jsonify({"error": "invalid_type"}), 400

    running, cooldown = _check_lock(INSIGHT_LOCK)
    if running:
        return jsonify({"error": "already_running"}), 409
    if cooldown:
        return jsonify({"error": "cooldown"}), 409

    venv_python = os.path.join(NSIGHT_DIR, ".venv", "bin", "python")
    script = os.path.join(NSIGHT_DIR, "generate_insights.py")
    # Rolling types use --rolling flag; standard types use --{type} --force
    if insight_type in ("weekly_current", "monthly_current"):
        _spawn_and_track([venv_python, script, "--rolling"], INSIGHT_LOCK)
    else:
        _spawn_and_track([venv_python, script, f"--{insight_type}", "--force"], INSIGHT_LOCK)
    return jsonify({"status": "started", "type": insight_type}), 202
```

With:
```python
def api_generate_insight():
    data = request.get_json(force=True)
    insight_type = data.get("type", "daily")
    if insight_type not in ("daily", "weekly", "monthly", "weekly_current", "monthly_current", "correlation"):
        return jsonify({"error": "invalid_type"}), 400

    running, cooldown = _check_lock(INSIGHT_LOCK)
    if running:
        return jsonify({"error": "already_running"}), 409
    if cooldown:
        return jsonify({"error": "cooldown"}), 409

    venv_python = os.path.join(NSIGHT_DIR, ".venv", "bin", "python")
    if insight_type == "correlation":
        script = os.path.join(NSIGHT_DIR, "generate_correlation_insight.py")
        _spawn_and_track([venv_python, script, "--force"], INSIGHT_LOCK)
    elif insight_type in ("weekly_current", "monthly_current"):
        script = os.path.join(NSIGHT_DIR, "generate_insights.py")
        _spawn_and_track([venv_python, script, "--rolling"], INSIGHT_LOCK)
    else:
        script = os.path.join(NSIGHT_DIR, "generate_insights.py")
        _spawn_and_track([venv_python, script, f"--{insight_type}", "--force"], INSIGHT_LOCK)
    return jsonify({"status": "started", "type": insight_type}), 202
```

- [ ] **Step 2: Add correlation to `/api/insight/<type>`**

Replace line 2482:
```python
    if insight_type not in ("daily", "weekly", "monthly", "weekly_current", "monthly_current"):
```
With:
```python
    if insight_type not in ("daily", "weekly", "monthly", "weekly_current", "monthly_current", "correlation"):
```

- [ ] **Step 3: Verify the app parses cleanly**

Run:
```bash
python -c "import py_compile; py_compile.compile('app.py', doraise=True)"
```
Expected: No output (clean compile).

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat: add correlation type to insight API endpoints

/api/generate-insight now accepts type='correlation' and routes to
generate_correlation_insight.py --force. /api/insight/correlation
returns the latest correlation insight."
```

---

### Task 3: Add regenerate button to correlations page

**Files:**
- Modify: `templates/correlations.html:449-460`

- [ ] **Step 1: Add regenerate button to the "What This Means" header**

The existing pattern from `templates/insights.html` uses a button with class `insights-regen-btn`, a refresh SVG icon, and inline JS that POSTs to the API, polls for completion, then reloads. The CSS and `showToast()` function already exist globally.

Replace lines 451-454:
```html
<div style="margin-top: var(--section-gap);">
  <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 12px;">
    <span style="font-size: 1.1rem; font-weight: 600; color: var(--text);">What This Means</span>
    <span style="font-size: 0.75rem; color: var(--text-muted);">updated {{ correlation_insight.date }}</span>
  </div>
```

With:
```html
<div style="margin-top: var(--section-gap);">
  <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 12px;">
    <span style="font-size: 1.1rem; font-weight: 600; color: var(--text);">What This Means</span>
    <span style="font-size: 0.75rem; color: var(--text-muted);">updated {{ correlation_insight.date }}</span>
    <button class="insights-regen-btn" id="corr-regen" aria-label="Regenerate correlation insight" title="Regenerate correlation insight">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="23 4 23 10 17 10"/>
        <polyline points="1 20 1 14 7 14"/>
        <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10"/>
        <path d="M20.49 15a9 9 0 0 1-14.85 3.36L1 14"/>
      </svg>
    </button>
  </div>
```

- [ ] **Step 2: Add JS handler in the scripts block**

Add the regenerate handler inside the existing `{% block scripts %}` block, after the existing strength-bar animation script. This follows the same pattern as the insights page handler.

In `{% block scripts %}`, after the existing `<script>` block (line 596-618) and before `{% endblock %}`, add:

```html
<script>
(function () {
  var regenBtn = document.getElementById('corr-regen');
  if (!regenBtn) return;
  regenBtn.addEventListener('click', function () {
    if (regenBtn.classList.contains('regenerating')) return;
    regenBtn.classList.add('regenerating');

    fetch('/api/generate-insight', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'correlation' }),
    })
      .then(function (res) {
        if (res.status === 409) {
          regenBtn.classList.remove('regenerating');
          return res.json().then(function (data) {
            var msg = data.error === 'already_running'
              ? 'Insight generation already running'
              : 'Please wait — last generation was less than 5 minutes ago';
            showToast(msg, 'info');
          });
        }
        var pollId = setInterval(function () {
          fetch('/api/insight/correlation')
            .then(function (r) { return r.json(); })
            .then(function () {
              clearInterval(pollId);
              regenBtn.classList.remove('regenerating');
              showToast('Correlation insight regenerated — reloading...', 'success');
              setTimeout(function () { location.reload(); }, 1500);
            });
        }, 3000);
      })
      .catch(function () {
        regenBtn.classList.remove('regenerating');
        showToast('Failed to regenerate insight', 'error');
      });
  });
})();
</script>
```

- [ ] **Step 3: Commit**

```bash
git add templates/correlations.html
git commit -m "feat: add regenerate button to correlations page

Reuses existing insights-regen-btn pattern. POSTs to /api/generate-insight
with type=correlation, polls for completion, reloads on success."
```

---

### Task 4: Infrastructure cleanup on docker-top

All commands run via SSH. No code deploy needed for these — can be done before or after deploy.

- [ ] **Step 1: Reload systemd timer**

```bash
ssh docker-top "systemctl --user daemon-reload"
```

This fixes the ingest timer from running 4x/day (10, 11, 12, 13 UTC) to once at 10:00 UTC as the on-disk file specifies.

Verify:
```bash
ssh docker-top "systemctl --user list-timers nsight-ingest.timer"
```
Expected: Only one NEXT fire time shown (next day at 10:00 UTC).

- [ ] **Step 2: Clear ghost healthdash units**

```bash
ssh docker-top "systemctl --user reset-failed"
```

Verify:
```bash
ssh docker-top "systemctl --user list-timers --all 2>/dev/null | grep healthdash"
```
Expected: No output (ghost units cleared).

- [ ] **Step 3: Clean old healthdash logs**

```bash
ssh docker-top "rm -rf ~/.local/log/healthdash/"
```

Verify:
```bash
ssh docker-top "ls ~/.local/log/healthdash/ 2>&1"
```
Expected: "No such file or directory"

- [ ] **Step 4: Check and remove old deploy keys**

First verify they're not referenced:
```bash
ssh docker-top "grep -r 'github_healthdash_deploy' ~/.ssh/config 2>/dev/null; echo exit:\$?"
```
Expected: No matches (exit:0 or exit:1 with no output).

If not referenced, remove:
```bash
ssh docker-top "rm -f ~/.ssh/github_healthdash_deploy ~/.ssh/github_healthdash_deploy.pub"
```

---

### Task 5: Deploy and verify

- [ ] **Step 1: Deploy code changes**

Use the deploy skill to commit, push, and deploy to docker-top.

- [ ] **Step 2: Trigger correlation insight generation**

```bash
ssh docker-top "cd ~/stacks/nsight && .venv/bin/python generate_correlation_insight.py --force"
```

Expected: Output like `Generated correlation insight for 2026-04-12 (XXX tokens)` with no errors.

- [ ] **Step 3: Verify the insight appears on the correlations page**

Check the API endpoint:
```bash
ssh docker-top "curl -s localhost:5100/api/insight/correlation | python3 -m json.tool"
```

Expected: JSON with today's date and non-null content.

- [ ] **Step 4: Verify the regenerate button works**

Load the correlations page in a browser. The "What This Means" section should show today's date. The regenerate button (refresh icon) should be visible next to the header.

- [ ] **Step 5: Verify timer schedule**

```bash
ssh docker-top "systemctl --user list-timers"
```

Expected: Only `nsight-ingest.timer` with a single daily fire time. No healthdash timers.
