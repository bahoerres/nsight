# nsight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **REQUIRED SKILLS:** Before implementing any frontend template or CSS, invoke the `frontend-design` skill. For any charting/visualization work, consider `web-design-guidelines`. For deployment, use `deploy-to-vercel` or manual systemd setup as appropriate.

**Goal:** Rebuild healthdash's frontend as a Sonar-inspired narrative health dashboard with gruvbox dark theming, composite scoring, and plain-english summaries.

**Architecture:** Flask + Jinja2 frontend reading from the same PostgreSQL database as healthdash. New `scoring.py` module computes 0-100 category scores at request time using 90-day rolling baselines. Hero banners, score circles, trend cards, and performance donuts replace the current data-dense layout. Templates use block inheritance from a shared base with sidebar nav and hero slot.

**Tech Stack:** Python 3 / Flask / Jinja2 / Chart.js 4 (CDN) / Vanilla JS / PostgreSQL 16 / psycopg2 / python-dotenv

**Required Skills (invoke before relevant work):**
- `frontend-design` — Before writing ANY template HTML or CSS. This is the primary design skill.
- `web-design-guidelines` — For accessibility review after each page is complete.
- `superpowers:verification-before-completion` — Before claiming any task is done.
- `superpowers:requesting-code-review` — After completing Tasks 1-2 (foundation) and after Task 8 (all pages done).

**Spec:** `/home/blake/code/nsight/CLAUDE_CODE_BRIEFING.md`
**Design reference:** `Screenshot*.png` files in `/home/blake/code/nsight/`
**Existing backend:** `/home/blake/code/healthdash/` (app.py, schema.sql, generate_insights.py, athlete_context.txt)

---

## Key Design Decisions

1. **Sleep date alignment**: Wake-date convention (same as healthdash). Sleep shown on the date you woke up.
2. **Sleep score**: nsight's composite replaces Garmin's `sleep_score` everywhere. Garmin's score is not displayed.
3. **Score computation**: Runtime per request (no new tables). Single-user, indexed queries, fast enough.
4. **Hero text**: Pull from `insights` table if today's daily insight exists; fall back to template-generated strings.
5. **Codebase independence**: Copy `tz.py` and any shared utilities into nsight. No symlinks to healthdash.
6. **Hero images**: Copied from `~/Wallpapers/` — nature/landscape images that complement gruvbox palette.
7. **No TDD**: This is a frontend build with no existing test infra. Verify against running app with real data.

## Hero Image Assignments

| Page | Image | Source |
|------|-------|--------|
| Home | `forest-foggy-misty-cloudy.png` | Moody forest, dark greens |
| Health | `lake.jpg` | Autumn lake, warm gruvbox tones |
| Sleep | `forest-2.jpg` | Misty forest, golden light filtering through |
| Recovery | `cabin.jpg` | Forest cabin, peaceful greens |
| Training | `river.jpg` | Dark earthy tones, cascading mountain stream |
| Nutrition | `forest-mountain-cloudy-valley.png` | Dramatic clouds over valley |
| Check-in | `forest-moss.png` | Deep greens, moss floor |
| Insights | `above-the-mountains.png` | Stylized mountain vista above clouds |

---

## File Structure

```
/home/blake/code/nsight/
├── app.py                      # Flask app — routes, queries, template context
├── scoring.py                  # Category score functions (Sleep, Recovery, Training, Nutrition)
├── tz.py                       # Timezone helper (copied from healthdash)
├── requirements.txt            # Python dependencies
├── .env                        # Database URL, API key, timezone (copied from healthdash)
├── .gitignore                  # Excludes .env, __pycache__, .venv, *.pyc
├── static/
│   ├── style.css               # Gruvbox dark stylesheet — layout, cards, heroes, responsive
│   ├── charts.js               # Chart.js config — sparklines, donuts, bar/line factories
│   ├── app.js                  # Sidebar toggle, mobile nav, interactions
│   ├── manifest.json           # PWA metadata (stub)
│   ├── sw.js                   # Service worker (stub)
│   └── heroes/                 # Hero banner images (copied from ~/Wallpapers/)
│       ├── home.jpg
│       ├── health.jpg
│       ├── sleep.jpg
│       ├── recovery.jpg
│       ├── training.jpg
│       ├── nutrition.jpg
│       ├── checkin.jpg
│       └── insights.jpg
│   └── icons/                  # PWA icons (favicon, apple-touch-icon, etc.)
├── templates/
│   ├── base.html               # Layout shell — sidebar nav, hero slot, content area
│   ├── components/
│   │   ├── score_circle.html   # Macro: SVG score ring with number + label
│   │   ├── trend_card.html     # Macro: metric value + sparkline + avg + status pill
│   │   ├── hero.html           # Macro: full-width hero banner with overlay text
│   │   └── donut.html          # Macro: 30-day performance donut (Good/Fair/Poor)
│   ├── home.html
│   ├── health.html
│   ├── sleep.html
│   ├── recovery.html
│   ├── training.html
│   ├── nutrition.html
│   ├── checkin.html
│   ├── insights.html
│   └── correlations.html
└── docs/
    └── plans/
        └── 2026-03-25-nsight-build.md  # This file
```

---

## Task 1: Project Scaffolding & Base Layout

**Files:**
- Create: `app.py`, `tz.py`, `requirements.txt`, `.env`
- Create: `static/style.css`, `static/app.js`, `static/manifest.json`, `static/sw.js`
- Create: `templates/base.html`
- Create: `templates/components/hero.html`, `templates/components/score_circle.html`, `templates/components/trend_card.html`, `templates/components/donut.html`
- Copy: Hero images into `static/heroes/`

This task produces a running Flask app with the sidebar nav, hero banner, and reusable component macros. No data yet — just the shell.

- [ ] **Step 1: Copy shared files from healthdash**

Copy `tz.py` from healthdash. Create `.env` with the same `DATABASE_URL` and `TZ` values. Create `requirements.txt`.

```
# requirements.txt
flask
gunicorn
markdown
markupsafe
psycopg2-binary
python-dotenv
```

- [ ] **Step 2: Create `.gitignore`**

```
.env
__pycache__/
*.pyc
.venv/
*.egg-info/
dist/
build/
```

