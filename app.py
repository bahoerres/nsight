"""nsight — personal health intelligence dashboard."""

import os
from datetime import date, datetime, timedelta

import markdown
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from flask import Flask, render_template, send_from_directory
from markupsafe import Markup
from zoneinfo import ZoneInfo

from scoring import (
    fetch_baselines,
    compute_sleep_score,
    compute_recovery_score,
    compute_training_score,
    compute_nutrition_score,
    compute_overall_score,
    generate_hero_summary,
)

load_dotenv()

app = Flask(__name__)

LOCAL_TZ = ZoneInfo(os.environ.get("TZ", "America/Chicago"))

# ── Database ────────────────────────────────────────────────────────

def get_db():
    """Return a psycopg2 connection with RealDictCursor."""
    conn = psycopg2.connect(
        os.environ["DATABASE_URL"],
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    return conn


# ── Template helpers ────────────────────────────────────────────────

@app.template_filter("md")
def md_filter(text):
    """Render markdown string to safe HTML."""
    if not text:
        return ""
    return Markup(markdown.markdown(text, extensions=["extra", "nl2br"]))


@app.context_processor
def inject_globals():
    """Make active_page available in all templates."""
    return {}


# ── Static PWA routes ──────────────────────────────────────────────

@app.route("/sw.js")
def service_worker():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")


@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json", mimetype="application/json")


# ── Pages ───────────────────────────────────────────────────────────

@app.route("/")
def home():
    now = datetime.now(LOCAL_TZ)
    hour = now.hour
    if hour < 12:
        greeting = "Good morning, Blake"
    elif hour < 17:
        greeting = "Good afternoon, Blake"
    else:
        greeting = "Good evening, Blake"

    today_str = now.strftime("%A, %B %-d, %Y")
    today = now.date()
    yesterday = today - timedelta(days=1)

    conn = get_db()
    try:
        # ── Baselines ──────────────────────────────────────────────
        baselines = fetch_baselines(conn, today)

        # ── Scores ─────────────────────────────────────────────────
        sleep_result     = compute_sleep_score(conn, today, baselines)
        recovery_result  = compute_recovery_score(conn, today, baselines)
        training_result  = compute_training_score(conn, yesterday)
        nutrition_result = compute_nutrition_score(conn, yesterday)

        sleep_score     = sleep_result.get("score")
        recovery_score  = recovery_result.get("score")
        training_score  = training_result.get("score")
        nutrition_score = nutrition_result.get("score")

        overall_score = compute_overall_score(
            sleep_result, recovery_result, training_result, nutrition_result
        )

        # ── Daily insight ──────────────────────────────────────────
        daily_summary = None
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content FROM insights WHERE date = %s AND type = 'daily' LIMIT 1",
                (yesterday,),
            )
            row = cur.fetchone()
            if row:
                daily_summary = row["content"]

        if not daily_summary:
            daily_summary = generate_hero_summary(
                "overall",
                overall_score,
                {
                    "sleep_score": sleep_score,
                    "recovery_score": recovery_score,
                    "training_score": training_score,
                    "nutrition_score": nutrition_score,
                },
            )

        # ── Recent workouts (last 3 sessions) ─────────────────────
        recent_workouts = []
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, session_id,
                       ARRAY_AGG(DISTINCT muscle_group) AS muscle_groups,
                       COUNT(*) AS set_count,
                       SUM(reps * weight_lbs) AS total_volume,
                       MAX(set_index) AS max_set
                FROM hevy_sets
                WHERE date <= %s
                GROUP BY date, session_id
                ORDER BY date DESC, session_id DESC
                LIMIT 3
            """, (yesterday,))
            rows = cur.fetchall()
            for r in rows:
                # Get session duration from daily_log if available
                duration = None
                with conn.cursor() as cur2:
                    cur2.execute(
                        "SELECT hevy_session_duration_min FROM daily_log WHERE date = %s",
                        (r["date"],),
                    )
                    dl = cur2.fetchone()
                    if dl and dl.get("hevy_session_duration_min"):
                        duration = int(dl["hevy_session_duration_min"])

                muscles = r.get("muscle_groups") or []
                # Filter out None values from array
                muscles = [m for m in muscles if m]

                recent_workouts.append({
                    "date": r["date"],
                    "date_str": r["date"].strftime("%b %-d"),
                    "muscle_groups": muscles,
                    "set_count": r.get("set_count") or 0,
                    "total_volume": round(float(r.get("total_volume") or 0)),
                    "duration": duration,
                })

        # ── Weekly charts (7-day data) ─────────────────────────────
        week_start = today - timedelta(days=6)
        weekly_labels = []  # Day-of-week labels
        weekly_hr = []
        weekly_sleep = []
        weekly_steps = []

        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, resting_hr, sleep_total_sec, steps
                FROM daily_log
                WHERE date >= %s AND date <= %s
                ORDER BY date ASC
            """, (week_start, today))
            rows = cur.fetchall()

        # Build a dict keyed by date for easy lookup
        daily_data = {}
        for r in rows:
            daily_data[r["date"]] = r

        day_abbrev = ['M', 'T', 'W', 'T', 'F', 'S', 'S']
        for i in range(7):
            d = week_start + timedelta(days=i)
            weekly_labels.append(day_abbrev[d.weekday()])
            row = daily_data.get(d)
            if row:
                weekly_hr.append(float(row["resting_hr"]) if row.get("resting_hr") else 0)
                sleep_hrs = float(row["sleep_total_sec"]) / 3600.0 if row.get("sleep_total_sec") else 0
                weekly_sleep.append(round(sleep_hrs, 1))
                weekly_steps.append(int(row["steps"]) if row.get("steps") else 0)
            else:
                weekly_hr.append(0)
                weekly_sleep.append(0)
                weekly_steps.append(0)

        # ── Trends (7-day sparklines + current value + delta) ──────
        trends = {}
        trend_metrics = [
            ("hrv", "hrv_nightly_avg", "ms", True),
            ("resting_hr", "resting_hr", "bpm", False),
            ("steps", "steps", "", True),
            ("body_battery", "body_battery_eod", "", True),
            ("sleep_hours", "sleep_total_sec", "hrs", True),
            ("calories", "crono_calories", "kcal", True),
        ]

        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, hrv_nightly_avg, resting_hr, steps,
                       body_battery_eod, sleep_total_sec, crono_calories
                FROM daily_log
                WHERE date >= %s AND date <= %s
                ORDER BY date ASC
            """, (week_start, today))
            trend_rows = cur.fetchall()

        trend_data = {}
        for r in trend_rows:
            trend_data[r["date"]] = r

        for key, col, unit, higher_is_better in trend_metrics:
            sparkline = []
            current = None
            for i in range(7):
                d = week_start + timedelta(days=i)
                row = trend_data.get(d)
                val = None
                if row and row.get(col) is not None:
                    val = float(row[col])
                    # Convert sleep_total_sec to hours for display
                    if col == "sleep_total_sec":
                        val = round(val / 3600.0, 1)
                sparkline.append(val if val is not None else 0)
                if d == today or (d == yesterday and current is None):
                    if val is not None and val != 0:
                        current = val

            # Compute delta vs baseline
            baseline_key = {
                "hrv": "hrv_avg",
                "resting_hr": "resting_hr_avg",
                "steps": "steps_avg",
                "body_battery": "body_battery_avg",
                "sleep_hours": "sleep_total_avg",
                "calories": "calories_avg",
            }.get(key)

            baseline_val = baselines.get(baseline_key) if baseline_key else None
            # Convert sleep baseline from seconds to hours
            if key == "sleep_hours" and baseline_val is not None:
                baseline_val = baseline_val / 3600.0

            delta = None
            if current is not None and baseline_val and baseline_val != 0:
                delta = round(((current - baseline_val) / baseline_val) * 100, 1)

            # Format current value for display
            if current is not None:
                if key == "steps":
                    display_val = f"{int(current):,}"
                elif key == "calories":
                    display_val = f"{int(current):,}"
                elif key in ("hrv", "resting_hr"):
                    display_val = f"{current:.0f}"
                elif key == "body_battery":
                    display_val = f"{current:.0f}"
                elif key == "sleep_hours":
                    display_val = f"{current:.1f}"
                else:
                    display_val = f"{current:.1f}"
            else:
                display_val = "--"

            trends[key] = {
                "value": display_val,
                "unit": unit,
                "sparkline": sparkline,
                "delta": delta,
                "higher_is_better": higher_is_better,
            }

    finally:
        conn.close()

    return render_template(
        "home.html",
        active_page="home",
        greeting=greeting,
        today_str=today_str,
        daily_summary=daily_summary,
        overall_score=overall_score or 0,
        sleep_score=sleep_score or 0,
        recovery_score=recovery_score or 0,
        training_score=training_score or 0,
        nutrition_score=nutrition_score or 0,
        recent_workouts=recent_workouts,
        weekly_labels=weekly_labels,
        weekly_hr=weekly_hr,
        weekly_sleep=weekly_sleep,
        weekly_steps=weekly_steps,
        trends=trends,
    )


# ── Run ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(port=5100, debug=True)
