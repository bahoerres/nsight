# Hevy Body Measurements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest body measurements from Hevy API, store in a dedicated table, bridge weight to daily_log, and display on the training page via a carousel with PRs.

**Architecture:** New `body_measurements` table stores all Hevy fields in metric. Ingest fetches paginated body measurements from Hevy, upserts into the table, and bridges `weight_kg` to `daily_log.body_weight_lbs`. The training page's last section becomes a 2-slide carousel (PRs / Body Measurements) reusing the health page's carousel pattern.

**Tech Stack:** Python 3 / psycopg2 / requests (backend), Jinja2 / Chart.js (frontend), PostgreSQL (database on docker-top)

---

### Task 1: Add `body_measurements` table to schema

**Files:**
- Modify: `schema.sql` (append after line 167, before EOF)

- [ ] **Step 1: Add the CREATE TABLE statement**

Add this block at the end of `schema.sql`, before the final newline:

```sql
-- hevy: body measurements (composition + circumference)
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

- [ ] **Step 2: Commit**

```bash
git add schema.sql
git commit -m "feat: add body_measurements table to schema"
```

---

### Task 2: Add body measurement fetch functions to Hevy ingest

**Files:**
- Modify: `ingest_hevy.py` (add after `get_all_workouts()`, around line 95)

- [ ] **Step 1: Add `get_body_measurements_page()` function**

Add after the `get_all_workouts()` function (after line 95):

```python
# ---------------------------------------------------------------------------
# Body Measurements API
# ---------------------------------------------------------------------------


def get_body_measurements_page(page: int, page_size: int = 10) -> dict:
    r = requests.get(
        f"{HEVY_BASE}/body_measurements",
        headers=get_headers(),
        params={"page": page, "pageSize": page_size},
    )
    r.raise_for_status()
    return r.json()


def get_all_body_measurements(since: date = None) -> list[dict]:
    """Paginate through all body measurements, optionally filtering by date."""
    measurements = []
    page = 1
    while True:
        data = get_body_measurements_page(page)
        batch = data.get("body_measurements", [])
        if not batch:
            break

        for m in batch:
            date_str = m.get("date", "")
            if not date_str:
                continue
            m_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()

            if since and m_date < since:
                log.info(f"Reached cutoff date {since}, stopping body measurement pagination")
                return measurements

            measurements.append({**m, "_date": m_date})

        log.info(f"Body measurements page {page}: got {len(batch)} entries")

        total = data.get("page_count", 1)
        if page >= total:
            break
        page += 1

    return measurements
```

- [ ] **Step 2: Verify the function parses correctly**

```bash
python -c "import ingest_hevy; print('OK')"
```

Expected: `OK` (no syntax errors)

- [ ] **Step 3: Commit**

```bash
git add ingest_hevy.py
git commit -m "feat: add Hevy body measurement API fetch functions"
```

---

### Task 3: Add body measurement upsert with daily_log bridge

**Files:**
- Modify: `ingest_hevy.py` (add after `upsert_sets()`, around line 515)

- [ ] **Step 1: Add the `upsert_body_measurements()` function**

Add after the `upsert_sets()` function:

```python
# Column mapping: normalize Hevy API field names to our DB column names.
# Hevy uses inconsistent naming — some fields have _cm suffix, some don't.
MEASUREMENT_FIELD_MAP = {
    "weight_kg": "weight_kg",
    "lean_mass_kg": "lean_mass_kg",
    "fat_percent": "fat_percent",
    "neck_cm": "neck_cm",
    "shoulder_cm": "shoulder_cm",
    "chest_cm": "chest_cm",
    "left_bicep_cm": "left_bicep_cm",
    "right_bicep_cm": "right_bicep_cm",
    "left_forearm_cm": "left_forearm_cm",
    "right_forearm_cm": "right_forearm_cm",
    "abdomen": "abdomen_cm",
    "abdomen_cm": "abdomen_cm",
    "waist": "waist_cm",
    "waist_cm": "waist_cm",
    "hips": "hips_cm",
    "hips_cm": "hips_cm",
    "left_thigh": "left_thigh_cm",
    "left_thigh_cm": "left_thigh_cm",
    "right_thigh": "right_thigh_cm",
    "right_thigh_cm": "right_thigh_cm",
    "left_calf": "left_calf_cm",
    "left_calf_cm": "left_calf_cm",
    "right_calf": "right_calf_cm",
    "right_calf_cm": "right_calf_cm",
}