- [ ] **Step 3: Copy and convert hero images**

Copy the 8 selected wallpapers from `~/Wallpapers/` into `static/heroes/`, converting to compressed JPEG and cropping to 1200x400 aspect ratio for hero banners. Name them by page: `home.jpg`, `health.jpg`, `sleep.jpg`, `recovery.jpg`, `training.jpg`, `nutrition.jpg`, `checkin.jpg`, `insights.jpg`.

Use ImageMagick:
```bash
mkdir -p static/heroes
convert ~/Wallpapers/forest-foggy-misty-cloudy.png -resize 1920x640^ -gravity center -extent 1920x640 -quality 85 static/heroes/home.jpg
convert ~/Wallpapers/lake.jpg -resize 1920x640^ -gravity center -extent 1920x640 -quality 85 static/heroes/health.jpg
convert ~/Wallpapers/forest-2.jpg -resize 1920x640^ -gravity center -extent 1920x640 -quality 85 static/heroes/sleep.jpg
convert ~/Wallpapers/cabin.jpg -resize 1920x640^ -gravity center -extent 1920x640 -quality 85 static/heroes/recovery.jpg
convert ~/Wallpapers/river.jpg -resize 1920x640^ -gravity center -extent 1920x640 -quality 85 static/heroes/training.jpg
convert ~/Wallpapers/forest-mountain-cloudy-valley.png -resize 1920x640^ -gravity center -extent 1920x640 -quality 85 static/heroes/nutrition.jpg
convert ~/Wallpapers/forest-moss.png -resize 1920x640^ -gravity center -extent 1920x640 -quality 85 static/heroes/checkin.jpg
convert ~/Wallpapers/above-the-mountains.png -resize 1920x640^ -gravity center -extent 1920x640 -quality 85 static/heroes/insights.jpg
```

- [ ] **Step 4: Create `templates/base.html`**

The layout shell. Key elements:
- `<head>` with DM Sans + JetBrains Mono from Google Fonts, Chart.js 4 CDN, `style.css`, `app.js`
- Left sidebar nav with icon + label for: Home, Health, Sleep, Recovery, Training, Nutrition, Check-in, Insights, Correlations
- Active page highlighting via `active_page` template variable
- `{% block hero %}` slot for page-specific hero banners
- `{% block content %}` slot for page body
- Mobile hamburger toggle for sidebar
- PWA manifest and sw.js registration

Sidebar icons: use simple Unicode/emoji or inline SVG. Keep it minimal — no icon library.

Sidebar nav items:
```
Home        (house icon)
Health      (heart icon)
Sleep       (moon icon)
Recovery    (battery icon)
Training    (dumbbell icon)
Nutrition   (apple icon)
Check-in    (clipboard icon)
Insights    (lightbulb icon)
Correlations (chart icon)
```

- [ ] **Step 5: Create `templates/components/hero.html`**

Jinja2 macro file. Usage: `{% from 'components/hero.html' import hero %}` then `{{ hero(image='home.jpg', title='Good evening, Blake', subtitle='March 25, 2026', summary='...', score=82, score_label='Overall') }}`.

Renders: full-width div with background-image, dark overlay (`--bg-hero-overlay`), overlaid text (title, subtitle, summary sentence), and optional score circle positioned right.

- [ ] **Step 6: Create `templates/components/score_circle.html`**

Jinja2 macro. SVG ring showing 0-100 score with color based on value (>80 green, 60-79 yellow, <60 red). Large number in center (JetBrains Mono), small label below. Accepts: `score`, `label`, `size` (default 120px), optional `href` for click-to-navigate.

- [ ] **Step 7: Create `templates/components/trend_card.html`**

Jinja2 macro. Renders a card with: metric name (label), current value (large, JetBrains Mono), unit, sparkline canvas placeholder (id for Chart.js), average value (small), and status pill (Normal/Above/Below with green/amber/red coloring). Accepts: `id`, `label`, `value`, `unit`, `avg`, `avg_label`, `status`, `status_color`.

- [ ] **Step 8: Create `templates/components/donut.html`**

Jinja2 macro. 30-day performance donut. Canvas element for Chart.js + legend showing Good/Fair/Poor counts. Accepts: `id`, `good`, `fair`, `poor`, `label`.

- [ ] **Step 9: Create `static/style.css`**

**IMPORTANT:** Invoke the `frontend-design` skill before writing CSS. Use `web-design-guidelines` for accessibility review.

Complete gruvbox dark stylesheet. Key sections:
- CSS custom properties (all colors, spacing, typography from briefing)
- Reset / base styles (body, headings, links)
- Sidebar layout (fixed left, 240px wide, collapsible)
- Hero banner (full-width, background-size cover, overlay, text positioning)
- Score circle (SVG ring styling)
- Card grid (CSS Grid, responsive: 1 col mobile, 2 col tablet, 3-4 col desktop)
- Trend card (padding, sparkline area, status pill)
- Donut card
- Status pills (.pill-normal, .pill-above, .pill-below)
- Insight chips
- Typography classes (.metric-value, .metric-label, .summary-text)
- Responsive breakpoints (mobile-first: 480px, 768px, 1024px)
- Page max-width 1200px with sidebar, content area ~900px

- [ ] **Step 10: Create `static/app.js`**

Sidebar toggle for mobile (hamburger button toggles `.sidebar-open` class on body). Active nav highlighting. Minimal — no framework.

- [ ] **Step 11: Create stub `static/manifest.json` and `static/sw.js`**

PWA stubs. manifest.json with app name "nsight", theme color `#1d2021`, icons placeholder. sw.js as empty service worker (just `self.addEventListener('fetch', ...)` passthrough).

- [ ] **Step 12: Create minimal `app.py` with base route**

Flask app with:
- `get_db()` using `cursor_factory=psycopg2.extras.RealDictCursor` (critical — all scoring.py and route code uses dict-style row access)
- `@app.route('/sw.js')` and `@app.route('/manifest.json')` static routes
- `@app.route('/')` returning a placeholder `home.html` that extends `base.html` with a hero and "Coming soon" content
- `if __name__ == '__main__': app.run(port=5100, debug=True)`

