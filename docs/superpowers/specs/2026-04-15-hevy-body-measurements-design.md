# Hevy Body Measurements Integration

## Problem

Bodyweight is currently sourced from Garmin's body composition API via manual logging in Garmin Connect. This creates an unnecessary dependency — workout data comes from Hevy, but the bodyweight needed for bodyweight exercise calculations comes from a completely separate integration. Hevy now offers a full body measurement API (`GET /v1/body_measurements`) with weight, body fat, lean mass, and 14 circumference fields. Moving body measurements to Hevy simplifies the workflow and opens up circumference tracking.

## Design

### Database: New `body_measurements` table

```sql
CREATE TABLE IF NOT EXISTS body_measurements (
    date            DATE PRIMARY KEY,

    -- composition
    weight_kg       NUMERIC(5,2),
    lean_mass_kg    NUMERIC(5,2),
    fat_percent     NUMERIC(4,1),

    -- circumference (all in cm, matching Hevy API)
    neck_cm         NUMERIC(5,1),
    shoulder_cm     NUMERIC(5,1),
    chest_cm        NUMERIC(5,1),
    left_bicep_cm   NUMERIC(5,1),
    right_bicep_cm  NUMERIC(5,1),
    left_forearm_cm NUMERIC(5,1),
    right_forearm_cm NUMERIC(5,1),
    abdomen_cm      NUMERIC(5,1),
    waist_cm        NUMERIC(5,1),
    hips_cm         NUMERIC(5,1),
    left_thigh_cm   NUMERIC(5,1),
    right_thigh_cm  NUMERIC(5,1),
    left_calf_cm    NUMERIC(5,1),
    right_calf_cm   NUMERIC(5,1),

    -- metadata
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_body_measurements_date ON body_measurements(date);
```

Key decisions:
- **Store in metric** (kg/cm) matching the Hevy API natively. Convert to imperial at display time. Avoids lossy round-trips.
- **DATE primary key** — same pattern as `daily_log`. One measurement entry per day.
- **Bridge to daily_log:** After upserting measurements, write `body_weight_lbs` into `daily_log` via `weight_kg * 2.20462`. This keeps all existing bodyweight-dependent code working (`get_recent_bodyweight()`, bodyweight exercise substitution) with zero changes.

### Hevy API Integration

New functions in `ingest_hevy.py`, following the existing `get_workouts_page()` pattern:

**Fetch:**
```python
def get_body_measurements_page(page: int, page_size: int = 10) -> dict:
    """GET /v1/body_measurements with pagination."""
```

**Pagination wrapper:**
```python
def get_all_body_measurements(since: date | None = None) -> list[dict]:
    """Paginate through all body measurements, filter client-side by date."""
```

**Upsert:**
```python
def upsert_body_measurements(conn, measurements: list[dict]) -> int:
    """INSERT ON CONFLICT into body_measurements table.
    Also bridges weight_kg to daily_log.body_weight_lbs for each row with weight data."""
```

Expected API response structure (based on Hevy docs and existing endpoint patterns):
```json
{
  "page": 1,
  "page_count": 3,
  "body_measurements": [
    {
      "date": "2026-04-15",
      "weight_kg": 86.2,
      "lean_mass_kg": 72.1,
      "fat_percent": 16.3,
      "neck_cm": 40.5,
      "shoulder_cm": 120.0,
      "chest_cm": 102.3,
      "left_bicep_cm": 36.2,
      "right_bicep_cm": 36.5,
      "left_forearm_cm": 30.1,
      "right_forearm_cm": 30.3,
      "abdomen": 86.2,
      "waist": 84.1,
      "hips": 98.5,
      "left_thigh": 58.1,
      "right_thigh": 58.4,
      "left_calf": 38.0,
      "right_calf": 38.2
    }
  ]
}
```