DB_MEASUREMENT_COLS = [
    "weight_kg", "lean_mass_kg", "fat_percent",
    "neck_cm", "shoulder_cm", "chest_cm",
    "left_bicep_cm", "right_bicep_cm",
    "left_forearm_cm", "right_forearm_cm",
    "abdomen_cm", "waist_cm", "hips_cm",
    "left_thigh_cm", "right_thigh_cm",
    "left_calf_cm", "right_calf_cm",
]


def upsert_body_measurements(conn, measurements: list[dict]) -> int:
    """Upsert body measurements and bridge weight to daily_log."""
    if not measurements:
        return 0

    count = 0
    for m in measurements:
        m_date = m["_date"]

        # Map API fields to DB columns
        values = {}
        for api_field, db_col in MEASUREMENT_FIELD_MAP.items():
            val = m.get(api_field)
            if val is not None and db_col not in values:
                values[db_col] = float(val)

        if not values:
            continue

        # Build dynamic upsert
        cols = list(values.keys())
        placeholders = ", ".join([f"%({c})s" for c in cols])
        col_list = ", ".join(cols)
        update_set = ", ".join([f"{c} = EXCLUDED.{c}" for c in cols])

        params = {"date": m_date}
        params.update(values)

        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO body_measurements (date, {col_list})
                VALUES (%(date)s, {placeholders})
                ON CONFLICT (date) DO UPDATE SET
                    {update_set},
                    updated_at = now()
                """,
                params,
            )

        # Bridge weight to daily_log
        weight_kg = values.get("weight_kg")
        if weight_kg:
            weight_lbs = round(weight_kg * 2.20462, 1)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO daily_log (date, body_weight_lbs)
                    VALUES (%s, %s)
                    ON CONFLICT (date) DO UPDATE SET
                        body_weight_lbs = EXCLUDED.body_weight_lbs,
                        updated_at = now()
                    """,
                    (m_date, weight_lbs),
                )

        count += 1

    conn.commit()
    return count
```

- [ ] **Step 2: Verify no syntax errors**

```bash
python -c "import ingest_hevy; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add ingest_hevy.py
git commit -m "feat: add body measurement upsert with daily_log weight bridge"
```

---

### Task 4: Wire body measurements into the ingest main() and CLI

**Files:**
- Modify: `ingest_hevy.py` — `main()` function (lines 532-569)

- [ ] **Step 1: Add body measurement ingest before workout processing**

In the `main()` function, add body measurement fetch and upsert **before** the workout fetch (before `workouts = get_all_workouts(since=since)` at line 547). This ensures `daily_log.body_weight_lbs` is populated before `get_recent_bodyweight()` is called.

Insert this block before the `workouts = get_all_workouts(since=since)` line:

```python
    # ── Body measurements ──────────────────────────────────────────
    body_measurements = get_all_body_measurements(since=since)
    log.info(f"Retrieved {len(body_measurements)} body measurements")

    if body_measurements:
        conn = get_db()
        bm_count = upsert_body_measurements(conn, body_measurements)
        log.info(f"Upserted {bm_count} body measurement rows")
        conn.close()

    # ── Workouts ───────────────────────────────────────────────────
```

- [ ] **Step 2: Verify no syntax errors**

```bash
python -c "import ingest_hevy; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add ingest_hevy.py
git commit -m "feat: wire body measurements into Hevy ingest pipeline"
```

---

### Task 5: Comment out Garmin weight extraction

**Files:**
- Modify: `ingest_garmin.py` — `fetch_day()` function (line 210)

- [ ] **Step 1: Comment out the extract_weight call**

In `fetch_day()`, comment out line 210 and the sleep after it:

Replace:
```python
    row.update(extract_weight(client, day))
    time.sleep(0.5)
```

With:
```python
    # Bodyweight now sourced from Hevy body measurements.
    # Uncomment if using a Garmin scale in the future.
    # row.update(extract_weight(client, day))
    # time.sleep(0.5)