- [ ] **Step 13: Verify the shell runs**

```bash
cd /home/blake/code/nsight && python app.py
```

Visit `http://localhost:5100`. Verify: sidebar renders, hero image displays with overlay, gruvbox colors applied, responsive layout works on mobile viewport.

- [ ] **Step 14: Commit**

```bash
git init
git add .gitignore app.py tz.py requirements.txt static/ templates/ docs/
git commit -m "feat: project scaffolding — base layout, sidebar nav, hero banner, component macros"
```

---

## Task 2: Scoring Engine (`scoring.py`)

**Files:**
- Create: `scoring.py`

Pure functions, no Flask dependency. Each function takes a database connection and a date, returns a score dict. All use 90-day rolling baselines. These get imported by `app.py` route handlers.

- [ ] **Step 1: Create `scoring.py` with shared helpers**

```python
"""
scoring.py — Category score calculations for nsight.

All scores are 0-100, calibrated to 90-day personal baselines.
80 = at baseline (not population norm).
"""
from datetime import date, timedelta
from decimal import Decimal

def _f(val):
    """Convert Decimal/None to float."""
    if val is None:
        return None
    return float(val)

def _pct_delta(current, baseline):
    if current is None or baseline is None or float(baseline) == 0:
        return None
    return ((float(current) - float(baseline)) / float(baseline)) * 100

def _clamp(val, lo=0, hi=100):
    return max(lo, min(hi, round(val)))

def _score_component(current, baseline, higher_is_better=True):
    """Score a single metric vs baseline. Returns 0-100 where 80 = at baseline."""
    if current is None or baseline is None:
        return None
    pct = _pct_delta(current, baseline)
    if pct is None:
        return None
    if higher_is_better:
        # At baseline = 80. Each 1% above = +1 point, each 1% below = -1.5 points
        return _clamp(80 + pct * 1.0 if pct >= 0 else 80 + pct * 1.5)
    else:
        # Inverted (lower is better, e.g. resting HR)
        return _clamp(80 - pct * 1.0 if pct >= 0 else 80 - pct * 1.5)

def _metric_status(current, baseline, std_dev, higher_is_better=True):
    """Return (status_text, status_color) for trend card pill."""
    if current is None or baseline is None or std_dev is None or float(std_dev) == 0:
        return ("No data", "muted")
    delta = float(current) - float(baseline)
    if abs(delta) <= float(std_dev):
        return ("Normal", "green")
    if delta > 0:
        if higher_is_better:
            return ("Above", "green")
        else:
            return ("Above", "amber")
    else:
        if higher_is_better:
            return ("Below", "amber")
        else:
            return ("Below", "green")
```

- [ ] **Step 2: Implement `fetch_baselines()`**

Query function that fetches 90-day rolling averages and standard deviations for all key metrics. Returns a dict. This gets called once per page load and passed to individual score functions.

```python
def fetch_baselines(conn, target_date: date) -> dict:
    """Fetch 90-day rolling baselines and std devs for all metrics.

    NOTE: Uses RealDictCursor (set in get_db()). Each metric's AVG/STDDEV
    independently ignores NULLs — no global filter that would bias results.
    """
    cur = conn.cursor()
    end = target_date - timedelta(days=1)
    start = end - timedelta(days=89)
    cur.execute("""
        SELECT
            AVG(hrv_nightly_avg) as hrv_avg,
            STDDEV(hrv_nightly_avg) as hrv_std,
            AVG(resting_hr) as rhr_avg,
            STDDEV(resting_hr) as rhr_std,
            AVG(sleep_total_sec) as sleep_total_avg,
            STDDEV(sleep_total_sec) as sleep_total_std,
            AVG(sleep_deep_sec) as sleep_deep_avg,
            STDDEV(sleep_deep_sec) as sleep_deep_std,
            AVG(body_battery_eod) as bb_avg,
            STDDEV(body_battery_eod) as bb_std,
            AVG(respiration_avg) as resp_avg,
            STDDEV(respiration_avg) as resp_std,
            AVG(spo2_avg) as spo2_avg,
            STDDEV(spo2_avg) as spo2_std,
            AVG(steps) as steps_avg,
            STDDEV(steps) as steps_std,
            AVG(stress_avg) as stress_avg,
            STDDEV(stress_avg) as stress_std,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY
                EXTRACT(HOUR FROM sleep_start) + EXTRACT(MINUTE FROM sleep_start) / 60.0
            ) as sleep_start_median_hour
        FROM daily_log
        WHERE date BETWEEN %s AND %s
    """, (start, end))
    row = cur.fetchone()
    return {k: _f(v) for k, v in dict(row).items()} if row else {}
```

- [ ] **Step 3: Implement `compute_sleep_score()`**

```python
def compute_sleep_score(conn, target_date: date, baselines: dict) -> dict:
    """
    Sleep Score (0-100):
    - Total sleep vs baseline: 30%
    - Deep sleep vs baseline: 30%
    - Sleep efficiency: 20%
    - Sleep consistency/timing: 20%
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT sleep_total_sec, sleep_deep_sec, sleep_light_sec,
               sleep_rem_sec, sleep_awake_sec, sleep_start, sleep_end
        FROM daily_log WHERE date = %s
    """, (target_date,))
    row = cur.fetchone()
    if not row or not row.get('sleep_total_sec'):
        return {'score': None, 'components': {}}

    total = _f(row['sleep_total_sec'])
    deep = _f(row['sleep_deep_sec'])
    awake = _f(row.get('sleep_awake_sec'))

    # Component scores
    total_score = _score_component(total, baselines.get('sleep_total_avg'))
    deep_score = _score_component(deep, baselines.get('sleep_deep_avg'))

    # Efficiency: (total - awake) / total * 100, scored relative to 85% baseline
    efficiency = None
    if total and total > 0:
        eff_pct = ((total - (awake or 0)) / total) * 100
        efficiency = _clamp(eff_pct)  # efficiency % maps directly to score

    # Consistency: how close to personal median sleep start time (from baselines)
    consistency = 80  # default if no timing data
    median_hour = baselines.get('sleep_start_median_hour')
    if row.get('sleep_start') and median_hour is not None:
        start_hour = row['sleep_start'].hour + row['sleep_start'].minute / 60
        # Normalize both to comparable range
        if start_hour < 12:
            start_hour += 24  # past midnight
        if median_hour < 12:
            median_hour += 24
        deviation = abs(start_hour - median_hour)
        consistency = _clamp(100 - deviation * 10)

    components = {
        'total': total_score,
        'deep': deep_score,
        'efficiency': efficiency,
        'consistency': consistency,
    }

    # Weighted composite
    weights = {'total': 0.30, 'deep': 0.30, 'efficiency': 0.20, 'consistency': 0.20}
    valid = {k: v for k, v in components.items() if v is not None}
    if not valid:
        return {'score': None, 'components': components}

    # Redistribute weights if some components missing
    total_weight = sum(weights[k] for k in valid)
    score = sum(v * weights[k] / total_weight for k, v in valid.items())

    return {'score': _clamp(score), 'components': components}
```

