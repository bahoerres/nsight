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
    classify_30_days,
    generate_hero_summary,
    _metric_status,
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


@app.route("/health")
def health():
    now = datetime.now(LOCAL_TZ)
    today = now.date()
    yesterday = today - timedelta(days=1)
    week_start = today - timedelta(days=6)

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

        # ── Daily insight (hero text) ─────────────────────────────
        daily_summary = None
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content FROM insights WHERE date = %s AND type = 'daily' LIMIT 1",
                (today,),
            )
            row = cur.fetchone()
            if row:
                daily_summary = row["content"]

        if not daily_summary:
            # Try yesterday
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

        # ── Insight chips (most recent 2 daily insights) ──────────
        insight_chips = []
        with conn.cursor() as cur:
            cur.execute("""
                SELECT content FROM insights
                WHERE type = 'daily'
                ORDER BY date DESC
                LIMIT 2
            """)
            for row in cur.fetchall():
                content = row["content"] or ""
                # First sentence only
                first_sentence = content.split(". ")[0]
                if first_sentence and not first_sentence.endswith("."):
                    first_sentence += "."
                insight_chips.append(first_sentence)

        # ── Vital Trends (7-day sparkline + current + 90d avg + status) ──
        vital_metrics = [
            ("hrv",       "hrv_nightly_avg", "Heart Rate Variability", "ms",   True,  "hrv_avg",         "hrv_std"),
            ("spo2",      "spo2_avg",        "SpO2",                   "%",    True,  "spo2_avg_val",    "spo2_std"),
            ("rhr",       "resting_hr",      "Resting Heart Rate",     "bpm",  False, "resting_hr_avg",  "resting_hr_std"),
            ("resp",      "respiration_avg", "Respiratory Rate",       "brpm", False, "respiration_avg_val", "respiration_std"),
            ("steps",     "steps",           "Steps",                  "",     True,  "steps_avg",       "steps_std"),
        ]

        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, hrv_nightly_avg, spo2_avg, resting_hr,
                       respiration_avg, steps
                FROM daily_log
                WHERE date >= %s AND date <= %s
                ORDER BY date ASC
            """, (week_start, today))
            trend_rows = cur.fetchall()

        trend_data = {}
        for r in trend_rows:
            trend_data[r["date"]] = r

        vitals = []
        for key, col, label, unit, higher_is_better, baseline_key, std_key in vital_metrics:
            sparkline = []
            current = None
            for i in range(7):
                d = week_start + timedelta(days=i)
                row = trend_data.get(d)
                val = None
                if row and row.get(col) is not None:
                    val = float(row[col])
                sparkline.append(val if val is not None else 0)
                if d == today or (d == yesterday and current is None):
                    if val is not None and val != 0:
                        current = val

            baseline_val = baselines.get(baseline_key)
            std_val = baselines.get(std_key)

            # 90-day average formatted
            avg_display = None
            if baseline_val is not None:
                if key == "steps":
                    avg_display = f"{int(baseline_val):,}"
                elif key in ("hrv", "rhr"):
                    avg_display = f"{baseline_val:.0f}"
                elif key == "spo2":
                    avg_display = f"{baseline_val:.1f}"
                elif key == "resp":
                    avg_display = f"{baseline_val:.1f}"
                else:
                    avg_display = f"{baseline_val:.1f}"

            # Status pill via _metric_status
            status_text, status_color = _metric_status(current, baseline_val, std_val, higher_is_better)
            # Map status text to CSS pill classes (pill-normal, pill-above, pill-below)
            pill_class = "normal"
            if status_text == "Above":
                pill_class = "above"
            elif status_text == "Below":
                pill_class = "below"
            elif status_text == "No data":
                pill_class = "below"

            # Format current value
            if current is not None:
                if key == "steps":
                    display_val = f"{int(current):,}"
                elif key in ("hrv", "rhr"):
                    display_val = f"{current:.0f}"
                elif key == "spo2":
                    display_val = f"{current:.1f}"
                elif key == "resp":
                    display_val = f"{current:.1f}"
                else:
                    display_val = f"{current:.1f}"
            else:
                display_val = "--"

            vitals.append({
                "id": key,
                "label": label,
                "value": display_val,
                "unit": unit,
                "sparkline": sparkline,
                "avg": avg_display,
                "status": status_text,
                "status_color": pill_class,
            })

        # ── Weekly Progress (avg score over last 7 days) ──────────
        weekly_scores = {"sleep": [], "training": [], "nutrition": []}
        for i in range(7):
            d = today - timedelta(days=i)
            d_baselines = baselines  # re-use 90d baselines (close enough)
            s = compute_sleep_score(conn, d, d_baselines)
            if s.get("score") is not None:
                weekly_scores["sleep"].append(s["score"])
            t = compute_training_score(conn, d)
            if t.get("score") is not None:
                weekly_scores["training"].append(t["score"])
            n = compute_nutrition_score(conn, d)
            if n.get("score") is not None:
                weekly_scores["nutrition"].append(n["score"])

        def _avg(lst):
            return round(sum(lst) / len(lst)) if lst else None

        weekly_progress = [
            {
                "label": "Fitness",
                "score": _avg(weekly_scores["training"]),
                "summary": generate_hero_summary("training", _avg(weekly_scores["training"]), {}),
            },
            {
                "label": "Sleep",
                "score": _avg(weekly_scores["sleep"]),
                "summary": generate_hero_summary("sleep", _avg(weekly_scores["sleep"]), {}),
            },
            {
                "label": "Nutrition",
                "score": _avg(weekly_scores["nutrition"]),
                "summary": generate_hero_summary("nutrition", _avg(weekly_scores["nutrition"]), {}),
            },
        ]

    finally:
        conn.close()

    return render_template(
        "health.html",
        active_page="health",
        today_label=now.strftime("%b %-d"),
        daily_summary=daily_summary,
        overall_score=overall_score or 0,
        sleep_score=sleep_score or 0,
        recovery_score=recovery_score or 0,
        training_score=training_score or 0,
        nutrition_score=nutrition_score or 0,
        insight_chips=insight_chips,
        vitals=vitals,
        weekly_progress=weekly_progress,
    )


@app.route("/recovery")
def recovery():
    now = datetime.now(LOCAL_TZ)
    today = now.date()
    yesterday = today - timedelta(days=1)
    week_start = today - timedelta(days=6)

    conn = get_db()
    try:
        # ── Baselines ──────────────────────────────────────────────
        baselines = fetch_baselines(conn, today)

        # ── Recovery score ─────────────────────────────────────────
        recovery_result = compute_recovery_score(conn, today, baselines)
        recovery_score = recovery_result.get("score") or 0

        # ── Hero summary ───────────────────────────────────────────
        hero_summary = None
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content FROM insights WHERE date = %s AND type = 'recovery' LIMIT 1",
                (today,),
            )
            row = cur.fetchone()
            if row:
                hero_summary = row["content"]

        if not hero_summary:
            hero_summary = generate_hero_summary(
                "recovery", recovery_score, recovery_result.get("components", {}),
            )

        # ── Vitals row (5 metrics with delta vs baseline) ──────────
        with conn.cursor() as cur:
            cur.execute("""
                SELECT hrv_nightly_avg, resting_hr, respiration_avg,
                       body_battery_eod, spo2_avg
                FROM daily_log
                WHERE date = %s
            """, (today,))
            today_row = cur.fetchone()

        vital_defs = [
            ("HRV",              "hrv_nightly_avg",  "ms",   True,  "hrv_avg"),
            ("Resting HR",       "resting_hr",       "bpm",  False, "resting_hr_avg"),
            ("Respiratory Rate", "respiration_avg",   "brpm", False, "respiration_avg_val"),
            ("Body Battery",     "body_battery_eod",  "",    True,  "body_battery_avg"),
            ("SpO2",             "spo2_avg",          "%",   True,  "spo2_avg_val"),
        ]

        vitals = []
        for label, col, unit, higher_is_better, baseline_key in vital_defs:
            current = None
            if today_row and today_row.get(col) is not None:
                current = float(today_row[col])

            baseline_val = baselines.get(baseline_key)

            # Compute delta percentage
            delta = None
            if current is not None and baseline_val and baseline_val != 0:
                delta = round(((current - baseline_val) / baseline_val) * 100, 1)

            # Delta color: green if favorable, amber/red if not
            delta_color = "muted"
            if delta is not None:
                if higher_is_better:
                    delta_color = "green" if delta >= 0 else ("amber" if delta > -10 else "red")
                else:
                    delta_color = "green" if delta <= 0 else ("amber" if delta < 10 else "red")

            # Format current value
            if current is not None:
                if col in ("hrv_nightly_avg", "resting_hr"):
                    display_val = f"{current:.0f}"
                elif col == "spo2_avg":
                    display_val = f"{current:.1f}"
                elif col == "body_battery_eod":
                    display_val = f"{current:.0f}"
                elif col == "respiration_avg":
                    display_val = f"{current:.1f}"
                else:
                    display_val = f"{current:.1f}"
            else:
                display_val = "--"

            vitals.append({
                "label": label,
                "value": display_val,
                "unit": unit,
                "delta": delta,
                "delta_color": delta_color,
            })

        # ── Today's Activity (yesterday's workout data) ────────────
        workout = None
        with conn.cursor() as cur:
            cur.execute("""
                SELECT hevy_session_count, hevy_total_volume_lbs,
                       hevy_total_sets, hevy_session_duration_min,
                       hevy_muscle_groups
                FROM daily_log
                WHERE date = %s
            """, (yesterday,))
            activity_row = cur.fetchone()

        if activity_row and activity_row.get("hevy_session_count") and int(activity_row["hevy_session_count"]) > 0:
            muscles_raw = activity_row.get("hevy_muscle_groups") or []
            if isinstance(muscles_raw, str):
                muscles_raw = [m.strip() for m in muscles_raw.strip("{}").split(",") if m.strip()]
            muscles = [m for m in muscles_raw if m]

            workout = {
                "muscle_groups": muscles,
                "volume": round(float(activity_row.get("hevy_total_volume_lbs") or 0)),
                "sets": int(activity_row.get("hevy_total_sets") or 0),
                "duration": int(activity_row.get("hevy_session_duration_min") or 0),
            }

        # ── 30-day Recovery Performance (line chart + donut) ───────
        perf_labels = []
        perf_scores = []
        for i in range(29, -1, -1):
            d = today - timedelta(days=i)
            perf_labels.append(d.strftime("%-m/%-d"))
            try:
                bl = fetch_baselines(conn, d)
                result = compute_recovery_score(conn, d, bl)
                s = result.get("score")
                perf_scores.append(s if s is not None else None)
            except Exception:
                perf_scores.append(None)

        donut_counts = classify_30_days(conn, today, "recovery")

        # ── Trend cards: Sleep HRV and Resting HR ──────────────────
        trend_defs = [
            ("sleep_hrv",   "hrv_nightly_avg", "Sleep HRV",   "ms",  True,  "hrv_avg",        "hrv_std"),
            ("resting_hr",  "resting_hr",      "Resting HR",  "bpm", False, "resting_hr_avg", "resting_hr_std"),
        ]

        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, hrv_nightly_avg, resting_hr
                FROM daily_log
                WHERE date >= %s AND date <= %s
                ORDER BY date ASC
            """, (week_start, today))
            spark_rows = cur.fetchall()

        spark_data = {}
        for r in spark_rows:
            spark_data[r["date"]] = r

        # Compute 30-day averages for the trend cards
        month_start = today - timedelta(days=29)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT AVG(hrv_nightly_avg) AS hrv_30d,
                       AVG(resting_hr) AS rhr_30d
                FROM daily_log
                WHERE date >= %s AND date <= %s
            """, (month_start, today))
            avg_30d_row = cur.fetchone()

        hrv_30d_avg = float(avg_30d_row["hrv_30d"]) if avg_30d_row and avg_30d_row.get("hrv_30d") else None
        rhr_30d_avg = float(avg_30d_row["rhr_30d"]) if avg_30d_row and avg_30d_row.get("rhr_30d") else None

        trends = []
        for key, col, label, unit, higher_is_better, baseline_key, std_key in trend_defs:
            sparkline = []
            current = None
            for i in range(7):
                d = week_start + timedelta(days=i)
                row = spark_data.get(d)
                val = None
                if row and row.get(col) is not None:
                    val = float(row[col])
                sparkline.append(val if val is not None else 0)
                if d == today or (d == yesterday and current is None):
                    if val is not None and val != 0:
                        current = val

            baseline_val = baselines.get(baseline_key)
            std_val = baselines.get(std_key)
            status_text, _ = _metric_status(current, baseline_val, std_val, higher_is_better)

            pill_class = "normal"
            if status_text == "Above":
                pill_class = "above"
            elif status_text == "Below":
                pill_class = "below"
            elif status_text == "No data":
                pill_class = "below"

            # 30-day avg display
            avg_30 = None
            if key == "sleep_hrv" and hrv_30d_avg is not None:
                avg_30 = f"{hrv_30d_avg:.0f}"
            elif key == "resting_hr" and rhr_30d_avg is not None:
                avg_30 = f"{rhr_30d_avg:.0f}"

            if current is not None:
                display_val = f"{current:.0f}"
            else:
                display_val = "--"

            trends.append({
                "id": key,
                "label": label,
                "value": display_val,
                "unit": unit,
                "sparkline": sparkline,
                "avg_30d": avg_30,
                "status": status_text,
                "status_color": pill_class,
            })

    finally:
        conn.close()

    return render_template(
        "recovery.html",
        active_page="recovery",
        recovery_score=recovery_score,
        hero_summary=hero_summary,
        vitals=vitals,
        workout=workout,
        perf_labels=perf_labels,
        perf_scores=perf_scores,
        donut_good=donut_counts["good"],
        donut_fair=donut_counts["fair"],
        donut_poor=donut_counts["poor"],
        trends=trends,
    )


@app.route("/sleep")
def sleep():
    now = datetime.now(LOCAL_TZ)
    today = now.date()
    week_start = today - timedelta(days=6)

    conn = get_db()
    try:
        # ── Baselines ──────────────────────────────────────────────
        baselines = fetch_baselines(conn, today)

        # ── Sleep score ────────────────────────────────────────────
        sleep_result = compute_sleep_score(conn, today, baselines)
        sleep_score = sleep_result.get("score") or 0

        # ── Hero summary ───────────────────────────────────────────
        hero_summary = None
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content FROM insights WHERE date = %s AND type = 'sleep' LIMIT 1",
                (today,),
            )
            row = cur.fetchone()
            if row:
                hero_summary = row["content"]

        if not hero_summary:
            hero_summary = generate_hero_summary(
                "sleep", sleep_score, sleep_result.get("components", {}),
            )

        # ── Last night's sleep stages ──────────────────────────────
        with conn.cursor() as cur:
            cur.execute("""
                SELECT sleep_total_sec, sleep_deep_sec, sleep_light_sec,
                       sleep_rem_sec, sleep_awake_sec, sleep_start, sleep_end,
                       hrv_nightly_avg, respiration_avg, resting_hr
                FROM daily_log
                WHERE date = %s
            """, (today,))
            today_row = cur.fetchone()

        stages = []
        stage_total_sec = 0
        sleep_start_time = None
        sleep_end_time = None
        today_hrv = None
        today_resp = None
        today_rhr = None

        if today_row:
            deep_sec = float(today_row["sleep_deep_sec"] or 0)
            light_sec = float(today_row["sleep_light_sec"] or 0)
            rem_sec = float(today_row["sleep_rem_sec"] or 0)
            awake_sec = float(today_row["sleep_awake_sec"] or 0)
            total_sec = float(today_row["sleep_total_sec"] or 0)
            stage_total_sec = deep_sec + light_sec + rem_sec + awake_sec

            today_hrv = float(today_row["hrv_nightly_avg"]) if today_row.get("hrv_nightly_avg") else None
            today_resp = float(today_row["respiration_avg"]) if today_row.get("respiration_avg") else None
            today_rhr = float(today_row["resting_hr"]) if today_row.get("resting_hr") else None

            if today_row.get("sleep_start"):
                sleep_start_time = today_row["sleep_start"].astimezone(LOCAL_TZ).strftime("%-I:%M %p")
            if today_row.get("sleep_end"):
                sleep_end_time = today_row["sleep_end"].astimezone(LOCAL_TZ).strftime("%-I:%M %p")

            for label, sec, color in [
                ("Deep Sleep", deep_sec, "#7daea3"),
                ("REM Sleep", rem_sec, "#d3869b"),
                ("Light Sleep", light_sec, "#d8a657"),
                ("Awake", awake_sec, "#ea6962"),
            ]:
                hrs = int(sec // 3600)
                mins = int((sec % 3600) // 60)
                pct = round((sec / stage_total_sec) * 100, 1) if stage_total_sec > 0 else 0
                stages.append({
                    "label": label,
                    "hours": hrs,
                    "minutes": mins,
                    "pct": pct,
                    "color": color,
                })

        # ── 30-day sleep performance (line chart + donut) ──────────
        perf_labels = []
        perf_scores = []
        for i in range(29, -1, -1):
            d = today - timedelta(days=i)
            perf_labels.append(d.strftime("%-m/%-d"))
            try:
                bl = fetch_baselines(conn, d)
                result = compute_sleep_score(conn, d, bl)
                s = result.get("score")
                perf_scores.append(s if s is not None else None)
            except Exception:
                perf_scores.append(None)

        donut_counts = classify_30_days(conn, today, "sleep")

        # ── 12 Trend cards (7-day sparklines + 90d avg + status) ───
        trend_metrics = [
            ("total_sleep",    "sleep_total_sec",   "Total Sleep",      "hrs",  True,  "sleep_total_avg",        "sleep_total_std",        "sec_to_hrs"),
            ("deep_sleep",     "sleep_deep_sec",    "Deep Sleep",       "hrs",  True,  "sleep_deep_avg",         "sleep_deep_std",         "sec_to_hrs"),
            ("rem_sleep",      "sleep_rem_sec",     "REM Sleep",        "hrs",  True,  None,                     None,                     "sec_to_hrs"),
            ("sleep_score",    None,                "Sleep Score",      "",     True,  None,                     None,                     "score"),
            ("time_in_bed",    None,                "Time in Bed",      "hrs",  True,  None,                     None,                     "tib"),
            ("light_sleep",    "sleep_light_sec",   "Light Sleep",      "hrs",  False, None,                     None,                     "sec_to_hrs"),
            ("efficiency",     None,                "Sleep Efficiency", "%",    True,  None,                     None,                     "eff"),
            ("sleep_hrv",      "hrv_nightly_avg",   "Sleep HRV",        "ms",   True,  "hrv_avg",                "hrv_std",                "raw"),
            ("resp_rate",      "respiration_avg",   "Respiratory Rate", "brpm", False, "respiration_avg_val",    "respiration_std",        "raw"),
            ("resting_hr",     "resting_hr",        "Resting HR",       "bpm",  False, "resting_hr_avg",         "resting_hr_std",         "raw"),
            ("sleep_start",    "sleep_start",       "Sleep Start",      "",     False, None,                     None,                     "time_start"),
            ("sleep_end",      "sleep_end",         "Sleep End",        "",     False, None,                     None,                     "time_end"),
        ]

        # Fetch 7-day data for sparklines
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, sleep_total_sec, sleep_deep_sec, sleep_light_sec,
                       sleep_rem_sec, sleep_awake_sec, sleep_start, sleep_end,
                       hrv_nightly_avg, respiration_avg, resting_hr
                FROM daily_log
                WHERE date >= %s AND date <= %s
                ORDER BY date ASC
            """, (week_start, today))
            spark_rows = cur.fetchall()

        spark_data = {}
        for r in spark_rows:
            spark_data[r["date"]] = r

        trends = []
        for key, col, label, unit, higher_is_better, baseline_key, std_key, transform in trend_metrics:
            sparkline = []
            current = None

            for i in range(7):
                d = week_start + timedelta(days=i)
                row = spark_data.get(d)
                val = None

                if transform == "score":
                    # Compute sleep score for each day
                    try:
                        bl = fetch_baselines(conn, d)
                        res = compute_sleep_score(conn, d, bl)
                        val = float(res["score"]) if res.get("score") is not None else None
                    except Exception:
                        val = None
                elif transform == "tib":
                    # Time in bed = total + awake
                    if row and row.get("sleep_total_sec") is not None and row.get("sleep_awake_sec") is not None:
                        val = (float(row["sleep_total_sec"]) + float(row["sleep_awake_sec"])) / 3600.0
                elif transform == "eff":
                    # Efficiency = (total - awake) / total * 100
                    if row and row.get("sleep_total_sec") and row.get("sleep_awake_sec") is not None:
                        t = float(row["sleep_total_sec"])
                        a = float(row["sleep_awake_sec"])
                        if t > 0:
                            val = ((t - a) / t) * 100
                elif transform == "sec_to_hrs":
                    if row and row.get(col) is not None:
                        val = float(row[col]) / 3600.0
                elif transform == "time_start":
                    if row and row.get("sleep_start"):
                        local_t = row["sleep_start"].astimezone(LOCAL_TZ)
                        # Convert to fractional hours for sparkline (normalize around midnight)
                        h = local_t.hour + local_t.minute / 60.0
                        if h > 12:
                            val = h - 24  # e.g. 22:30 = -1.5
                        else:
                            val = h
                elif transform == "time_end":
                    if row and row.get("sleep_end"):
                        local_t = row["sleep_end"].astimezone(LOCAL_TZ)
                        val = local_t.hour + local_t.minute / 60.0
                elif transform == "raw":
                    if row and row.get(col) is not None:
                        val = float(row[col])

                sparkline.append(val if val is not None else 0)
                if d == today or (d == (today - timedelta(days=1)) and current is None):
                    if val is not None and val != 0:
                        current = val

            # 90d avg
            baseline_val = baselines.get(baseline_key) if baseline_key else None
            std_val = baselines.get(std_key) if std_key else None

            # Convert baseline for sec_to_hrs metrics
            if transform == "sec_to_hrs" and baseline_val is not None:
                baseline_val_display = baseline_val / 3600.0
            elif transform == "raw":
                baseline_val_display = baseline_val
            else:
                baseline_val_display = baseline_val

            # Format avg display
            avg_display = None
            if baseline_val_display is not None:
                if transform == "sec_to_hrs":
                    avg_display = f"{baseline_val_display:.1f}"
                elif key in ("sleep_hrv", "resting_hr"):
                    avg_display = f"{baseline_val_display:.0f}"
                elif key == "resp_rate":
                    avg_display = f"{baseline_val_display:.1f}"
                else:
                    avg_display = f"{baseline_val_display:.1f}"

            # Status pill
            # For sec_to_hrs, compare in original units
            current_for_status = current
            baseline_for_status = baselines.get(baseline_key) if baseline_key else None
            std_for_status = baselines.get(std_key) if std_key else None

            if transform == "sec_to_hrs" and current_for_status is not None:
                current_for_status = current_for_status * 3600  # back to seconds
            status_text, _ = _metric_status(current_for_status, baseline_for_status, std_for_status, higher_is_better)

            pill_class = "normal"
            if status_text == "Above":
                pill_class = "above"
            elif status_text == "Below":
                pill_class = "below"
            elif status_text == "No data":
                pill_class = "below"

            # Format current value for display
            if current is not None:
                if transform == "sec_to_hrs":
                    display_val = f"{current:.1f}"
                elif transform == "score":
                    display_val = f"{current:.0f}"
                elif transform == "eff":
                    display_val = f"{current:.1f}"
                elif transform == "tib":
                    display_val = f"{current:.1f}"
                elif transform == "time_start" or transform == "time_end":
                    # Show actual time, not fractional hours
                    if today_row:
                        ts_col = "sleep_start" if transform == "time_start" else "sleep_end"
                        if today_row.get(ts_col):
                            display_val = today_row[ts_col].astimezone(LOCAL_TZ).strftime("%-I:%M %p")
                        else:
                            display_val = "--"
                    else:
                        display_val = "--"
                elif key in ("sleep_hrv", "resting_hr"):
                    display_val = f"{current:.0f}"
                elif key == "resp_rate":
                    display_val = f"{current:.1f}"
                else:
                    display_val = f"{current:.1f}"
            else:
                display_val = "--"

            trends.append({
                "id": key,
                "label": label,
                "value": display_val,
                "unit": unit,
                "sparkline": sparkline,
                "avg": avg_display,
                "status": status_text,
                "status_color": pill_class,
            })

    finally:
        conn.close()

    return render_template(
        "sleep.html",
        active_page="sleep",
        sleep_score=sleep_score,
        hero_summary=hero_summary,
        stages=stages,
        perf_labels=perf_labels,
        perf_scores=perf_scores,
        donut_good=donut_counts["good"],
        donut_fair=donut_counts["fair"],
        donut_poor=donut_counts["poor"],
        trends=trends,
    )


# ── Run ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(port=5100, debug=True)