```

- [ ] **Step 2: Commit**

```bash
git add ingest_garmin.py
git commit -m "feat: comment out Garmin weight fetch, now sourced from Hevy"
```

---

### Task 6: Update `get_recent_bodyweight()` docstring

**Files:**
- Modify: `ingest_hevy.py` — `get_recent_bodyweight()` function (line 313)

- [ ] **Step 1: Update the docstring to reflect new source**

Replace:
```python
def get_recent_bodyweight(conn) -> float | None:
    """Get most recent body_weight_lbs from daily_log (Garmin source)."""
```

With:
```python
def get_recent_bodyweight(conn) -> float | None:
    """Get most recent body_weight_lbs from daily_log (bridged from Hevy body measurements)."""
```

- [ ] **Step 2: Commit**

```bash
git add ingest_hevy.py
git commit -m "docs: update get_recent_bodyweight docstring for Hevy source"
```

---

### Task 7: Add body measurement data to training route

**Files:**
- Modify: `app.py` — `training()` function (add before the `finally:` block at line 1061)

- [ ] **Step 1: Add weight trend query (90 days)**

Add this block before the `finally: conn.close()` line in the `training()` route:

```python
        # ── Body measurements (90-day weight trend + latest snapshot) ──
        weight_labels = []
        weight_data = []
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, weight_kg
                FROM body_measurements
                WHERE weight_kg IS NOT NULL
                  AND date >= %s
                ORDER BY date ASC
            """, (today - timedelta(days=89),))
            wt_rows = cur.fetchall()

        for r in wt_rows:
            weight_labels.append(r["date"].strftime("%-m/%-d"))
            weight_data.append(round(float(r["weight_kg"]) * 2.20462, 1))

        # Latest measurements snapshot (most recent row with any data)
        latest_measurements = {}
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM body_measurements
                ORDER BY date DESC LIMIT 1
            """)
            row = cur.fetchone()

        if row:
            cm_to_in = 0.393701
            measurement_fields = [
                ("neck_cm", "Neck"),
                ("shoulder_cm", "Shoulders"),
                ("chest_cm", "Chest"),
                ("left_bicep_cm", "L Bicep"),
                ("right_bicep_cm", "R Bicep"),
                ("left_forearm_cm", "L Forearm"),
                ("right_forearm_cm", "R Forearm"),
                ("abdomen_cm", "Abdomen"),
                ("waist_cm", "Waist"),
                ("hips_cm", "Hips"),
                ("left_thigh_cm", "L Thigh"),
                ("right_thigh_cm", "R Thigh"),
                ("left_calf_cm", "L Calf"),
                ("right_calf_cm", "R Calf"),
            ]
            for col, label in measurement_fields:
                val = row.get(col)
                if val is not None:
                    latest_measurements[label] = round(float(val) * cm_to_in, 1)

            # Include weight in lbs and the snapshot date
            if row.get("weight_kg"):
                latest_measurements["_weight_lbs"] = round(float(row["weight_kg"]) * 2.20462, 1)
            latest_measurements["_date"] = row["date"].strftime("%b %-d, %Y")
```

- [ ] **Step 2: Pass new data to the template**

Find the `return render_template("training.html", ...)` call and add these new variables to it:

```python
        weight_labels=weight_labels,
        weight_data=weight_data,
        latest_measurements=latest_measurements,
```

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: add body measurement data to training route"
```

---

### Task 8: Build the carousel HTML on the training page

**Files:**
- Modify: `templates/training.html` — replace the Personal Records section (lines 471-504)

- [ ] **Step 1: Replace the Personal Records section with a carousel**

Replace lines 471-504 (the entire `{# ── Personal Records ──` section through the closing `</section>`) with:

```html
{# ── Personal Records / Body Measurements Carousel ───────────────── #}
<section>
  <div class="section-header mt-section">
    <div class="carousel-header-row">
      <button class="carousel-arrow training-carousel-prev" aria-label="Previous slide">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="15 18 9 12 15 6"/>
        </svg>
      </button>
      <h3 class="section-title" id="training-carousel-title">Personal Records</h3>
      <button class="carousel-arrow training-carousel-next" aria-label="Next slide">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="9 18 15 12 9 6"/>
        </svg>
      </button>
    </div>
  </div>

  {# ── Slide 1: Personal Records ──────────────────────────────────── #}
  <div class="training-carousel-slide training-carousel-active" data-title="Personal Records">
    <div class="card pr-table-card">
      {% if personal_records %}
      <table class="pr-table">
        <thead>
          <tr>
            <th>#</th>
            <th>Exercise</th>
            <th>Weight</th>
            <th>Reps</th>
            <th>Date</th>
          </tr>
        </thead>
        <tbody>
          {% for pr in personal_records %}
          <tr>
            <td class="pr-rank {{ 'pr-rank-top' if loop.index <= 3 }}">{{ loop.index }}</td>
            <td class="pr-exercise">{{ pr.exercise }}</td>
            <td class="pr-weight">{{ "{:,.0f}".format(pr.weight) }} lbs</td>
            <td class="pr-reps">{{ pr.reps }}</td>
            <td class="pr-date">{{ pr.date }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      {% else %}
      <p style="color: var(--text-muted);">No personal records found.</p>
      {% endif %}
    </div>
  </div>

  {# ── Slide 2: Body Measurements ─────────────────────────────────── #}
  <div class="training-carousel-slide" data-title="Body Measurements">
    <div class="card">
      {% if weight_data %}
      <div class="chart-wrap" style="padding: 16px;">
        <canvas id="weight-trend-chart"></canvas>
      </div>
      {% endif %}

      {% if latest_measurements %}
      <div class="measurements-grid">
        {% if latest_measurements._weight_lbs %}
        <div class="measurement-current-weight">
          <span class="measurement-weight-value">{{ latest_measurements._weight_lbs }}</span>
          <span class="measurement-weight-unit">lbs</span>
          <span class="measurement-weight-date">as of {{ latest_measurements._date }}</span>
        </div>
        {% endif %}
        <div class="measurement-items">
          {% for label, val in latest_measurements.items() if not label.startswith('_') %}
          <div class="measurement-item">
            <span class="measurement-label">{{ label }}</span>
            <span class="measurement-value">{{ val }}"</span>
          </div>
          {% endfor %}
        </div>
      </div>
      {% elif not weight_data %}
      <p style="color: var(--text-muted); padding: 24px;">No body measurements found. Log measurements in Hevy to see trends here.</p>
      {% endif %}
    </div>
  </div>
</section>
```

- [ ] **Step 2: Commit**

```bash
git add templates/training.html
git commit -m "feat: add carousel with PRs and body measurements slides"
```

---

### Task 9: Add carousel and measurements CSS

**Files:**
- Modify: `templates/training.html` — `<style>` block (add before the responsive media queries at line 319)

- [ ] **Step 1: Add carousel header and slide styles**

Add before the `/* ── Responsive ──` comment:

```css
  /* ── Training Carousel ─────────────────────────────────────────── */
  .carousel-header-row {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .training-carousel-slide {
    display: none;
  }

  .training-carousel-slide.training-carousel-active {
    display: block;
  }

  /* ── Body Measurements Grid ────────────────────────────────────── */
  .measurements-grid {
    padding: 20px 24px;
  }

  .measurement-current-weight {
    display: flex;
    align-items: baseline;
    gap: 6px;
    margin-bottom: 16px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--border-subtle);
  }

  .measurement-weight-value {
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.8rem;
    font-weight: 700;
    color: var(--yellow);
  }

  .measurement-weight-unit {
    font-size: 0.9rem;
    color: var(--text-muted);
  }

  .measurement-weight-date {
    font-size: 0.75rem;
    color: var(--text-muted);
    margin-left: auto;
  }

  .measurement-items {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
  }

  .measurement-item {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding: 6px 0;
  }

  .measurement-label {
    font-size: 0.8rem;
    color: var(--text-muted);
  }

  .measurement-value {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.88rem;
    font-weight: 600;
    color: var(--text-primary);
  }
```

- [ ] **Step 2: Add responsive rules for the measurement grid**

Inside the `@media (max-width: 600px)` block, add:

```css
    .measurement-items {
      grid-template-columns: repeat(2, 1fr);
    }
```

- [ ] **Step 3: Commit**

```bash
git add templates/training.html
git commit -m "style: add carousel and body measurements grid CSS"
```

---

### Task 10: Add carousel JS and weight trend chart

**Files:**
- Modify: `templates/training.html` — `{% block scripts %}` section (add after the muscle group chart code, near end of the script block)