- [ ] **Step 4: Implement `compute_recovery_score()`**

```python
def compute_recovery_score(conn, target_date: date, baselines: dict) -> dict:
    """
    Recovery Score (0-100):
    - HRV vs baseline: 40%
    - Deep sleep vs baseline: 25%
    - Resting HR vs baseline: 20% (inverted — lower is better)
    - Body battery: 15%
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT hrv_nightly_avg, resting_hr, body_battery_eod, sleep_deep_sec
        FROM daily_log WHERE date = %s
    """, (target_date,))
    row = cur.fetchone()

    if not row:
        return {'score': None, 'components': {}}

    hrv_score = _score_component(_f(row.get('hrv_nightly_avg')), baselines.get('hrv_avg'))
    deep_score = _score_component(_f(row.get('sleep_deep_sec')), baselines.get('sleep_deep_avg'))
    rhr_score = _score_component(_f(row.get('resting_hr')), baselines.get('rhr_avg'), higher_is_better=False)
    bb_score = _score_component(_f(row.get('body_battery_eod')), baselines.get('bb_avg'))

    components = {'hrv': hrv_score, 'deep_sleep': deep_score, 'resting_hr': rhr_score, 'body_battery': bb_score}
    weights = {'hrv': 0.40, 'deep_sleep': 0.25, 'resting_hr': 0.20, 'body_battery': 0.15}

    valid = {k: v for k, v in components.items() if v is not None}
    if not valid:
        return {'score': None, 'components': components}

    total_weight = sum(weights[k] for k in valid)
    score = sum(v * weights[k] / total_weight for k, v in valid.items())

    return {'score': _clamp(score), 'components': components}
```

- [ ] **Step 5: Implement `compute_training_score()`**

```python
def compute_training_score(conn, target_date: date) -> dict:
    """
    Training Score (0-100):
    - ACWR zone: 40% (0.8-1.3 optimal = 90-100)
    - Volume trend vs 28-day avg: 30%
    - Session consistency (trained within last 4 days): 20%
    - Muscle group coverage (last 7 days): 10%
    """
    cur = conn.cursor()

    # ACWR from derived_daily if available, else compute
    acwr = None
    cur.execute("SELECT acwr_volume FROM derived_daily WHERE date = %s", (target_date,))
    dd_row = cur.fetchone()
    if dd_row and dd_row.get('acwr_volume'):
        acwr = _f(dd_row['acwr_volume'])
    else:
        # Compute inline
        cur.execute("""
            SELECT
                NULLIF(AVG(CASE WHEN date > %s THEN hevy_total_volume_lbs END), 0) /
                NULLIF(AVG(CASE WHEN date > %s THEN hevy_total_volume_lbs END), 0) as acwr
            FROM daily_log WHERE date BETWEEN %s AND %s
        """, (
            target_date - timedelta(days=7),
            target_date - timedelta(days=28),
            target_date - timedelta(days=28),
            target_date,
        ))
        acwr_row = cur.fetchone()
        if acwr_row and acwr_row.get('acwr'):
            acwr = _f(acwr_row['acwr'])

    # ACWR score: 0.8-1.3 = 95, gradual falloff outside
    if acwr is not None:
        if 0.8 <= acwr <= 1.3:
            acwr_score = 95
        elif acwr < 0.8:
            acwr_score = _clamp(95 - (0.8 - acwr) * 100)
        elif acwr <= 1.7:
            acwr_score = _clamp(95 - (acwr - 1.3) * 80)
        else:
            acwr_score = _clamp(40 - (acwr - 1.7) * 50)
    else:
        acwr_score = None

    # Volume trend: current 7-day avg vs 28-day avg
    cur.execute("""
        SELECT
            AVG(CASE WHEN date > %s THEN hevy_total_volume_lbs END) as acute,
            AVG(hevy_total_volume_lbs) as chronic
        FROM daily_log
        WHERE date BETWEEN %s AND %s AND hevy_total_volume_lbs > 0
    """, (target_date - timedelta(days=7), target_date - timedelta(days=28), target_date))
    vol_row = cur.fetchone()
    vol_score = None
    if vol_row and vol_row.get('acute') and vol_row.get('chronic'):
        vol_score = _score_component(_f(vol_row['acute']), _f(vol_row['chronic']))

    # Session consistency: days since last training session
    cur.execute("""
        SELECT MAX(date) as last_session FROM daily_log
        WHERE date <= %s AND hevy_session_count > 0
    """, (target_date,))
    last_row = cur.fetchone()
    consistency_score = 80
    if last_row and last_row.get('last_session'):
        gap = (target_date - last_row['last_session']).days
        # DoggCrapp: 3-4 day gaps normal. >5 days starts dropping
        if gap <= 4:
            consistency_score = 90
        elif gap <= 6:
            consistency_score = 75
        else:
            consistency_score = _clamp(75 - (gap - 6) * 8)

    # Muscle group coverage (last 7 days)
    cur.execute("""
        SELECT DISTINCT unnest(hevy_muscle_groups) as mg
        FROM daily_log
        WHERE date BETWEEN %s AND %s AND hevy_muscle_groups IS NOT NULL
    """, (target_date - timedelta(days=7), target_date))
    muscle_rows = cur.fetchall()
    groups_hit = len(muscle_rows)
    # 5+ groups in 7 days = full coverage
    coverage_score = _clamp(min(groups_hit / 5.0, 1.0) * 100)

    components = {
        'acwr': acwr_score,
        'volume_trend': vol_score,
        'consistency': consistency_score,
        'coverage': coverage_score,
    }
    weights = {'acwr': 0.40, 'volume_trend': 0.30, 'consistency': 0.20, 'coverage': 0.10}

    valid = {k: v for k, v in components.items() if v is not None}
    if not valid:
        return {'score': None, 'acwr': acwr, 'components': components}

    total_weight = sum(weights[k] for k in valid)
    score = sum(v * weights[k] / total_weight for k, v in valid.items())

    return {'score': _clamp(score), 'acwr': acwr, 'components': components}
```