Note: Some fields in the Hevy schema use `_cm` suffix and some don't (e.g. `abdomen`, `waist`, `hips`). The ingest code must handle both naming conventions and normalize into the `_cm` suffixed columns in our table.

### Garmin Changes

Comment out the `extract_weight()` call in `ingest_garmin.py` (preserve the function itself):
```python
# Bodyweight now sourced from Hevy body measurements.
# Uncomment if using a Garmin scale in the future.
# weight_data = extract_weight(client, day)
```

### Ingest Order

The main ingest script runs in this order:
1. Garmin (sleep, HR, stress, etc. — weight call commented out)
2. **Hevy body measurements (new)** — populates `body_measurements` + bridges to `daily_log.body_weight_lbs`
3. Hevy workouts (existing) — `get_recent_bodyweight()` now has fresh data
4. Cronometer (unchanged)

### Training Page UI

The last section on the training page becomes a carousel with two slides, reusing the health page's existing carousel pattern (CSS classes, JS toggle logic).

**Section header:** Title updates to match active slide — "Personal Records" / "Body Measurements" — with left/right arrow buttons flanking it.

**Slide 1: Personal Records**
Existing PR table, completely unchanged.

**Slide 2: Body Measurements**
Two parts stacked vertically:

1. **Weight trend line chart (90 days)**
   - Full Chart.js line chart, same visual style as the volume trend chart
   - Single axis: weight in lbs
   - 90-day window — long enough to see real body composition trends
   - Date labels on X axis, same format as other charts (M/D)
   - Hover tooltips with exact values

2. **Latest circumference snapshot grid**
   - Compact grid showing the most recent measurement values
   - Displayed in imperial (inches), converted from stored cm
   - Only shows fields that have data (skip nulls)
   - Layout example:
     ```
     Neck     15.9"    Chest    40.3"    Waist   33.1"
     Shoulders 47.2"   Hips     38.8"    Abdomen 33.9"
     L Bicep  14.3"    R Bicep  14.4"
     L Forearm 11.9"   R Forearm 11.9"
     L Thigh  22.9"    R Thigh  23.0"
     L Calf   15.0"    R Calf   15.0"
     ```

**Empty state:** "No body measurements found. Log measurements in Hevy to see trends here."

### Backend Data for Training Route

The `training()` route in `app.py` adds two new queries:

```python
# 90 days of weight for trend chart
weight_trend_labels, weight_trend_data = ...  # from body_measurements

# Latest measurement row for snapshot grid
latest_measurements = ...  # most recent row from body_measurements, converted to imperial
```

### What Stays Unchanged

- **Home page** — weight sparkline keeps working via `daily_log.body_weight_lbs` (now bridged from Hevy instead of Garmin)
- **`get_recent_bodyweight()`** — still queries `daily_log.body_weight_lbs`, works as before
- **Bodyweight exercise substitution** — unchanged, still reads from `get_recent_bodyweight()`
- **All other pages and scores** — no changes

### Deployment & Migration

The database (PostgreSQL `healthdash`) runs on `docker-top`, not locally. There is no local DB instance.

- **Schema migration:** Add the `CREATE TABLE IF NOT EXISTS body_measurements` block to `schema.sql`. The table will be created on deploy via the existing migration path (SSH to docker-top, run schema or `generate_insights.py --rolling`).
- **No data migration needed:** Existing `daily_log.body_weight_lbs` data from Garmin stays as-is. New weight data will come from Hevy going forward. No backfill required.
- **Testing:** Code changes are verified on the remote after deploy. Use `/deploy` skill which handles commit, push, CI, SSH migration, and health check.

### Unit Conversions

| Direction | Conversion |
|-----------|-----------|
| API → DB (weight) | Store as-is (kg) |
| API → DB (circumference) | Store as-is (cm) |
| DB → display (weight) | kg * 2.20462 = lbs |
| DB → display (circumference) | cm * 0.393701 = inches |
| DB → daily_log bridge | kg * 2.20462 → body_weight_lbs |
