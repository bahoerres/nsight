# nsight standalone migration — roadmap

**Goal:** Absorb healthdash's backend (ingest, insights, schema, orchestration) into nsight. Decommission healthdash's web frontend. End state: nsight is the single repo for the entire health stack.

**Current state:** nsight serves all user-facing pages but depends on healthdash for data ingest (Garmin, Hevy, Cronometer), insight generation (daily/weekly/monthly), schema management, and nightly orchestration. Both apps read/write the same Postgres database (`healthdash` on localhost:5432).

---

## Phase 1: Absorb backend scripts

Copy healthdash's backend into nsight. No behavior changes — just moving files and fixing paths.

### 1.1 Ingest scripts

- Copy `ingest_garmin.py`, `ingest_hevy.py`, `cronometer/ingest_cronometer.py` into `nsight/ingest/`
- Each script uses `upsert_daily()` patterns with `ON CONFLICT (date) DO UPDATE` — fully idempotent, safe to re-run
- Update imports: `from tz import today` needs `tz.py` (nsight already has one — verify they match, use nsight's)
- Dependencies to add to `requirements.txt`: `garminconnect` (Garmin), `requests` (Hevy API)

### 1.2 Insight generation

- Copy `generate_insights.py` into `nsight/`
- Copy `athlete_context.txt` into `nsight/`
- Fix `generate_correlation_insight.py` — it currently tries to load athlete_context from `../healthdash/` path. Update to look in nsight's own directory
- The `run_correlations_for_display()` function already lives in nsight's `app.py` — the import in `generate_correlation_insight.py` already points there

### 1.3 Schema

- Copy `schema.sql` into `nsight/` (healthdash's copy was the source of truth)
- nsight's copy already has the `nsight_*` score columns added this session
- Verify the CHECK constraint on `insights.type` includes all types: `daily`, `weekly`, `monthly`, `correlation`, `sleep`, `recovery`

### 1.4 Derived metrics

- `correlations.py` from healthdash computes ACWR, rolling baselines, anomaly flags into `derived_daily`
- nsight's `scoring.py` already computes its own baselines via `fetch_baselines()` — these are query-time, not materialized
- Decision: `derived_daily` is only read by healthdash's old frontend (for anomaly badges). nsight doesn't use it. **Skip copying `correlations.py` for now** — the correlation analysis for the correlations page is already in nsight's `app.py` (`run_correlations_for_display`)

### 1.5 Docker Compose

- Copy `docker-compose.yml` into `nsight/` — this runs the Postgres container
- The database name stays `healthdash` (renaming is cosmetic and risky for no benefit)

---

## Phase 2: Orchestration

Replace healthdash's ingest pipeline with nsight's own.

### 2.1 New ingest script

Create `nsight/ingest.sh` (replaces `healthdash/healthdash-ingest.sh`):

```
Garmin ingest (--days 2)
Hevy ingest (--days 30)
Cronometer export + ingest
Materialize nsight scores (--days 2)     ← already exists
Correlation insight (weekly, Mondays)     ← already exists
Daily insight generation
Weekly insight (Mondays)
Monthly insight (1st of month)
```

All paths reference `nsight/` instead of bouncing between repos.

### 2.2 Credentials

- Copy `.env` from healthdash → nsight (or symlink)
- Keys needed: `DATABASE_URL`, `GARMIN_EMAIL`, `GARMIN_PASSWORD`, `HEVY_API_KEY`, `CRONOMETER_EMAIL`, `CRONOMETER_PASSWORD`, `ANTHROPIC_API_KEY`
- nsight's existing `.env` only has `DATABASE_URL` and `TZ` — add the rest

### 2.3 Systemd services

Create new systemd units in `nsight/systemd/`:

| Unit | Replaces | Schedule |
|------|----------|----------|
| `nsight-web.service` | (already exists) | always-on |
| `nsight-ingest.service` + `.timer` | `healthdash-ingest.*` | daily 10:00 UTC |
| `nsight-insights.service` + `.timer` | `healthdash-insights.*` | daily (after ingest) |
| `nsight-insights-weekly.service` + `.timer` | `healthdash-insights-weekly.*` | Sundays |
| `nsight-insights-monthly.service` + `.timer` | `healthdash-insights-monthly.*` | 1st of month |

Since `ingest.sh` already handles insight generation at the end of the pipeline, the separate insight timers may be unnecessary. Consider consolidating into just `nsight-ingest.*` that runs the full pipeline.

---

## Phase 3: Deploy and cut over

### 3.1 Deploy nsight standalone

- Push all changes to nsight repo
- SSH to docker-top, `git pull` in `/home/sysadmin/stacks/nsight/`
- `pip install -r requirements.txt` (new deps: garminconnect, etc.)
- Copy `.env` with all credentials
- Install new systemd units, enable timers
- Run `ingest.sh` manually once to verify end-to-end

### 3.2 Run backfills on server

```bash
# Add nsight score columns and backfill
python materialize_scores.py --days 365

# Generate first correlation insight
python generate_correlation_insight.py --force
```

### 3.3 Verify

- [ ] Ingest pipeline runs successfully (all 3 sources)
- [ ] Scores materialize correctly
- [ ] Daily insight generates
- [ ] All pages load with current data
- [ ] Systemd timer fires on schedule (check with `systemctl --user list-timers`)

### 3.4 Decommission healthdash frontend

```bash
# On docker-top:
systemctl --user stop healthdash-web
systemctl --user disable healthdash-web
systemctl --user stop healthdash-ingest.timer
systemctl --user disable healthdash-ingest.timer
# ... same for all healthdash-insights-* timers
```

### 3.5 Update Caddy

- Remove `healthdash.blakehoerres.com → :5200` route
- Point domain to nsight's `:5100` (or add `nsight.blakehoerres.com`)

---

## Phase 4: Cleanup

- Archive healthdash repo on GitHub (Settings → Archive)
- Remove healthdash deploy workflow from GitHub Actions
- Keep `docker-compose.yml` for Postgres (stays running regardless)
- Delete healthdash systemd units from server

---

## Gotchas

1. **Garmin token cache** — `ingest_garmin.py` stores a session token at `GARMIN_TOKEN_PATH` (defaults to `~/.garmin_token`). This is a file path, not repo-relative. Should work unchanged.

2. **Cronometer binary** — `cronometer-export` is a standalone binary at `~/.local/bin/cronometer-export`. Not repo-dependent, just needs to be on PATH.

3. **generate_correlation_insight.py imports `from app import run_correlations_for_display`** — this currently works because it runs from nsight's directory. After moving to `nsight/`, ensure it still resolves correctly (it should, since `app.py` is in the same directory).

4. **insights table CHECK constraint** — the live DB may still have the old constraint (`daily`, `weekly`, `monthly` only). `generate_correlation_insight.py` already handles this with an `ALTER TABLE` on first run, but verify it worked.

5. **`app.py` is 2300+ lines** — not a blocker for this migration, but worth noting for a future refactor into blueprints. Don't refactor during migration.

6. **`derived_daily` table** — healthdash's `correlations.py` populates ACWR and anomaly flags here. nsight doesn't read from this table directly (it computes ACWR inline in `app.py`). If you want the anomaly flags later, port `correlations.py` then. Not needed for MVP.

---

## File moves summary

```
healthdash/                    →  nsight/
├── ingest_garmin.py           →  ingest/ingest_garmin.py
├── ingest_hevy.py             →  ingest/ingest_hevy.py
├── cronometer/                →  ingest/cronometer/
├── generate_insights.py       →  generate_insights.py
├── athlete_context.txt        →  athlete_context.txt
├── schema.sql                 →  schema.sql
├── docker-compose.yml         →  docker-compose.yml
├── .env                       →  .env (merge with existing)
├── healthdash-ingest.sh       →  ingest.sh (rewritten)
└── systemd/                   →  systemd/ (new units)

NOT moved (no longer needed):
├── app.py                     ✗  nsight has its own
├── templates/                 ✗  nsight has its own
├── static/                    ✗  nsight has its own
├── correlations.py            ✗  logic already in nsight app.py
├── kahunas_prep.py            ✗  legacy, ported to app.py
├── debug_*.py                 ✗  scratch files
└── Caddyfile                  ✗  update in-place on server
```

---

## Time estimate

Phases 1-2 are ~90 minutes of file moves, path fixes, and script writing. Phase 3 is ~30 minutes of server work. Phase 4 is 10 minutes of cleanup. Total: one focused afternoon session.