- [ ] **Step 6: Implement `compute_nutrition_score()`**

```python
def compute_nutrition_score(conn, target_date: date) -> dict:
    """
    Nutrition Score (0-100):
    - Calorie adherence: 30%
    - Protein adherence (280g): 30%
    - Carb adherence (day-specific): 20%
    - Micro coverage (fiber, sodium): 20%

    Day-of-week aware: Tue/Wed = 400g carbs, 3170 kcal. Others = 300g carbs, 2770 kcal.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT crono_calories, crono_protein_g, crono_carbs_g, crono_fat_g,
               crono_fiber_g, crono_sodium_mg
        FROM daily_log WHERE date = %s
    """, (target_date,))
    row = cur.fetchone()
    if not row or not row.get('crono_calories'):
        return {'score': None, 'targets': {}, 'components': {}}

    # Day-specific targets
    day_name = target_date.strftime('%A')
    is_high_carb = day_name in ('Tuesday', 'Wednesday')
    cal_target = 3170 if is_high_carb else 2770
    carb_target = 400 if is_high_carb else 300
    protein_target = 280
    fat_target = 50

    targets = {
        'calories': cal_target,
        'protein': protein_target,
        'carbs': carb_target,
        'fat': fat_target,
        'is_high_carb': is_high_carb,
        'day_name': day_name,
    }

    def adherence_score(actual, target, tolerance_pct=10):
        """Score 0-100 based on how close actual is to target. 100 = within tolerance."""
        if actual is None or target is None:
            return None
        pct_off = abs(float(actual) - target) / target * 100
        if pct_off <= tolerance_pct:
            return 100
        return _clamp(100 - (pct_off - tolerance_pct) * 2)

    cal_score = adherence_score(_f(row['crono_calories']), cal_target)
    protein_score = adherence_score(_f(row['crono_protein_g']), protein_target, 5)
    carb_score = adherence_score(_f(row['crono_carbs_g']), carb_target, 15)

    # Micro coverage: fiber 26-34g, sodium 3500-4500mg
    fiber = _f(row.get('crono_fiber_g'))
    sodium = _f(row.get('crono_sodium_mg'))
    micro_scores = []
    if fiber is not None:
        if 26 <= fiber <= 34:
            micro_scores.append(100)
        else:
            micro_scores.append(_clamp(100 - abs(fiber - 30) * 5))
    if sodium is not None:
        if 3500 <= sodium <= 4500:
            micro_scores.append(100)
        else:
            mid = 4000
            micro_scores.append(_clamp(100 - abs(sodium - mid) / 50))
    micro_score = sum(micro_scores) / len(micro_scores) if micro_scores else None

    components = {
        'calories': cal_score,
        'protein': protein_score,
        'carbs': carb_score,
        'micros': micro_score,
    }
    weights = {'calories': 0.30, 'protein': 0.30, 'carbs': 0.20, 'micros': 0.20}

    valid = {k: v for k, v in components.items() if v is not None}
    if not valid:
        return {'score': None, 'targets': targets, 'components': components}

    total_weight = sum(weights[k] for k in valid)
    score = sum(v * weights[k] / total_weight for k, v in valid.items())

    return {'score': _clamp(score), 'targets': targets, 'components': components}
```

- [ ] **Step 7: Implement `compute_overall_score()` and `classify_30_days()`**

```python
def compute_overall_score(sleep_score, recovery_score, training_score, nutrition_score) -> int | None:
    """Overall Health Score: Sleep 30%, Recovery 30%, Training 20%, Nutrition 20%."""
    scores = {
        'sleep': (sleep_score, 0.30),
        'recovery': (recovery_score, 0.30),
        'training': (training_score, 0.20),
        'nutrition': (nutrition_score, 0.20),
    }
    valid = {k: (s, w) for k, (s, w) in scores.items() if s is not None}
    if not valid:
        return None
    total_weight = sum(w for _, w in valid.values())
    return _clamp(sum(s * w / total_weight for s, w in valid.values()))


def classify_30_days(conn, target_date: date, category: str) -> dict:
    """
    Classify each of last 30 days as Good (>80), Fair (60-79), Poor (<60).
    Returns {'good': N, 'fair': N, 'poor': N}.

    category: 'sleep', 'recovery', 'training', 'nutrition'
    """
    baselines = fetch_baselines(conn, target_date)
    compute_fn = {
        'sleep': compute_sleep_score,
        'recovery': compute_recovery_score,
        'training': compute_training_score,
        'nutrition': compute_nutrition_score,
    }[category]

    good = fair = poor = 0
    for i in range(30):
        d = target_date - timedelta(days=i)
        if category == 'training':
            result = compute_fn(conn, d)
        else:
            result = compute_fn(conn, d, baselines)
        score = result.get('score')
        if score is None:
            continue
        if score > 80:
            good += 1
        elif score >= 60:
            fair += 1
        else:
            poor += 1

    return {'good': good, 'fair': fair, 'poor': poor}
```

**Note on performance:** `classify_30_days` runs 30 individual score computations. For a single user this is fine (~30 simple queries). If it's noticeably slow, the first optimization is to batch-fetch all 30 days of data in one query and score in-memory. But don't optimize prematurely.

- [ ] **Step 8: Implement `generate_hero_summary()`**

Template-based fallback summary generation for when no daily insight exists.

