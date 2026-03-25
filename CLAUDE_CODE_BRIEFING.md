# nsight — Build Briefing

## What This Is

nsight is a rebuild of [healthdash](/home/blake/code/healthdash/) — a self-hosted personal
health intelligence dashboard. The backend (Flask, PostgreSQL, ingest scripts, insight
generation) stays mostly intact. The frontend gets a ground-up redesign inspired by
[Sonar](screenshots in this repo), using the existing gruvbox dark palette but with a
lighter, more narrative layout.

**The goal**: same data, better storytelling. Less clinical density, more glanceable
scores, plain-english summaries, and hero-driven page layouts.

---

## Design Reference

Screenshots from Sonar live in this repo at `Screenshot*.png`. Study them. The new
frontend should borrow these patterns, adapted to gruvbox:

### Layout Patterns to Adopt

| Sonar Pattern | How to Apply |
|---|---|
| **Hero banners** | Full-width image header on each page with overlaid date, score, and 1-2 sentence plain-english summary. Use nature/landscape photography (user preference). |
| **Score circles** | Large, prominent category scores (Recovery, Sleep, Nutrition, Training) as the primary visual hierarchy on each page. Numbers big, labels small. |
| **Plain-english summaries** | Every page opens with a human-readable sentence about today's state. Not "HRV: 19ms, Δ-5.3%". More like "Your recovery looks solid — HRV and resting heart rate are both within your normal range." |
| **Trend cards** | Individual metric displayed as: current value (large) + unit, sparkline (7-day), average label, and a green/amber/red "Normal"/"Below"/"Above" pill. One metric per card, not packed grids. |
| **Performance donut** | 30-day overview ring chart with Poor/Fair/Good day counts. One per category. |
| **Sidebar nav** | Left sidebar with icon + label. Pages: Home, Health, Sleep, Training, Nutrition, Check-in, Insights. Collapsible on mobile. |
| **Insight chips** | Pill-shaped contextual callouts ("Performance Insight", "Recovery tip") placed inline, not in separate sections. |
| **Weekly progress cards** | Category cards (Fitness, Sleep, Nutrition) with a ring/percentage and a 1-sentence trend summary. |
| **Explore More** | Educational/contextual content cards at the bottom of Sleep, Nutrition, and Recovery detail pages. Article-style cards with images and short descriptions. Sonar leans heavily into this — it softens data-heavy pages and adds context. Lower priority but include in the design. |
| **Activity-type icons** | SVG icons per workout type on training/workout cards (strength_training, hiking, cycling, etc.). Sonar bundles 118 of these. Use a simple icon set or emoji fallback. |

### What NOT to Copy from Sonar

- Sonar's color palette (use gruvbox instead)
- Social/community features
- Account/settings UI chrome
- "Add Data" manual entry flows (our data comes from API ingest)
- Any branding, logos, or Sonar-specific copy

---

## Color Palette (Gruvbox Dark)

```css
/* Backgrounds */
--bg-root:        #1d2021;
--bg-card:        #282828;
--bg-elevated:    #32302f;
--bg-hero-overlay: rgba(29, 32, 33, 0.7);  /* over hero images */

/* Borders */
--border:         #45403d;
--border-subtle:  rgba(69, 64, 61, 0.5);

/* Text */
--text-primary:   #ddc7a1;
--text-secondary: #a89984;
--text-muted:     #665c54;

/* Accent / Status */
--green:          #a9b665;   /* good, normal, at baseline */
--amber:          #e78a4e;   /* below baseline, worth noting */
--red:            #ea6962;   /* concerning, suppressed */
--blue:           #7daea3;   /* info, accent, links */
--yellow:         #d8a657;   /* highlights, warnings */
--purple:         #d3869b;   /* secondary accent */
--cyan:           #89b482;   /* tertiary accent */

/* Score ring colors */
--score-good:     #a9b665;
--score-fair:     #d8a657;
--score-poor:     #ea6962;
```

### Typography