- [ ] **Step 1: Add the weight trend chart initialization**

Add at the end of the `DOMContentLoaded` callback, before the closing `});`:

```javascript
  // ── Weight Trend Chart (90 days) ────────────────────────────────
  var weightLabels = {{ weight_labels | tojson }};
  var weightData   = {{ weight_data | tojson }};

  if (weightLabels.length > 0) {
    createLineChart('weight-trend-chart', weightLabels, [{
      label: 'Weight (lbs)',
      data: weightData,
      borderColor: '#d3869b',
      backgroundColor: 'rgba(211, 134, 155, 0.1)',
      fill: true,
      pointRadius: 2,
      pointHoverRadius: 5,
      pointBackgroundColor: '#d3869b',
    }], {
      scales: {
        x: { grid: { display: false } },
        y: {
          beginAtZero: false,
          ticks: {
            maxTicksLimit: 5,
            callback: function(val) { return val + ' lbs'; }
          }
        }
      },
      plugins: {
        tooltip: {
          callbacks: {
            label: function(item) { return item.raw + ' lbs'; }
          }
        }
      }
    });
  }

  // ── Training Carousel (PRs / Body Measurements) ─────────────────
  var tSlides = document.querySelectorAll('.training-carousel-slide');
  var tTitle  = document.getElementById('training-carousel-title');
  var tCurrent = 0;

  function showTrainingSlide(idx) {
    tSlides.forEach(function(s) { s.classList.remove('training-carousel-active'); });
    tCurrent = ((idx % tSlides.length) + tSlides.length) % tSlides.length;
    tSlides[tCurrent].classList.add('training-carousel-active');
    if (tTitle) {
      tTitle.textContent = tSlides[tCurrent].dataset.title || '';
    }
  }

  var tPrev = document.querySelector('.training-carousel-prev');
  var tNext = document.querySelector('.training-carousel-next');
  if (tPrev) tPrev.addEventListener('click', function() { showTrainingSlide(tCurrent - 1); });
  if (tNext) tNext.addEventListener('click', function() { showTrainingSlide(tCurrent + 1); });
```

- [ ] **Step 2: Commit**

```bash
git add templates/training.html
git commit -m "feat: add weight trend chart and carousel JS to training page"
```

---

### Task 11: Deploy and verify

**Files:** None (deployment only)

- [ ] **Step 1: Deploy using /deploy skill**

Run `/deploy` which will:
1. Push to main
2. CI deploys to docker-top
3. SSH to run schema migration (creates `body_measurements` table)
4. Verify service is running

Since `schema.sql` changed, the deploy needs to apply the migration:
```bash
ssh docker-top "cd /home/sysadmin/stacks/nsight && .venv/bin/python -c \"
import psycopg2, os
conn = psycopg2.connect(os.environ['DATABASE_URL'])
with open('schema.sql') as f:
    conn.cursor().execute(f.read())
conn.commit()
print('Schema applied')
\""
```

- [ ] **Step 2: Verify the table exists**

```bash
ssh docker-top "cd /home/sysadmin/stacks/nsight && .venv/bin/python -c \"
import psycopg2, os
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute(\\\"SELECT column_name FROM information_schema.columns WHERE table_name = 'body_measurements' ORDER BY ordinal_position\\\")
print([r[0] for r in cur.fetchall()])
\""
```

Expected: List of all column names from the `body_measurements` table.

- [ ] **Step 3: Run a test ingest**

```bash
ssh docker-top "cd /home/sysadmin/stacks/nsight && .venv/bin/python ingest_hevy.py --days 90"
```

Check logs for body measurement lines:
- `Retrieved N body measurements`
- `Upserted N body measurement rows`

- [ ] **Step 4: Verify the training page in the browser**

Open the training page. Scroll to the last section. Verify:
- Carousel arrows appear flanking the section title
- "Personal Records" slide shows the existing PR table
- Clicking the arrow switches to "Body Measurements"
- Weight trend chart renders (if weight data was ingested)
- Circumference grid shows values in inches (if circumference data exists)
- Empty state message shows if no measurements exist yet

- [ ] **Step 5: Verify home page weight sparkline still works**

Open the home page. Confirm the weight sparkline and value still display correctly — they should now be populated from Hevy-bridged data in `daily_log.body_weight_lbs`.