```python
def generate_hero_summary(category: str, score: int | None, data: dict) -> str:
    """Generate a plain-english hero summary for a page."""
    if score is None:
        return "Not enough data yet to generate a summary."

    level = "solid" if score > 80 else ("decent" if score >= 60 else "below your usual")

    if category == 'sleep':
        total_hrs = data.get('sleep_hrs', '?')
        deep_min = data.get('deep_min', '?')
        return f"Your sleep looks {level} — {total_hrs} hours total with {deep_min} minutes of deep sleep."

    if category == 'recovery':
        hrv = data.get('hrv', '?')
        return f"Recovery is {level} — HRV at {hrv} ms, within your normal range."

    if category == 'training':
        acwr = data.get('acwr')
        if acwr and 0.8 <= acwr <= 1.3:
            return f"Training load is in the sweet spot — ACWR at {acwr:.2f}, right in your optimal range."
        elif acwr and acwr > 1.3:
            return f"Training load is running high — ACWR at {acwr:.2f}. Monitor recovery closely."
        return f"Training looks {level} overall."

    if category == 'nutrition':
        is_high = data.get('is_high_carb', False)
        day_type = "high-carb" if is_high else "standard"
        return f"Nutrition for this {day_type} day looks {level}."

    if category == 'overall':
        return f"Overall health looks {level} today."

    return f"Your {category} looks {level}."
```

- [ ] **Step 9: Commit**

```bash
git add scoring.py && git commit -m "feat: scoring engine — sleep, recovery, training, nutrition composite scores"
```

---

## Task 3: Home Page

**Files:**
- Modify: `app.py` — add home route with full data context
- Create: `templates/home.html`
- Modify: `static/charts.js` — sparkline and weekly chart factories

- [ ] **Step 1: Create `static/charts.js`**

Chart.js configuration and factory functions. All charts use gruvbox palette.

**Global Chart.js defaults** (set once at top of file):
```javascript
Chart.defaults.color = '#a89984';           // --text-secondary
Chart.defaults.font.family = "'DM Sans', sans-serif";
Chart.defaults.plugins.tooltip.backgroundColor = '#32302f';  // --bg-elevated
Chart.defaults.plugins.tooltip.titleColor = '#ddc7a1';       // --text-primary
Chart.defaults.plugins.tooltip.bodyColor = '#a89984';
Chart.defaults.plugins.tooltip.borderColor = '#45403d';      // --border
Chart.defaults.plugins.tooltip.borderWidth = 1;
Chart.defaults.plugins.legend.display = false;
Chart.defaults.elements.line.tension = 0.3;
Chart.defaults.elements.line.borderWidth = 2;
Chart.defaults.elements.point.radius = 0;
Chart.defaults.elements.point.hoverRadius = 4;
Chart.defaults.scale.grid.color = 'rgba(69, 64, 61, 0.5)';  // --border-subtle
Chart.defaults.scale.ticks.font = { family: "'JetBrains Mono', monospace", size: 10 };
```

**Factory functions:**
- `createSparkline(canvasId, data, color)` — tiny line chart (60px tall), no axes/labels/tooltips, fill with 10% opacity of line color. Used in trend cards.
- `createWeeklyChart(canvasId, data, label, color)` — bar chart with day labels `['M','T','W','T','F','S','S']`, rounded bars, `--bg-elevated` hover
- `createDonut(canvasId, good, fair, poor)` — 3-segment doughnut, colors: `--score-good`, `--score-fair`, `--score-poor`, cutout 70%, no animation on load
- `createLineChart(canvasId, labels, datasets, options)` — general purpose, axes visible, responsive, supports multiple datasets with different colors
- `createBarChart(canvasId, labels, data, color, maData)` — bar chart with optional moving average line overlay, rounded bars (borderRadius: 4)
- `createACWRChart(canvasId, labels, data)` — specialized line chart with horizontal bands at 0.8, 1.3, 1.7 using Chart.js annotation plugin or manual fillBetween. Zone colors: `rgba(169,182,101,0.1)` for optimal, `rgba(231,138,78,0.1)` for overreach, `rgba(234,105,98,0.1)` for risk

- [ ] **Step 2: Implement home route in `app.py`**

Port and adapt `home()` from healthdash. Key additions:
- Compute all 4 category scores + overall score
- Fetch today's daily insight (fallback to `generate_hero_summary`)
- Fetch last 3 workouts from `hevy_sets` (grouped by date + session_id)
- Fetch 7-day sparkline data for: avg heart rate, time asleep, steps
- Fetch 7-day trend data for sidebar: HRV, resting HR, steps, body battery, sleep, calories
- Time-of-day greeting: "Good morning/afternoon/evening, Blake"

- [ ] **Step 3: Create `templates/home.html`**

**SKILL:** Invoke `frontend-design` before writing this template.

Extends `base.html`. Sections:
1. **Hero**: greeting, date, daily summary, overall score circle
2. **Score row**: 4 score circles (Sleep, Recovery, Training, Nutrition) — each links to detail page
3. **Recent activity**: Last 3 workout cards (exercise count, duration, volume, muscle groups)
4. **Weekly charts**: 3 chart cards (Avg Heart Rate, Time Asleep, Steps) with day-of-week sparklines *(spec suggests VO2 Max as 3rd option but vo2max data is sparse — Steps is more useful)*
5. **Your Trends sidebar/section**: vertical list of key metrics with sparkline, current value, delta

- [ ] **Step 4: Verify home page renders with real data**

