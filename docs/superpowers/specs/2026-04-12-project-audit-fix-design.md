# Project Audit Fix — Design Spec

**Date:** 2026-04-12
**Scope:** Fix broken correlation pipeline, add regenerate button, clean up infrastructure drift

## Context

A routine check of the correlations page revealed that the "What This Means" insight hadn't updated since March 22. Investigation uncovered a chain of issues: a key mismatch bug in the correlation insight generator, the correlation type excluded from the API, a stale CHECK constraint that could break other insight types, and infrastructure drift on docker-top (timer running 4x/day, ghost healthdash units, stale logs/keys).

The codebase is otherwise healthy — no dead code, no architectural issues. This is a focused cleanup of specific breakage and drift.

## 1. Fix correlation insight generation

### generate_correlation_insight.py

**Key mismatch bug (lines 70, 76):**
The `build_correlation_prompt()` function references `f['lag']` and `f.get('p', 0)`, but `run_correlations_for_display()` in app.py returns findings with keys `lag_days` and `p_corrected`. This causes a `KeyError` on every run, which is why correlation insights have been failing since ~March 28.

Fix:
- `f['lag']` → `f['lag_days']` (both lines 70 and 76)
- `f.get('p_corrected', f.get('p', 0))` → `f['p_corrected']` for significant findings (line 70)
- For exploratory findings (line 76), use `f['p_corrected']` as well — in the exploratory path, `run_correlations_for_display()` stores raw p under `p_corrected` already

**Stale CHECK constraint (lines 123-134):**
Remove the entire `DO $$ ... END $$` block that dynamically recreates the insights type CHECK constraint. The constraint on the server already includes all 8 types (from schema.sql). This block can only cause harm — it overwrites the correct constraint with one missing `weekly_current` and `monthly_current`.

### app.py

**Add correlation to API endpoints:**

`/api/generate-insight` (line 2461): Add `"correlation"` to the allowed types list. When type is `"correlation"`, route to `generate_correlation_insight.py --force` instead of `generate_insights.py`.

`/api/insight/<type>` (line 2482): Add `"correlation"` to the allowed types list so the frontend can poll for the updated insight.

## 2. Add regenerate button to correlations page

### templates/correlations.html

Add a regenerate button next to the "What This Means" header, matching the existing pattern from the insights archive page. The button:

- POSTs to `/api/generate-insight` with `{"type": "correlation"}`
- Shows a spinner/loading state while generating
- Polls `/api/insight/correlation` for the updated content
- Swaps in the new insight text and date on completion
- Shows a toast notification on success/failure

Reuse the existing CSS classes and JS patterns from `templates/insights.html` and `static/app.js`.

## 3. Infrastructure cleanup on docker-top

All commands run via SSH. No code deploy required for these.

### Timer reload
```bash
systemctl --user daemon-reload
```
The on-disk timer file was updated to run once daily at 10:00 UTC, but systemd never reloaded — it's still running the old 4x/day schedule (10, 11, 12, 13 UTC).

### Clear ghost units
```bash
systemctl --user reset-failed
```
Removes 5 stale healthdash unit references (no files exist, just systemd memory artifacts).

### Clean old logs
```bash
rm -rf ~/.local/log/healthdash/
```
41 log files from the pre-rename era (March 19-27). No longer needed.

### Remove old deploy keys
```bash
# Verify these aren't referenced anywhere first
grep -r "github_healthdash_deploy" ~/.ssh/config 2>/dev/null
rm ~/.ssh/github_healthdash_deploy ~/.ssh/github_healthdash_deploy.pub
```

## 4. Deploy and verify

- Deploy code changes via the deploy skill
- Trigger correlation insight generation (via new button or direct API call)
- Confirm the insight generates successfully
- Confirm the "What This Means" section updates with fresh content and today's date
- Check journalctl for clean execution (no KeyError, no constraint errors)

## Out of scope

- Renaming healthdash references in compose.yml / DB name / volume name (breaking change, not worth the risk for cosmetic benefit)
- Adding session-type-aware volume to correlation pairs (separate feature, needs its own design)
- The pandas SQLAlchemy warning in `run_correlations_for_display()` (functional, noisy but harmless)
- `sleep` and `recovery` insight types (unused feature slots, not broken)