- **Body**: DM Sans (or system sans-serif fallback)
- **Data/numbers**: JetBrains Mono
- **Hero overlay text**: DM Sans, 600 weight, slight text-shadow for readability over images
- **Score numbers**: JetBrains Mono, 2.5-3rem, bold
- **Labels**: DM Sans, 11-12px, uppercase, letter-spacing 0.05em, `--text-muted`

### Spacing & Radius

- Card radius: 12px (slightly softer than healthdash's 8px)
- Button radius: 8px
- Score circle size: ~120px diameter on desktop, ~90px mobile
- Card padding: 20-24px
- Section gap: 32px
- Page max-width: 1200px (with sidebar), content area ~900px

---

## Pages & Information Architecture

### 1. Home (`/`)

**Hero banner**: Landscape photo, overlaid with greeting ("Good evening, Blake"),
today's date, and a 1-sentence daily summary from the insight engine.

**Score row**: 4 category circles — Sleep, Recovery, Training, Nutrition. Each shows
a 0-100 score. Tap to navigate to detail page.

**Recent activity**: Last 3 workouts as compact cards (exercise count, duration,
volume). "See All" link to Training page.

**Weekly charts**: 3 compact chart cards (Avg Heart Rate, Time Asleep, VO2 Max or
similar) showing weekly sparkline with day-of-week labels (M T W T F S S).

**Your Trends sidebar** (desktop) or section (mobile): Vertical list of key metrics
with current value, sparkline, and delta arrow. Rolling 7-day window.

### 2. Health Overview (`/health`)

**Hero banner**: Date + overall health score + plain-english summary.

**Score pills row**: Sleep %, Recovery %, Nutrition % — tappable sub-scores.

**Insight chips**: 1-2 contextual insights pulled from daily insight generation.

**Vital Trends grid**: Metric cards for:
- Heart Rate Variability (ms, sparkline, avg, normal range)
- Blood Oxygen (%, sparkline)
- Resting Heart Rate (bpm, sparkline)
- Respiratory Rate (brpm, sparkline)
- Body Temperature (if available, else omit)
- Steps (count, sparkline)

**Weekly Progress Report**: Side-by-side cards for Fitness, Sleep, Nutrition —
each with a ring chart percentage and 1-sentence summary.

### 3. Sleep (`/sleep`)

**Hero banner**: "Sleep Overview" + sleep score + plain-english summary of last night.

**Sleep stages row**: 4 metric pills — Deep Sleep, Light Sleep, REM, Awake.
Each shows duration and percentage of total.

**Sleep Performance**: 30-day line chart + donut showing Good/Fair/Poor night
distribution.

**Your Trends grid**: Metric cards for:
- Total Sleep (hrs, sparkline)
- Deep Sleep (hrs, sparkline)
- REM Sleep (hrs, sparkline)
- Sleep Score (0-100, sparkline)
- Time in Bed (hrs)
- Light Sleep (hrs)
- Sleep Efficiency (%)
- Sleep Heart Rate Variability (ms, sparkline)
- Respiratory Rate (brpm)
- Resting Heart Rate (bpm)
- Sleep Start time
- Sleep End time

### 4. Recovery (`/recovery`)

**Hero banner**: "Recovery Overview" + recovery score + plain-english summary.

**Vitals row**: HRV (ms), Resting HR (bpm), Respiratory Rate, Body Battery, SpO2.

**Today's Activity**: Last workout summary or "Rest day" card.

**Recovery Performance**: 30-day chart + donut (Good/Fair/Poor distribution).

**Trend cards**: Sleep Heart Rate Variability, Resting Heart Rate — with avg and
normal range indicators.

### 5. Training (`/training`)

**Hero banner**: Training score or weekly volume summary.

**Recent Workouts**: Cards showing session name, date, duration, volume, set count.

**Volume Trend**: Bar chart — daily volume with 28-day moving average line.

**ACWR**: Line chart with 4-zone coloring (<0.8 detraining, 0.8-1.3 optimal,
1.3-1.7 overreach, >1.7 risk). Plain-english label for current zone.

**Volume by Muscle Group**: Stacked bar chart, gruvbox accent colors per group.

**Personal Records**: Table — exercise name, max weight, reps at max, date achieved.

### 6. Nutrition (`/nutrition`)

**Hero banner**: "Nutrition Overview" + nutrition score + plain-english summary.

**Score pills**: Calories, Protein, Carbs — quick status indicators.

**Nutrition Performance**: 30-day chart + donut.

**Your Trends grid**: Metric cards for:
- Calories Consumed (vs target, sparkline)
- Protein (g, vs 280g target)
- Carbohydrates (g, vs day-specific target — 400g Tue/Wed, 300g otherwise)
- Total Fat (g, vs 50g target)
- Fiber (g, vs 26-34g target)
- Sodium (mg, vs 3500-4500mg target)
- Water (if tracking available)

**Note**: Calorie and carb targets are day-of-week-dependent (5/2 carb cycle).
The UI should reflect the correct target for the displayed day.

### 7. Check-in Prep (`/checkin`)

This page stays functionally similar to healthdash — it's the Kahunas coaching
prep view. But adopt the new card/trend styling:

- Period selector (7d / 14d / 30d / 90d)
- Flags section (notable events as colored pills)
- Biometrics summary cards
- Training summary
- Scoring recommendations (1-10 scale, data-derived, with context strings)
- Notes/export section

### 8. Insights (`/insights`)

- Daily / Weekly / Monthly tab switcher
- Each insight rendered as a card with date header and markdown body
- No model name or token count in the UI (keep in DB only)
- Label as "Daily Insight", "Weekly Summary", etc. — never "AI-generated"

### 9. Correlations (`/correlations`)

- Keep existing correlation display
- Style as trend cards: finding title, r-value, p-value, interpretation sentence
- Group by metric pair, show lag window as a subtle label

---

## Scoring System

### Category Scores (new)

Each page's hero banner shows a 0-100 composite score. These are **new** and need
to be calculated server-side:

**Sleep Score** (0-100):
- Weighted composite: Total sleep vs baseline (30%), Deep sleep vs baseline (30%),
  Sleep efficiency (20%), Sleep consistency/timing (20%)
- 90-day rolling baseline calibration

**Recovery Score** (0-100):
- Weighted composite: HRV vs baseline (40%), Deep sleep vs baseline (25%),
  Resting HR vs baseline (20%), Body battery (15%)
- Same baseline philosophy as healthdash: 80 = at baseline, not population norm

**Training Score** (0-100):
- Factors: ACWR zone (40%), Volume trend vs 28-day avg (30%),
  Session consistency (20%), Muscle group coverage (10%)
- Higher when ACWR is 0.8-1.3 and volume is progressing

**Nutrition Score** (0-100):
- Factors: Calorie adherence to day-specific target (30%),
  Protein adherence to 280g (30%), Carb adherence to day-specific target (20%),
  Micronutrient coverage (20%)
- Day-of-week aware (Tue/Wed high-carb targets differ)

**Overall Health Score** (0-100):
- Weighted: Sleep 30%, Recovery 30%, Training 20%, Nutrition 20%

### 30-Day Performance Donut

For each category, classify each of the last 30 days as:
- **Good** (score > 80): green
- **Fair** (score 60-79): yellow
- **Poor** (score < 60): red

Display as a donut with count labels: "12x Good, 15x Fair, 3x Poor"

### Trend Card Status Pills

Each metric card shows a status pill:
- **Normal** (green): within ±1 SD of 90-day baseline
- **Above** (amber/green depending on metric direction): >1 SD above baseline
- **Below** (amber/red depending on metric direction): >1 SD below baseline

For "positive" metrics (HRV, deep sleep, body battery): above = green, below = amber/red.
For "negative" metrics (resting HR, stress): above = amber/red, below = green.

---

## Athlete Context & Personalization

All scoring, insight generation, and plain-english summaries must be calibrated to
Blake's personal context. The full context lives in
`/home/blake/code/healthdash/athlete_context.txt` — read it. Key points:

- **HRV baseline is 18-24ms** — normal for him. Don't flag as low.
- **HRV % swings are exaggerated at low baselines** — ignore single-day fluctuations
  under 15% unless absolute delta >4-5ms or multi-day trend.
- **DoggCrapp training**: very high intensity, very low volume, 3-4 day gaps between
  sessions by design. Multi-day rest gaps are normal, not detraining.
- **5/2 carb cycle**: Tue/Wed are high-carb (400g carbs, 3170 kcal), all other days
  are 300g carbs, 2770 kcal. Evaluate against the correct day's target.
- **Protein target**: 280g/day, every day.
- **Steps**: 9k target. 12-15k is normal active day (don't comment). Only note <3k
  (likely no watch) or 18k+ (genuinely high).
- **Mass phase**: targeting 225-230 lbs.
- **Communication**: Direct, specific numbers, no generic wellness advice. Address
  as "you". Plain english, not clinical jargon.

---

## Tech Stack

### Carries Over from Healthdash (do not rebuild)

| Component | Location | Notes |
|---|---|---|
| PostgreSQL 16 | Docker, localhost:5432 | Schema at `/home/blake/code/healthdash/schema.sql` |
| Garmin ingest | `/home/blake/code/healthdash/ingest_garmin.py` | Nightly via systemd |
| Hevy ingest | `/home/blake/code/healthdash/ingest_hevy.py` | Nightly via systemd |
| Cronometer ingest | `/home/blake/code/healthdash/cronometer/ingest_cronometer.py` | CSV import |
| Insight generation | `/home/blake/code/healthdash/generate_insights.py` | Claude Opus 4.6 |
| Correlation engine | `/home/blake/code/healthdash/correlations.py` | Pearson + BH FDR |
| Systemd services | `/home/blake/code/healthdash/systemd/` | Timers for ingest + insights |
| Athlete context | `/home/blake/code/healthdash/athlete_context.txt` | Personalization params |

### Frontend Stack (new)

- **Framework**: Flask + Jinja2 (same as healthdash — no reason to change)
- **Charting**: Chart.js 4 (CDN) — proven, works well with gruvbox theming
- **CSS**: New stylesheet, gruvbox palette, built from scratch
- **PWA**: Service worker + manifest for mobile install
- **JS**: Vanilla — no build step, no bundler, no framework
- **Mobile-first**: Touch targets, swipe nav, responsive grid

### New Backend Requirements

The existing `app.py` query logic can be reused, but nsight needs:

1. **Category score calculation** — new functions for Sleep/Recovery/Training/Nutrition
   composite scores (see Scoring System above)
2. **30-day classification** — Good/Fair/Poor day counts per category
3. **Plain-english summary generation** — short template strings for each page's hero
   text (can be static templates filled with data, or pulled from insight engine)
4. **Day-of-week-aware nutrition targets** — carb cycle logic for Tue/Wed vs other days

These can live in the new `app.py` or in a shared `scoring.py` module.

---

## Database Schema Reference

The full schema is at `/home/blake/code/healthdash/schema.sql`. Key tables:

- **`daily_log`** — one row per day, all metrics. Primary fact table.
- **`hevy_sets`** — per-exercise, per-set training data. Enables PRs, muscle group analysis.
- **`derived_daily`** — rolling baselines, ACWR, anomaly flags. Populated by `correlations.py`.
- **`kahunas_checkins`** — subjective 1-10 scores from coaching check-ins.
- **`insights`** — AI-generated daily/weekly/monthly narratives.

Both nsight and healthdash read from the same database. No schema changes needed
unless new scores warrant a new table or columns.

---

## File Structure (Target)

```
/home/blake/code/nsight/
├── app.py                      # Flask app — routes, queries, score calculation
├── scoring.py                  # Category score functions (Sleep, Recovery, etc.)
├── requirements.txt            # Python dependencies
├── .env                        # → symlink or copy from healthdash
├── static/
│   ├── style.css               # New gruvbox stylesheet
│   ├── charts.js               # Chart.js config, sparkline factories
│   ├── app.js                  # Navigation, interactions, swipe
│   ├── manifest.json           # PWA metadata
│   ├── sw.js                   # Service worker
│   ├── icons/                  # PWA icons
│   └── heroes/                 # Hero banner images (nature/landscape)
├── templates/
│   ├── base.html               # Layout shell — sidebar nav, hero slot, content slot
│   ├── home.html               # Home — scores, recent activity, weekly charts
│   ├── health.html             # Health overview — vitals, weekly progress
│   ├── sleep.html              # Sleep detail — stages, performance, trends
│   ├── recovery.html           # Recovery — HRV, battery, activity
│   ├── training.html           # Training — volume, ACWR, PRs
│   ├── nutrition.html          # Nutrition — macros, adherence, trends
│   ├── checkin.html            # Kahunas check-in prep
│   ├── insights.html           # AI insight archive
│   └── correlations.html       # Statistical findings
└── systemd/
    └── nsight-web.service      # Systemd unit (port 5100 or similar)
```

---

## Implementation Priority

1. **Base layout** (`base.html`, `style.css`) — sidebar nav, hero banner component,
   card grid system, responsive breakpoints
2. **Scoring engine** (`scoring.py`) — category scores, 30-day classification,
   trend card status pills
3. **Home page** — hero, score circles, recent workouts, weekly charts
4. **Sleep & Recovery pages** — hero, stages, performance donut, trend cards
5. **Training page** — hero, volume chart, ACWR, PRs, activity-type icons
6. **Nutrition page** — hero, day-aware targets, macro trend cards
7. **Check-in & Insights** — port and restyle from healthdash
8. **Correlations** — restyle as trend cards
9. **PWA & polish** — service worker, manifest, swipe nav, transitions

---

## Important Notes for Agents

1. **Read the existing healthdash code** at `/home/blake/code/healthdash/` — the
   query logic, database access patterns, and insight generation are battle-tested.
   Reuse, don't reinvent.
2. **Never hardcode credentials** — read from `.env` via `python-dotenv`.
3. **Always use upserts** — `ON CONFLICT DO UPDATE`, idempotent operations.
4. **Nulls are expected** — not every metric populates every day. Handle gracefully.
5. **90-day baseline is the norm** — all scores calibrate to personal average, never
   population standards.
6. **HRV 18-24ms is normal for this user** — do not flag, do not emphasize small swings.
7. **No "AI" labeling** — insights are "Daily Insight", never "AI-powered" or "Generated by Claude".
8. **No model metadata in UI** — model name and token counts stay in the database only.
9. **Mobile-first** — design for phone, scale up to desktop. Touch targets ≥44px.
10. **Day-of-week nutrition targets** — Tue/Wed are high-carb days with different macros.
11. **Hero images** — include a set of 5-6 nature/landscape images in `static/heroes/`.
    Rotate by page or day.
12. **Plain english over data density** — when in doubt, show fewer numbers and more
    words. The user wants to glance and understand, not parse a spreadsheet.
13. **Be a design partner** — if you see an opportunity to improve how information is
    presented, suggest it. Don't just replicate.

---

## Name

**nsight** — short for "insight", implies seeing clearly into your health data.
No need to overthink it.

---

## Starter Prompt

Copy and paste the following to kick off a new session:

---

```
Read the build briefing at /home/blake/code/nsight/CLAUDE_CODE_BRIEFING.md — that's your spec.

Then study the Sonar design reference screenshots in this same directory (Screenshot*.png). These are the layout and UX patterns we're adapting — hero banners, score circles, plain-english summaries, trend cards, performance donuts, sidebar nav. We're keeping gruvbox dark colors, not Sonar's palette.

The existing backend lives at /home/blake/code/healthdash/ — read app.py, schema.sql, generate_insights.py, and athlete_context.txt to understand the data model, query patterns, and personalization context. Reuse what works; don't reinvent.

Both repos are side by side. nsight reads from the same PostgreSQL database as healthdash.

Start by proposing a plan — base layout and scoring engine first, then page-by-page. Flag anything in the briefing that's unclear or that you'd approach differently before writing code.
```