Run `python app.py`, visit `http://localhost:5100`. Verify all sections render, scores display, charts initialize, sparklines populate.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: home page — hero banner, score circles, recent workouts, weekly charts"
```

---

## Task 4: Health Overview Page

**Files:**
- Modify: `app.py` — add `/health` route
- Create: `templates/health.html`

- [ ] **Step 1: Implement `/health` route**

Fetches:
- Overall health score + all 4 category sub-scores
- Today's daily insight (for hero text)
- Vital trends data (7-day): HRV, SpO2, resting HR, respiratory rate, steps, body temperature (if available)
- 90-day baselines + std devs for status pills
- Weekly progress: Sleep/Fitness/Nutrition ring percentages and 1-sentence summaries
- 1-2 insight chips from recent daily insights

- [ ] **Step 2: Create `templates/health.html`**

**SKILL:** Invoke `frontend-design` before writing this template.

Sections:
1. **Hero**: date, overall health score, summary
2. **Score pills row**: Sleep %, Recovery %, Training %, Nutrition % as tappable pills
3. **Insight chips**: 1-2 contextual insight pills
4. **Vital Trends grid**: 6 trend cards (HRV, SpO2, Resting HR, Respiratory Rate, Steps, Body Temp)
5. **Weekly Progress Report**: 3 side-by-side cards with ring charts

- [ ] **Step 3: Verify and commit**

```bash
git add -A && git commit -m "feat: health overview — vitals grid, weekly progress, insight chips"
```

---

## Task 5: Sleep Page

**Files:**
- Modify: `app.py` — add `/sleep` route
- Create: `templates/sleep.html`

- [ ] **Step 1: Implement `/sleep` route**

Fetches:
- Sleep score for today
- Last night's sleep stages (deep, light, REM, awake — duration + %)
- 30-day sleep performance line chart data
- 30-day donut classification (Good/Fair/Poor)
- Trend card data for: total sleep, deep sleep, REM, sleep score, time in bed, light sleep, efficiency, sleep HRV, respiratory rate, resting HR, sleep start, sleep end
- All with 90-day averages and status pills

Port `dashboard_sleep_data()` from healthdash, adapt to trend card format.

- [ ] **Step 2: Create `templates/sleep.html`**

**SKILL:** Invoke `frontend-design` before writing this template.

Sections:
1. **Hero**: "Sleep Overview", sleep score circle, last night's summary
2. **Sleep stages row**: 4 metric pills (Deep, Light, REM, Awake) with duration + %
3. **Sleep Performance**: 30-day line chart + donut
4. **Your Trends grid**: 12 trend cards (from briefing spec)
5. **Explore More** (lower section): 3-4 educational/contextual cards with placeholder article-style content about sleep hygiene, deep sleep optimization, etc. Card with image thumbnail and short description. Content is static — can be updated later.

- [ ] **Step 3: Verify and commit**

```bash
git add -A && git commit -m "feat: sleep page — stages, performance donut, 12 trend cards, explore more"
```

---

## Task 6: Recovery Page

**Files:**
- Modify: `app.py` — add `/recovery` route
- Create: `templates/recovery.html`

- [ ] **Step 1: Implement `/recovery` route**

Fetches:
- Recovery score
- Vitals row: HRV (ms), Resting HR (bpm), Respiratory Rate, Body Battery, SpO2
- Today's activity (last workout summary or "Rest day")
- Recovery Performance: 30-day chart + donut
- Trend cards: Sleep HRV, Resting HR with avg and normal range

- [ ] **Step 2: Create `templates/recovery.html`**

**SKILL:** Invoke `frontend-design` before writing this template.

Follow the Recovery Sonar screenshot pattern closely:
1. **Hero**: "Recovery Overview", recovery score, summary
2. **Vitals row**: 5 metric pills with values and delta indicators
3. **Today's Activity**: workout card or rest day card
4. **Recovery Performance**: line chart + 30-day donut
5. **Your Trends**: HRV and Resting HR trend cards
6. **Explore More**: 2-3 educational cards about recovery optimization, HRV interpretation, etc.

- [ ] **Step 3: Verify and commit**

```bash
git add -A && git commit -m "feat: recovery page — vitals, performance donut, HRV/RHR trends"
```

---

## Task 7: Training Page

**Files:**
- Modify: `app.py` — add `/training` route
- Create: `templates/training.html`

- [ ] **Step 1: Implement `/training` route**

Port `dashboard_training_data()` from healthdash. Add:
- Training score
- Recent workouts: last 5 sessions with name, date, duration, volume, set count, muscle groups
- Volume trend: daily volume bars with 28-day MA line
- ACWR: line chart with 4-zone coloring + plain-english current zone label
- Volume by muscle group: stacked bar chart
- Personal records: exercise name, max weight, reps at max, date achieved

For recent workouts, query `hevy_sets` grouped by date + session_id:
```sql
SELECT date, session_id, COUNT(DISTINCT exercise_name) as exercises,
       SUM(weight_lbs * reps) as volume, COUNT(*) as sets
