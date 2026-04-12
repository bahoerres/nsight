# nsight roadmap

**Last updated:** 2026-04-12

nsight is the single repo for the entire health stack. The healthdash → nsight migration (original purpose of this doc) is complete. This roadmap tracks what's been done, what's known to need work, and ideas that need further discussion.

---

## Completed

### Migration (formerly Phases 1-4)

- [x] Absorb all backend scripts (ingest_garmin, ingest_hevy, cronometer, scoring, insights)
- [x] Schema, compose, .env, credentials — all consolidated
- [x] Systemd services (nsight-web, nsight-ingest timer)
- [x] Decommission healthdash frontend, timers, and deploy keys
- [x] All pages built: home, health, sleep, recovery, training, nutrition, checkin, insights, correlations

### Recent fixes (2026-04-12 audit sweep)

- [x] Fix correlation insight generator (key mismatch, stale CHECK constraint, dead model ID)
- [x] Add correlation type to insight API endpoints
- [x] Add regenerate button to correlations page
- [x] Infra cleanup: timer reload (was 4x/day → 1x/day), ghost healthdash units, old logs/keys

### Data & scoring improvements

- [x] Volume trend compares against same-type sessions, not all sessions (scoring.py)
- [x] Bodyweight substitution for BW exercises in Hevy volume
- [x] Warmup set filtering + set_type tracking
- [x] Insight engine v3: exercise-level training detail + signal filtering
- [x] Rolling current insights (weekly_current, monthly_current)
- [x] Friday week-start alignment for training week
- [x] Volume trend MA averages training days only, not rest days
- [x] Sync button with polling and toast feedback
- [x] Insight carousel on health page
- [x] PWA setup

---

## Known issues / tech debt

### Cosmetic healthdash naming

The database layer still uses "healthdash" names: Postgres container, volume, DB name, DB user, .env references. Renaming requires a coordinated migration (stop services, rename volume/DB, update all connection strings, restart). Low risk but high annoyance factor. Not urgent — it works fine as-is.

**Decision needed:** Is the naming confusion worth the migration effort? Probably not unless we're adding a second database or sharing the stack with someone.

### pandas SQLAlchemy warning

`run_correlations_for_display()` passes a raw psycopg2 connection to `pd.read_sql()`. Pandas wants a SQLAlchemy engine. Functional but noisy in logs. Fix is straightforward (create an engine from DATABASE_URL) but touches the correlation analysis hot path.

### Correlation insight schedule

Currently runs on Fridays only (matching Fri-Thu training week). The day-of-week check in `nsight-ingest` was changed from Monday → Friday in b991ba7. Verify this is actually the right cadence — with the regenerate button available, manual generation is always an option.

---

## Backlog

### Session-type-aware correlations

`scoring.py` now compares training volume against same-muscle-group sessions (upper vs upper, lower vs lower). The correlation analysis in `app.py` still uses raw `hevy_total_volume_lbs` which lumps all session types together. This means the HRV-vs-volume and deep-sleep-vs-volume correlations may be noisy.

**Options to discuss:**
- Add muscle-group-specific volume columns to daily_log (e.g. `hevy_upper_volume`, `hevy_lower_volume`)
- Or compute session-type volume on the fly in `run_correlations_for_display()` using hevy_sets
- Or just correlate against session RPE/intensity rather than raw volume

### Radial muscle group chart

Hevy-style body map visualization showing muscle group distribution over a time window. Would go on the training page alongside or replacing the current muscle group bar chart.

**Needs discussion:**
- What time window? Rolling 7 days? 28 days? Selectable?
- SVG body outline vs radial/polar chart vs heatmap grid?
- Data is already available (hevy_muscle_groups array in daily_log, granular data in hevy_sets)
- Reference: Hevy's own body map for the interaction model

### Unused insight types: sleep & recovery

The schema allows `sleep` and `recovery` insight types but nothing generates them. These could be specialized insights focused on sleep patterns or recovery trends, separate from the general daily/weekly insights.

**Decision needed:** Are these worth building, or do the daily/weekly insights already cover sleep and recovery well enough?

### app.py decomposition

`app.py` is ~2,500 lines. Not a crisis, but it's doing a lot: all routes, all scoring display logic, correlation analysis, API endpoints. Flask blueprints would be the natural split (e.g. `routes/training.py`, `routes/api.py`, `routes/correlations.py`). Only worth doing when we're next making significant changes to the routing layer.

---

## Ideas (not yet evaluated)

These have come up in conversation but haven't been scoped or designed. Capturing them here so they don't get lost.

- **Check-in flow improvements** — the checkin page was ported from healthdash; may need UX refinement for the nsight context
- **Notification/alerting** — push notifications for anomalies (HRV crash, missed workout, etc.) via PWA
- **Data export** — CSV/JSON export of daily_log for external analysis
- **Multi-device Garmin support** — currently assumes single device; may need adjustment if watch changes

---

## Infrastructure notes

| Component | Status | Notes |
|-----------|--------|-------|
| Server | docker-top (sysadmin) | Tailscale + local SSH |
| Web | nsight-web.service (gunicorn :5100) | 2 workers, restart=on-failure |
| Ingest | nsight-ingest.timer | Daily 10:00 UTC |
| Correlations | Friday only (in nsight-ingest) | + manual via regenerate button |
| DB | PostgreSQL 16 (healthdash container) | localhost:5432 |
| CI | GitHub Actions → git pull + pip install + restart | On push to main |
| Linger | Enabled for sysadmin | Required for user services without SSH |
