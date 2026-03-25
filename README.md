# nsight

Personal health intelligence dashboard — same data as [healthdash](https://github.com/bahoerres/healthdash), better storytelling.

Sonar-inspired layout with gruvbox dark theming. Hero banners, composite scores, plain-english summaries, trend cards with sparklines, and performance donuts replace the data-dense original.

## Pages

| Page | Route | What it shows |
|------|-------|---------------|
| Home | `/` | Greeting, 4 category scores, recent workouts, weekly charts, trends sidebar |
| Health | `/health` | Overall score, vital trends (HRV, RHR, resp rate, steps), weekly progress |
| Sleep | `/sleep` | Sleep score, stage breakdown, 30-day performance, 12 trend cards |
| Recovery | `/recovery` | Recovery score, vitals row, today's activity, HRV/RHR trends |
| Training | `/training` | Training score, workout cards, volume chart, ACWR zones, muscle groups, PRs |
| Nutrition | `/nutrition` | Nutrition score, day-aware carb cycle targets, macro trends |
| Check-in | `/checkin` | Kahunas coaching prep — flags, biometrics, scoring recommendations |
| Insights | `/insights` | Daily/weekly/monthly AI-generated narratives |
| Correlations | `/correlations` | Pearson correlation findings with FDR correction |

## Scoring

All scores are 0-100, calibrated to 90-day personal baselines (80 = at baseline, not population norms).

- **Sleep** — total sleep, deep sleep, efficiency, timing consistency
- **Recovery** — HRV (40%), deep sleep (25%), resting HR (20%), body battery (15%)
- **Training** — ACWR zone (40%), volume trend (30%), session consistency (20%), muscle coverage (10%)
- **Nutrition** — calorie adherence (30%), protein (30%), carbs (20%), micros (20%)
- **Overall** — Sleep 30%, Recovery 30%, Training 20%, Nutrition 20%

Nutrition targets are day-of-week aware: Tue/Wed are high-carb days (400g carbs, 3170 kcal), all other days are standard (300g carbs, 2770 kcal).

## Stack

- **Backend**: Flask + Jinja2, PostgreSQL 16 (shared with healthdash)
- **Frontend**: Vanilla JS, Chart.js 4, gruvbox dark CSS
- **Scoring**: `scoring.py` — pure Python, no ML, no external APIs
- **Deploy**: gunicorn, systemd user service, GitHub Actions via Tailscale

## Setup

```bash
git clone https://github.com/bahoerres/nsight.git
cd nsight
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Create .env with your database URL
echo 'DATABASE_URL=postgresql://user:pass@localhost:5432/healthdash' > .env
echo 'TZ=America/Chicago' >> .env

python app.py  # http://localhost:5100
```

Requires the same PostgreSQL database as healthdash — same `daily_log`, `hevy_sets`, `derived_daily`, and `insights` tables.

## Deploy (systemd)

```bash
cp systemd/nsight-web.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now nsight-web
```

The service uses `%h` paths, so it works for any user without editing.

## Data Sources

nsight doesn't ingest data — healthdash handles that:

- **Garmin** — sleep, HRV, heart rate, steps, body battery, stress
- **Hevy** — training sessions, sets, reps, weights, muscle groups
- **Cronometer** — calories, protein, carbs, fat, fiber, sodium

Both apps read from the same database. Run healthdash's `healthdash-ingest.sh` to pull fresh data.