FROM hevy_sets
WHERE date >= (current_date - interval '30 days')
GROUP BY date, session_id
ORDER BY date DESC LIMIT 5
```

For PRs, enhance the existing query to include date and reps:
```sql
SELECT exercise_name, weight_lbs as max_weight, reps, date
FROM hevy_sets h1
WHERE weight_lbs = (
    SELECT MAX(weight_lbs) FROM hevy_sets h2
    WHERE h2.exercise_name = h1.exercise_name
)
GROUP BY exercise_name, weight_lbs, reps, date
ORDER BY weight_lbs DESC LIMIT 15
```

- [ ] **Step 2: Create `templates/training.html`**

**SKILL:** Invoke `frontend-design` before writing this template.

Sections:
1. **Hero**: training score or weekly volume, summary
2. **Recent Workouts**: workout cards with activity-type icons (emoji fallback: weight lifter, hiking, cycling)
3. **Volume Trend**: bar chart with 28-day MA overlay
4. **ACWR**: line chart with colored zones and current label
5. **Volume by Muscle Group**: stacked bar chart
6. **Personal Records**: styled table

- [ ] **Step 3: Verify and commit**

```bash
git add -A && git commit -m "feat: training page — workouts, volume chart, ACWR zones, PRs"
```

---

## Task 8: Nutrition Page

**Files:**
- Modify: `app.py` — add `/nutrition` route
- Create: `templates/nutrition.html`

- [ ] **Step 1: Implement `/nutrition` route**

Port `dashboard_nutrition_data()` from healthdash. Add:
- Nutrition score
- Day-aware targets (Tue/Wed vs other days)
- Score pills: Calories, Protein, Carbs with status indicators
- 30-day nutrition performance chart + donut
- Trend cards: Calories (vs target), Protein (vs 280g), Carbs (vs day target), Fat (vs 50g), Fiber (vs 26-34g), Sodium (vs 3500-4500mg)

Each trend card shows the day-appropriate target.

- [ ] **Step 2: Create `templates/nutrition.html`**

**SKILL:** Invoke `frontend-design` before writing this template.

Follow the Nutrition Sonar screenshot pattern:
1. **Hero**: "Nutrition Overview", nutrition score, summary mentioning day type
2. **Score pills**: Calories, Protein, Carbs status
3. **Nutrition Performance**: 30-day chart + donut
4. **Your Trends grid**: 7 trend cards — Calories (vs target), Protein (vs 280g), Carbs (vs day target), Fat (vs 50g), Fiber (vs 26-34g), Sodium (vs 3500-4500mg), Water (show "No tracking data" card if not available — spec says include if tracking exists)
5. **Explore More**: 2-3 educational cards about nutrition, carb cycling, protein timing, etc.

- [ ] **Step 3: Verify and commit**

```bash
git add -A && git commit -m "feat: nutrition page — day-aware targets, macro trends, performance donut"
```

---

## Task 9: Check-in Page (Port & Restyle)

**Files:**
- Modify: `app.py` — add `/checkin` route (port from healthdash)
- Create: `templates/checkin.html`

- [ ] **Step 1: Port check-in route from healthdash**

Copy `fetch_period_data()`, `build_scores()`, `build_flags()`, `build_narrative()`, `suggest_score()`, and `delta_pct()` from healthdash's `app.py` into nsight's `app.py`. Port the `/checkin` route.

These are the check-in prep functions — they work fine, just need the new template styling.

- [ ] **Step 2: Create `templates/checkin.html`**

Same functional layout as healthdash but restyled:
1. **Hero**: "Check-in Prep", date range
2. **Period selector**: 7d / 14d / 30d / 90d buttons
3. **Flags section**: colored pill badges
4. **Biometrics summary**: trend-card style layout
5. **Training summary**: card with volume, sessions, ACWR
6. **Scoring recommendations**: 1-10 scales with context strings
7. **Narrative / Notes section**

- [ ] **Step 3: Verify and commit**

```bash
git add -A && git commit -m "feat: check-in prep — ported from healthdash with new card styling"
```

---

## Task 10: Insights Page (Port & Restyle)

**Files:**
- Modify: `app.py` — add `/insights` route
- Create: `templates/insights.html`

- [ ] **Step 1: Port insights route from healthdash**

Port the `/insights` route. Fetch from `insights` table, group by type (daily/weekly/monthly). Add tab switching via query param `?tab=daily|weekly|monthly`.

No model name or token count in UI. Label as "Daily Insight", "Weekly Summary", "Monthly Review".

- [ ] **Step 2: Create `templates/insights.html`**

1. **Hero**: "Insights", stylized hero
2. **Tab switcher**: Daily / Weekly / Monthly
3. **Insight cards**: date header, markdown-rendered body, card styling

- [ ] **Step 3: Verify and commit**

```bash
git add -A && git commit -m "feat: insights page — daily/weekly/monthly tabs with card layout"
```

---

## Task 11: Correlations Page (Port & Restyle)

**Files:**
- Modify: `app.py` — add `/correlations` route (port from healthdash)
- Create: `templates/correlations.html`

- [ ] **Step 1: Port correlations route from healthdash**

Copy `run_correlations_for_display()` from healthdash's `app.py`. This function is self-contained (imports scipy/statsmodels inline). Port the `/correlations` route.

- [ ] **Step 2: Create `templates/correlations.html`**

Restyle as trend cards:
1. **Hero**: "Correlations"
2. **Finding cards**: Each correlation as a card with:
   - Finding title (interpretation sentence)
   - r-value and p-value (small, secondary text)
   - Lag window label
   - Strength indicator (bar or color)

- [ ] **Step 3: Verify and commit**

```bash
git add -A && git commit -m "feat: correlations page — findings as styled cards"
```

---

## Task 12: Polish & PWA

**Files:**
- Modify: `static/style.css` — transitions, hover states, mobile refinements
- Modify: `static/app.js` — swipe nav (optional), smooth transitions
- Modify: `static/sw.js` — basic offline caching
- Modify: `static/manifest.json` — proper PWA metadata
- Create: `systemd/nsight-web.service`

- [ ] **Step 1: CSS polish**

- Card hover effects (subtle elevation change)
- Smooth transitions on sidebar open/close
- Touch target verification (>=44px on all interactive elements)
- Print styles (hide sidebar, full-width content)
- Scroll-to-top on mobile

- [ ] **Step 2: PWA setup**

Update `manifest.json` with proper name, icons (generate from a simple favicon), theme/background colors. Update `sw.js` with basic cache-first strategy for static assets.

- [ ] **Step 3: Create systemd service**

```ini
[Unit]
Description=nsight health dashboard
After=network.target postgresql.service

[Service]
Type=simple
User=blake
WorkingDirectory=/home/blake/code/nsight
ExecStart=/home/blake/code/nsight/.venv/bin/gunicorn -b 127.0.0.1:5100 app:app
Restart=on-failure
EnvironmentFile=/home/blake/code/nsight/.env

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Final verification pass**

Walk through every page on desktop and mobile viewport. Check:
- All hero images load and overlay text is readable
- All score circles display and color correctly
- All sparklines render
- All trend cards show value, avg, status pill
- Sidebar nav works on mobile
- Links between pages work
- No console errors

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: PWA setup, systemd service, polish pass"
```

---

## Implementation Order Summary

| Task | What | Dependencies |
|------|------|-------------|
| 1 | Scaffolding & base layout | None |
| 2 | Scoring engine | None (parallel with Task 1) |
| 3 | Home page | Tasks 1, 2 |
| 4 | Health overview | Tasks 1, 2 |
| 5 | Sleep page | Tasks 1, 2 |
| 6 | Recovery page | Tasks 1, 2 |
| 7 | Training page | Tasks 1, 2 |
| 8 | Nutrition page | Tasks 1, 2 |
| 9 | Check-in (port) | Task 1 |
| 10 | Insights (port) | Task 1 |
| 11 | Correlations (port) | Task 1 |
| 12 | Polish & PWA | All above |

Tasks 1 and 2 can run in parallel. Tasks 3-8 depend on both 1 and 2 but are independent of each other (could be parallelized with subagents). Tasks 9-11 only depend on Task 1. Task 12 is last.
