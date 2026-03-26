"""nsight — personal health intelligence dashboard."""

import os
from datetime import date, datetime, timedelta

import markdown
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from flask import Flask, render_template, request, send_from_directory
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


def _truncate_summary(text, max_sentences=2):
    """Truncate text to first N sentences for hero banners."""
    if not text:
        return text
    sentences = text.replace(".\n", ". ").split(". ")
    truncated = ". ".join(sentences[:max_sentences])
    if not truncated.endswith("."):
        truncated += "."
    return truncated


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


@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static/icons', 'icon-192.png', mimetype='image/png')


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

        # ── Hero summary — short template text, not the full daily insight
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

        # ── Hero summary — always use the short template-based summary
        #    The daily insight is too detailed/long for the hero overlay.
        #    It goes in the insight chip instead.
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

        # ── Insight chip (1 chip, different from hero text) ──────────
        insight_chips = []
        with conn.cursor() as cur:
            cur.execute("""
                SELECT content FROM insights
                WHERE type = 'daily'
                ORDER BY date DESC
                LIMIT 3
            """)
            for row in cur.fetchall():
                content = row["content"] or ""
                if content:
                    insight_chips.append(content)
                if len(insight_chips) >= 1:
                    break

        # ── Vital Trends (7-day sparkline + current + 90d avg + status) ──
        vital_metrics = [
            ("hrv",       "hrv_nightly_avg", "Heart Rate Variability", "ms",   True,  "hrv_avg",         "hrv_std"),
            ("rhr",       "resting_hr",      "Resting Heart Rate",     "bpm",  False, "resting_hr_avg",  "resting_hr_std"),
            ("resp",      "respiration_avg", "Respiratory Rate",       "brpm", False, "respiration_avg_val", "respiration_std"),
            ("steps",     "steps",           "Steps",                  "",     True,  "steps_avg",       "steps_std"),
        ]

        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, hrv_nightly_avg, resting_hr,
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
                else:
                    avg_display = f"{baseline_val:.1f}"

            # Status pill via _metric_status
            status_text, status_color = _metric_status(current, baseline_val, std_val, higher_is_better)
            if status_color == "green":
                pill_class = "normal"
            elif status_color == "amber":
                pill_class = "above" if status_text == "Above" else "below"
            elif status_color == "muted":
                pill_class = "below"
            else:
                pill_class = "normal"

            # Format current value
            if current is not None:
                if key == "steps":
                    display_val = f"{int(current):,}"
                elif key in ("hrv", "rhr"):
                    display_val = f"{current:.0f}"
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
                hero_summary = _truncate_summary(row["content"])

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
            status_text, status_color = _metric_status(current, baseline_val, std_val, higher_is_better)

            if status_color == "green":
                pill_class = "normal"
            elif status_color == "amber":
                pill_class = "above" if status_text == "Above" else "below"
            elif status_color == "muted":
                pill_class = "below"
            else:
                pill_class = "normal"

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


@app.route("/training")
def training():
    now = datetime.now(LOCAL_TZ)
    today = now.date()
    yesterday = today - timedelta(days=1)

    conn = get_db()
    try:
        # ── Training score ──────────────────────────────────────────
        training_result = compute_training_score(conn, yesterday)
        training_score = training_result.get("score") or 0

        # ── Hero summary ────────────────────────────────────────────
        hero_summary = None
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content FROM insights WHERE date = %s AND type = 'training' LIMIT 1",
                (today,),
            )
            row = cur.fetchone()
            if row:
                hero_summary = _truncate_summary(row["content"])

        if not hero_summary:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT content FROM insights WHERE date = %s AND type = 'training' LIMIT 1",
                    (yesterday,),
                )
                row = cur.fetchone()
                if row:
                    hero_summary = _truncate_summary(row["content"])

        if not hero_summary:
            hero_summary = generate_hero_summary(
                "training", training_score, training_result.get("components", {}),
            )

        # ── Recent workouts (last 5 sessions) ──────────────────────
        recent_workouts = []
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, session_id,
                       ARRAY_AGG(DISTINCT exercise_name) AS exercises,
                       ARRAY_AGG(DISTINCT muscle_group) AS muscles,
                       SUM(weight_lbs * reps) AS volume,
                       COUNT(*) AS total_sets,
                       COUNT(DISTINCT exercise_name) AS exercise_count
                FROM hevy_sets
                WHERE date >= (current_date - interval '60 days')
                GROUP BY date, session_id
                ORDER BY date DESC LIMIT 5
            """)
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

                muscles = r.get("muscles") or []
                muscles = [m for m in muscles if m]
                exercises = r.get("exercises") or []
                exercises = [e for e in exercises if e]

                recent_workouts.append({
                    "date": r["date"],
                    "date_str": r["date"].strftime("%b %-d"),
                    "exercises": exercises,
                    "muscle_groups": muscles,
                    "volume": round(float(r.get("volume") or 0)),
                    "total_sets": r.get("total_sets") or 0,
                    "exercise_count": r.get("exercise_count") or 0,
                    "duration": duration,
                })

        # ── Volume trend (30 days + 28-day MA) ──────────────────────
        vol_labels = []
        vol_data = []
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, hevy_total_volume_lbs
                FROM daily_log
                WHERE date >= %s AND date <= %s
                ORDER BY date ASC
            """, (today - timedelta(days=29), today))
            vol_rows = cur.fetchall()

        vol_by_date = {}
        for r in vol_rows:
            vol_by_date[r["date"]] = float(r["hevy_total_volume_lbs"]) if r.get("hevy_total_volume_lbs") else 0

        # Also fetch 28 days before the 30-day window for moving average
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, hevy_total_volume_lbs
                FROM daily_log
                WHERE date >= %s AND date <= %s
                ORDER BY date ASC
            """, (today - timedelta(days=57), today))
            ma_rows = cur.fetchall()

        ma_by_date = {}
        for r in ma_rows:
            ma_by_date[r["date"]] = float(r["hevy_total_volume_lbs"]) if r.get("hevy_total_volume_lbs") else 0

        vol_ma_data = []
        for i in range(30):
            d = today - timedelta(days=29 - i)
            vol_labels.append(d.strftime("%-m/%-d"))
            vol_data.append(vol_by_date.get(d, 0))

            # 28-day moving average
            ma_vals = []
            for j in range(28):
                md = d - timedelta(days=j)
                v = ma_by_date.get(md, 0)
                ma_vals.append(v)
            vol_ma_data.append(round(sum(ma_vals) / len(ma_vals)) if ma_vals else 0)

        # ── ACWR (30 days) ──────────────────────────────────────────
        # Need ~58 days of lookback for 28-day chronic window
        acwr_labels = []
        acwr_data = []
        for i in range(30):
            d = today - timedelta(days=29 - i)
            acwr_labels.append(d.strftime("%-m/%-d"))

            # 7-day acute load
            acute_vals = []
            for j in range(7):
                ad = d - timedelta(days=j)
                acute_vals.append(ma_by_date.get(ad, 0))
            acute_avg = sum(acute_vals) / 7.0

            # 28-day chronic load
            chronic_vals = []
            for j in range(28):
                cd = d - timedelta(days=j)
                chronic_vals.append(ma_by_date.get(cd, 0))
            chronic_avg = sum(chronic_vals) / 28.0

            if chronic_avg > 0:
                acwr_data.append(round(acute_avg / chronic_avg, 2))
            else:
                acwr_data.append(None)

        # Current ACWR value and zone label
        current_acwr = acwr_data[-1] if acwr_data else None
        acwr_zone = "Unknown"
        acwr_zone_color = "muted"
        if current_acwr is not None:
            if current_acwr < 0.8:
                acwr_zone = "Detraining"
                acwr_zone_color = "blue"
            elif current_acwr <= 1.3:
                acwr_zone = "Optimal"
                acwr_zone_color = "green"
            elif current_acwr <= 1.7:
                acwr_zone = "Overreach"
                acwr_zone_color = "yellow"
            else:
                acwr_zone = "Injury Risk"
                acwr_zone_color = "red"

        # ── Volume by muscle group (30 days) ────────────────────────
        muscle_vol = {}  # {muscle_group: {date_str: volume}}
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, muscle_group, SUM(weight_lbs * reps) AS volume
                FROM hevy_sets
                WHERE date >= %s AND muscle_group IS NOT NULL
                GROUP BY date, muscle_group
                ORDER BY date ASC
            """, (today - timedelta(days=29),))
            mg_rows = cur.fetchall()

        all_muscles = set()
        mg_by_date = {}  # {date: {muscle: vol}}
        for r in mg_rows:
            d = r["date"]
            mg = r["muscle_group"]
            vol = float(r.get("volume") or 0)
            all_muscles.add(mg)
            if d not in mg_by_date:
                mg_by_date[d] = {}
            mg_by_date[d][mg] = vol

        # Sort muscles for consistent ordering
        all_muscles = sorted(all_muscles)

        mg_labels = []
        mg_datasets = {m: [] for m in all_muscles}
        for i in range(30):
            d = today - timedelta(days=29 - i)
            mg_labels.append(d.strftime("%-m/%-d"))
            day_data = mg_by_date.get(d, {})
            for m in all_muscles:
                mg_datasets[m].append(day_data.get(m, 0))

        # ── Personal records (top 15 by max weight) ─────────────────
        personal_records = []
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (exercise_name) exercise_name, weight_lbs, reps, date
                FROM hevy_sets
                WHERE weight_lbs IS NOT NULL
                ORDER BY exercise_name, weight_lbs DESC
            """)
            pr_rows = cur.fetchall()

        # Sort by weight descending and take top 15
        pr_rows = sorted(pr_rows, key=lambda r: float(r.get("weight_lbs") or 0), reverse=True)[:15]
        for r in pr_rows:
            personal_records.append({
                "exercise": r["exercise_name"],
                "weight": float(r["weight_lbs"]),
                "reps": int(r["reps"]) if r.get("reps") else 0,
                "date": r["date"].strftime("%b %-d, %Y"),
            })

    finally:
        conn.close()

    # Gruvbox accent colors for muscle groups
    mg_colors = [
        '#a9b665', '#7daea3', '#d8a657', '#ea6962', '#d3869b',
        '#89b482', '#e78a4e', '#ddc7a1', '#b0b846', '#fabd2f',
    ]

    return render_template(
        "training.html",
        active_page="training",
        training_score=training_score,
        hero_summary=hero_summary,
        recent_workouts=recent_workouts,
        vol_labels=vol_labels,
        vol_data=vol_data,
        vol_ma_data=vol_ma_data,
        acwr_labels=acwr_labels,
        acwr_data=acwr_data,
        current_acwr=current_acwr,
        acwr_zone=acwr_zone,
        acwr_zone_color=acwr_zone_color,
        mg_labels=mg_labels,
        all_muscles=all_muscles,
        mg_datasets=mg_datasets,
        mg_colors=mg_colors,
        personal_records=personal_records,
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
                hero_summary = _truncate_summary(row["content"])

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
            status_text, status_color = _metric_status(current_for_status, baseline_for_status, std_for_status, higher_is_better)

            # Map status color to pill CSS class
            if status_color == "green":
                pill_class = "normal"
            elif status_color == "amber":
                pill_class = "above" if status_text == "Above" else "below"
            elif status_color == "muted":
                pill_class = "below"
            else:
                pill_class = "normal"

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


@app.route("/nutrition")
def nutrition():
    now = datetime.now(LOCAL_TZ)
    today = now.date()
    yesterday = today - timedelta(days=1)
    week_start = today - timedelta(days=6)

    conn = get_db()
    try:
        # ── Nutrition score (yesterday, since today may be incomplete) ─
        nutrition_result = compute_nutrition_score(conn, yesterday)
        nutrition_score = nutrition_result.get("score") or 0
        targets = nutrition_result.get("targets", {})
        components = nutrition_result.get("components", {})

        high_carb_day = targets.get("high_carb_day", False)
        cal_target = targets.get("calories", 2770)
        carb_target = targets.get("carbs_g", 300)
        protein_target = targets.get("protein_g", 280)

        # Raw values from yesterday
        cal_actual = components.get("calories_raw")
        protein_actual = components.get("protein_raw")
        carbs_actual = components.get("carbs_raw")

        # ── Score pills ──────────────────────────────────────────────
        # Calories pill
        cal_delta = None
        cal_status = "muted"
        if cal_actual is not None:
            cal_delta = int(cal_actual - cal_target)
            pct_off = abs(cal_delta) / cal_target * 100
            cal_status = "green" if pct_off <= 10 else ("amber" if pct_off <= 20 else "red")

        # Protein pill
        protein_delta = None
        protein_status = "muted"
        if protein_actual is not None:
            protein_delta = int(protein_actual - protein_target)
            pct_off = abs(protein_delta) / protein_target * 100
            protein_status = "green" if pct_off <= 10 else ("amber" if pct_off <= 20 else "red")

        # Carbs pill
        carbs_delta = None
        carbs_status = "muted"
        if carbs_actual is not None:
            carbs_delta = int(carbs_actual - carb_target)
            pct_off = abs(carbs_delta) / carb_target * 100
            carbs_status = "green" if pct_off <= 12 else ("amber" if pct_off <= 25 else "red")

        pills = [
            {
                "label": "Calories",
                "value": f"{int(cal_actual):,}" if cal_actual is not None else "--",
                "unit": "kcal",
                "delta": f"{cal_delta:+,}" if cal_delta is not None else "--",
                "target_label": f"vs {int(cal_target):,}",
                "status": cal_status,
            },
            {
                "label": "Protein",
                "value": f"{int(protein_actual)}" if protein_actual is not None else "--",
                "unit": "g",
                "delta": f"{protein_delta:+}" if protein_delta is not None else "--",
                "target_label": f"vs {int(protein_target)}g",
                "status": protein_status,
            },
            {
                "label": "Carbs",
                "value": f"{int(carbs_actual)}" if carbs_actual is not None else "--",
                "unit": "g",
                "delta": f"{carbs_delta:+}" if carbs_delta is not None else "--",
                "target_label": f"vs {int(carb_target)}g" + (" (high)" if high_carb_day else ""),
                "status": carbs_status,
            },
        ]

        # ── Hero summary ─────────────────────────────────────────────
        yesterday_name = yesterday.strftime("%A")
        day_type = "high-carb" if high_carb_day else "standard"
        hero_summary = generate_hero_summary(
            "nutrition", nutrition_score,
            {"targets": {"high_carb_day": high_carb_day}, "components": components},
        )
        # Prepend day type context — note: data is for yesterday
        hero_summary = f"{yesterday_name} was a {day_type} day. {hero_summary}"

        # ── 30-day performance (line chart + donut) ───────────────────
        perf_labels = []
        perf_scores = []
        for i in range(29, -1, -1):
            d = today - timedelta(days=i)
            perf_labels.append(d.strftime("%-m/%-d"))
            try:
                result = compute_nutrition_score(conn, d)
                s = result.get("score")
                perf_scores.append(s if s is not None else None)
            except Exception:
                perf_scores.append(None)

        donut_counts = classify_30_days(conn, today, "nutrition")

        # ── 7 Trend cards (7-day sparklines + target + status) ────────
        # Fetch 7-day nutrition data
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, crono_calories, crono_protein_g, crono_carbs_g,
                       crono_fat_g, crono_fiber_g, crono_sodium_mg,
                       crono_potassium_mg, crono_water_g
                FROM daily_log
                WHERE date >= %s AND date <= %s
                ORDER BY date ASC
            """, (week_start, today))
            spark_rows = cur.fetchall()

        spark_data = {}
        for r in spark_rows:
            spark_data[r["date"]] = r

        trend_defs = [
            # (key, col, label, unit, target_val, target_label, format_fn)
            ("calories",  "crono_calories",   "Calories Consumed", "kcal", None,   None,               "int_comma"),
            ("protein",   "crono_protein_g",  "Protein",           "g",    280,    "Target 280g",      "int"),
            ("carbs",     "crono_carbs_g",    "Carbohydrates",     "g",    None,   None,               "int"),
            ("fat",       "crono_fat_g",      "Total Fat",         "g",    50,     "Target 50g",       "int"),
            ("fiber",     "crono_fiber_g",    "Fiber",             "g",    None,   "Target 26 – 34g",  "one_dec"),
            ("sodium",    "crono_sodium_mg",  "Sodium",            "mg",   None,   "Target 3,500 – 4,500mg", "int_comma"),
            ("potassium", "crono_potassium_mg", "Potassium",       "mg",   3800,   "Target 3,800mg",   "int_comma"),
            ("water",     "crono_water_g",    "Water",             "oz",   120,    "Target 120 oz",    "water_oz"),
        ]

        trends = []
        for key, col, label, unit, target_val, target_label, fmt in trend_defs:
            sparkline = []
            current = None

            for i in range(7):
                d = week_start + timedelta(days=i)
                row = spark_data.get(d)
                val = None
                if row and row.get(col) is not None:
                    val = float(row[col])
                sparkline.append(val if val is not None else 0)
                if d == yesterday or (d == today and current is None):
                    if val is not None and val != 0:
                        current = val

            # Convert water sparkline from grams to oz for chart display
            if fmt == "water_oz":
                sparkline = [round(v / 29.5735, 1) if v else 0 for v in sparkline]

            # Day-specific targets for calories and carbs
            if key == "calories":
                # Use yesterday's target for the pill
                dow = yesterday.weekday()
                day_target = 3170 if dow in (1, 2) else 2770
                target_label = f"Target {int(day_target):,} kcal"
            elif key == "carbs":
                dow = yesterday.weekday()
                day_target = 400 if dow in (1, 2) else 300
                target_label = f"Target {int(day_target)}g"

            # Status: compare current vs target
            status_text = "No data"
            status_color = "muted"
            if current is not None:
                if key == "calories":
                    pct_off = abs(current - day_target) / day_target * 100
                    if pct_off <= 10:
                        status_text, status_color = "On track", "normal"
                    elif current > day_target:
                        status_text, status_color = "Above", "above"
                    else:
                        status_text, status_color = "Below", "below"
                elif key == "protein":
                    pct_off = abs(current - 280) / 280 * 100
                    if pct_off <= 10:
                        status_text, status_color = "On track", "normal"
                    elif current > 280:
                        status_text, status_color = "Above", "normal"
                    else:
                        status_text, status_color = "Below", "below"
                elif key == "carbs":
                    pct_off = abs(current - day_target) / day_target * 100
                    if pct_off <= 12:
                        status_text, status_color = "On track", "normal"
                    elif current > day_target:
                        status_text, status_color = "Above", "above"
                    else:
                        status_text, status_color = "Below", "below"
                elif key == "fat":
                    if current <= 55:
                        status_text, status_color = "On track", "normal"
                    else:
                        status_text, status_color = "Above", "above"
                elif key == "fiber":
                    if 26 <= current <= 34:
                        status_text, status_color = "In range", "normal"
                    elif current < 26:
                        status_text, status_color = "Below", "below"
                    else:
                        status_text, status_color = "Above", "normal"
                elif key == "sodium":
                    if 3500 <= current <= 4500:
                        status_text, status_color = "In range", "normal"
                    elif current < 3500:
                        status_text, status_color = "Below", "below"
                    else:
                        status_text, status_color = "Above", "above"
                elif key == "potassium":
                    pct_off = abs(current - 3800) / 3800 * 100
                    if pct_off <= 15:
                        status_text, status_color = "On track", "normal"
                    elif current < 3800:
                        status_text, status_color = "Below", "below"
                    else:
                        status_text, status_color = "Above", "normal"
                elif key == "water":
                    # current is in grams, target is 120 oz = ~3540g
                    water_oz = current / 29.5735
                    if water_oz >= 100:
                        status_text, status_color = "On track", "normal"
                    elif water_oz >= 60:
                        status_text, status_color = "Below", "below"
                    else:
                        status_text, status_color = "Low", "below"

            # Format display value
            if current is not None:
                if fmt == "water_oz":
                    # Convert grams to fl oz
                    display_val = f"{current / 29.5735:.0f}"
                elif fmt == "int_comma":
                    display_val = f"{int(current):,}"
                elif fmt == "int":
                    display_val = f"{int(current)}"
                elif fmt == "one_dec":
                    display_val = f"{current:.1f}"
                else:
                    display_val = f"{current:.0f}"
            else:
                display_val = "--"

            trends.append({
                "id": key,
                "label": label,
                "value": display_val,
                "unit": unit,
                "sparkline": sparkline,
                "avg_label": target_label or "",
                "status": status_text,
                "status_color": status_color,
                "no_data": False,
            })

    finally:
        conn.close()

    return render_template(
        "nutrition.html",
        active_page="nutrition",
        nutrition_score=nutrition_score,
        hero_summary=hero_summary,
        high_carb_day=high_carb_day,
        day_type=day_type,
        pills=pills,
        perf_labels=perf_labels,
        perf_scores=perf_scores,
        donut_good=donut_counts["good"],
        donut_fair=donut_counts["fair"],
        donut_poor=donut_counts["poor"],
        trends=trends,
    )


# ── Check-in helpers (ported from healthdash) ─────────────────────


def local_today() -> date:
    """Return today's date in the local timezone."""
    return datetime.now(LOCAL_TZ).date()


def delta_pct(current, baseline):
    """Percentage delta from baseline. Returns None if either is None."""
    if current is None or baseline is None or baseline == 0:
        return None
    return round(((float(current) - float(baseline)) / float(baseline)) * 100, 1)


def suggest_score(metric: str, current, baseline) -> tuple[int, str]:
    """
    Returns (score 1-10, context string) calibrated to personal baseline.
    8 = clean baseline, 7 = good with minor friction.
    """
    if current is None or baseline is None:
        return 7, "no data"

    pct = delta_pct(current, baseline)

    # Metrics where higher = better
    positive = metric in ("hrv", "sleep", "deep_sleep", "body_battery")

    if positive:
        if pct >= 10:
            return 9, f"\u2191 {abs(pct)}% above baseline"
        if pct >= 3:
            return 8, "at baseline"
        if pct >= -5:
            return 7, f"slight dip ({abs(pct):.0f}%)"
        if pct >= -15:
            return 6, f"\u2193 {abs(pct):.0f}% below baseline"
        if pct >= -25:
            return 5, f"\u2193 {abs(pct):.0f}% below baseline"
        return 4, f"\u2193 {abs(pct):.0f}% \u2014 meaningfully suppressed"
    else:
        # Metrics where lower = better (resting HR, stress)
        if pct <= -10:
            return 9, f"\u2193 {abs(pct)}% below baseline"
        if pct <= -3:
            return 8, "at baseline"
        if pct <= 5:
            return 7, f"slight elevation ({pct:.0f}%)"
        if pct <= 15:
            return 6, f"\u2191 {pct:.0f}% elevated"
        if pct <= 25:
            return 5, f"\u2191 {pct:.0f}% elevated"
        return 4, f"\u2191 {pct:.0f}% \u2014 meaningfully elevated"


def fetch_period_data(days: int = 14) -> dict:
    """Fetch all metrics for the check-in period and baselines."""
    conn = get_db()
    cur = conn.cursor()

    period_end = local_today() - timedelta(days=1)
    period_start = period_end - timedelta(days=days - 1)

    # ---- Period averages ----
    cur.execute(
        """
        SELECT
            ROUND(AVG(hrv_nightly_avg)::numeric, 1)         AS hrv_avg,
            ROUND(AVG(resting_hr)::numeric, 1)              AS rhr_avg,
            ROUND(AVG(sleep_total_sec / 3600.0)::numeric, 2) AS sleep_hrs_avg,
            ROUND(AVG(sleep_deep_sec / 60.0)::numeric, 1)   AS deep_min_avg,
            ROUND(AVG(body_battery_eod)::numeric, 1)        AS bb_eod_avg,
            ROUND(AVG(body_battery_max)::numeric, 1)        AS bb_max_avg,
            ROUND(AVG(stress_avg)::numeric, 1)              AS stress_avg,
            ROUND(AVG(respiration_avg)::numeric, 1)         AS resp_avg,
            ROUND(AVG(steps)::numeric, 0)                   AS steps_avg,
            MAX(steps)                                       AS steps_max,
            SUM(CASE WHEN steps > 15000 THEN 1 ELSE 0 END)  AS high_step_days,
            ROUND(AVG(intensity_minutes)::numeric, 0)       AS intensity_avg,
            (SELECT weight_lbs FROM kahunas_checkins
             ORDER BY submitted_at DESC LIMIT 1)            AS last_weight,
            ROUND(AVG(crono_calories)::numeric, 0)          AS cal_avg,
            ROUND(AVG(crono_protein_g)::numeric, 0)         AS protein_avg,
            COUNT(CASE WHEN crono_last_meal_time > '20:00:00' THEN 1 END) AS late_meals,
            COUNT(crono_calories)                            AS crono_days_logged
        FROM daily_log
        WHERE date BETWEEN %s AND %s
    """,
        (period_start, period_end),
    )
    period = dict(cur.fetchone())

    # ---- 90-day baselines ----
    baseline_start = period_end - timedelta(days=90)
    cur.execute(
        """
        SELECT
            ROUND(AVG(hrv_nightly_avg)::numeric, 1)          AS hrv_baseline,
            ROUND(AVG(resting_hr)::numeric, 1)               AS rhr_baseline,
            ROUND(AVG(sleep_total_sec / 3600.0)::numeric, 2) AS sleep_baseline,
            ROUND(AVG(sleep_deep_sec / 60.0)::numeric, 1)    AS deep_baseline,
            ROUND(AVG(body_battery_eod)::numeric, 1)         AS bb_baseline,
            ROUND(AVG(steps)::numeric, 0)                    AS steps_baseline
        FROM daily_log
        WHERE date BETWEEN %s AND %s
          AND hrv_nightly_avg IS NOT NULL
    """,
        (baseline_start, period_end),
    )
    baseline = dict(cur.fetchone())

    # ---- Training for period ----
    cur.execute(
        """
        SELECT
            COUNT(*)                                              AS session_count,
            ROUND(SUM(hevy_total_volume_lbs)::numeric, 0)        AS total_volume,
            ROUND(AVG(hevy_total_volume_lbs)::numeric, 0)        AS avg_volume,
            (SELECT ROUND(SUM(hevy_total_volume_lbs)::numeric, 0)
             FROM daily_log
             WHERE date BETWEEN %s AND %s
               AND hevy_session_count > 0)                       AS prior_volume,
            (SELECT COUNT(*) FROM daily_log
             WHERE date BETWEEN %s AND %s
               AND hevy_session_count > 0)                       AS prior_sessions
        FROM daily_log
        WHERE date BETWEEN %s AND %s
          AND hevy_session_count > 0
    """,
        (
            period_start - timedelta(days=days),
            period_start - timedelta(days=1),
            period_start - timedelta(days=days),
            period_start - timedelta(days=1),
            period_start,
            period_end,
        ),
    )
    training = dict(cur.fetchone())

    # ---- ACWR ----
    cur.execute(
        """
        SELECT
            ROUND(
                NULLIF(AVG(CASE WHEN date >= %s THEN hevy_total_volume_lbs END), 0) /
                NULLIF(AVG(CASE WHEN date >= %s THEN hevy_total_volume_lbs END), 0)
            ::numeric, 2) AS acwr
        FROM daily_log
        WHERE date BETWEEN %s AND %s
    """,
        (
            period_end - timedelta(days=6),
            period_end - timedelta(days=27),
            period_end - timedelta(days=27),
            period_end,
        ),
    )
    acwr_row = cur.fetchone()
    training["acwr"] = (
        float(acwr_row["acwr"]) if acwr_row and acwr_row["acwr"] else None
    )

    conn.close()
    return {
        "period": period,
        "baseline": baseline,
        "training": training,
        "period_start": period_start,
        "period_end": period_end,
        "days": days,
    }


def build_scores(data: dict) -> dict:
    p = data["period"]
    b = data["baseline"]

    sleep_score, sleep_ctx = suggest_score(
        "sleep", p["sleep_hrs_avg"], b["sleep_baseline"]
    )
    deep_score, deep_ctx = suggest_score(
        "deep_sleep", p["deep_min_avg"], b["deep_baseline"]
    )
    hrv_score, hrv_ctx = suggest_score("hrv", p["hrv_avg"], b["hrv_baseline"])
    rhr_score, rhr_ctx = suggest_score("rhr", p["rhr_avg"], b["rhr_baseline"])
    bb_score, bb_ctx = suggest_score("body_battery", p["bb_eod_avg"], b["bb_baseline"])
    stress_score, stress_ctx = suggest_score("stress", p["stress_avg"], 50)

    recovery_score = round(
        (hrv_score * 0.4) + (deep_score * 0.35) + (rhr_score * 0.25)
    )
    recovery_ctx = f"HRV {hrv_ctx}, deep sleep {deep_ctx}"

    energy_score = round((bb_score * 0.6) + (stress_score * 0.4))
    energy_ctx = f"body battery {bb_ctx}"

    t = data["training"]
    if t["acwr"] and t["acwr"] > 1.3:
        fatigue_score = max(4, recovery_score - 2)
        fatigue_ctx = f"ACWR {t['acwr']} \u2014 above optimal range"
    elif t["acwr"] and t["acwr"] < 0.8:
        fatigue_score = min(9, recovery_score + 1)
        fatigue_ctx = f"ACWR {t['acwr']} \u2014 deload territory"
    else:
        fatigue_score = recovery_score
        fatigue_ctx = f"ACWR {t['acwr'] or 'n/a'} \u2014 within range"

    return {
        "sleep_quality": (sleep_score, sleep_ctx),
        "recovery": (recovery_score, recovery_ctx),
        "energy": (energy_score, energy_ctx),
        "stress": (stress_score, stress_ctx),
        "fatigue": (fatigue_score, fatigue_ctx),
        "hunger": (7, "no signal \u2014 log more Cronometer data"),
        "digestion": (7, "no signal"),
    }


def build_flags(data: dict, scores: dict) -> list[dict]:
    """Generate period flags."""
    flags = []
    p = data["period"]
    b = data["baseline"]
    t = data["training"]

    hrv_delta = delta_pct(p["hrv_avg"], b["hrv_baseline"])
    if hrv_delta is not None:
        if hrv_delta <= -15:
            flags.append(
                {
                    "type": "warn",
                    "text": f"HRV trending down \u2014 avg {p['hrv_avg']}, baseline {b['hrv_baseline']}",
                }
            )
        elif hrv_delta >= 10:
            flags.append(
                {
                    "type": "good",
                    "text": f"HRV above baseline \u2014 avg {p['hrv_avg']} vs {b['hrv_baseline']}",
                }
            )

    deep_delta = delta_pct(p["deep_min_avg"], b["deep_baseline"])
    if deep_delta is not None and deep_delta <= -15:
        flags.append(
            {
                "type": "warn",
                "text": f"Deep sleep below baseline \u2014 {p['deep_min_avg']} min avg vs {b['deep_baseline']} min",
            }
        )

    if t["prior_volume"] and t["total_volume"]:
        vol_delta = delta_pct(t["total_volume"], t["prior_volume"])
        if vol_delta is not None:
            if vol_delta >= 10:
                flags.append(
                    {
                        "type": "good",
                        "text": f"Training volume up {vol_delta}% vs prior period",
                    }
                )
            elif vol_delta <= -20:
                flags.append(
                    {
                        "type": "info",
                        "text": f"Training volume down {abs(vol_delta)}% vs prior period",
                    }
                )

    if t["acwr"]:
        if t["acwr"] > 1.3:
            flags.append(
                {
                    "type": "warn",
                    "text": f"ACWR {t['acwr']} \u2014 acute load above optimal range",
                }
            )
        elif t["acwr"] < 0.8:
            flags.append(
                {"type": "info", "text": f"ACWR {t['acwr']} \u2014 deload territory"}
            )
        else:
            flags.append(
                {
                    "type": "good",
                    "text": f"ACWR {t['acwr']} \u2014 training load within optimal range",
                }
            )

    if p["late_meals"] and p["late_meals"] > 3:
        flags.append(
            {
                "type": "warn",
                "text": f"Late meals (>8pm) on {p['late_meals']} of {data['days']} days",
            }
        )

    if p["protein_avg"]:
        target = 280
        if p["protein_avg"] >= target * 0.95:
            flags.append(
                {
                    "type": "good",
                    "text": f"Protein on target \u2014 {p['protein_avg']}g avg vs {target}g goal",
                }
            )
        else:
            flags.append(
                {
                    "type": "info",
                    "text": f"Protein averaged {p['protein_avg']}g \u2014 {target - int(p['protein_avg'])}g below target",
                }
            )

    if p["high_step_days"] and p["high_step_days"] >= 4:
        flags.append(
            {
                "type": "info",
                "text": f"{p['high_step_days']} high step days (>15k) \u2014 significant floor load",
            }
        )

    return flags


def build_narrative(data: dict, scores: dict) -> str:
    """Generate a draft narrative paragraph."""
    p = data["period"]
    b = data["baseline"]
    t = data["training"]
    lines = []

    if p["hrv_avg"] and b["hrv_baseline"]:
        delta = delta_pct(p["hrv_avg"], b["hrv_baseline"])
        if delta is not None:
            direction = "above" if delta > 0 else "below"
            lines.append(
                f"HRV averaged {p['hrv_avg']} this period vs my baseline of "
                f"{b['hrv_baseline']} \u2014 {abs(delta):.0f}% {direction} baseline."
            )

    if p["deep_min_avg"] and p["late_meals"]:
        lines.append(
            f"Sleep deep average was {p['deep_min_avg']} min"
            + (
                f", with {p['late_meals']} late meals logged after 8pm."
                if p["late_meals"]
                else "."
            )
        )

    if t["session_count"] and t["total_volume"]:
        acwr_str = f" ACWR {t['acwr']}." if t["acwr"] else ""
        lines.append(
            f"Logged {t['session_count']} training sessions, "
            f"{int(t['total_volume']):,} lbs total volume.{acwr_str}"
        )

    if p["last_weight"]:
        lines.append(f"Current weight {p['last_weight']} lbs.")

    if p["protein_avg"]:
        lines.append(f"Protein averaged {p['protein_avg']}g against a 280g target.")

    lines.append(
        "Subjectively energy and fatigue feel consistent with what the data shows."
    )

    return " ".join(lines)


# ── Check-in route ─────────────────────────────────────────────────

@app.route("/checkin")
@app.route("/checkin/<int:days>")
def checkin(days: int = 14):
    """Kahunas check-in prep view."""
    data = fetch_period_data(days)
    scores = build_scores(data)
    flags = build_flags(data, scores)
    narrative = build_narrative(data, scores)

    # Most recent weekly insight
    weekly_insight = None
    body_weight = None
    try:
        conn2 = get_db()
        cur2 = conn2.cursor()
        cur2.execute(
            "SELECT content FROM insights WHERE type = 'weekly' ORDER BY date DESC LIMIT 1"
        )
        wi_row = cur2.fetchone()
        if wi_row:
            weekly_insight = wi_row["content"]
        cur2.execute(
            "SELECT body_weight_lbs FROM daily_log WHERE body_weight_lbs IS NOT NULL ORDER BY date DESC LIMIT 1"
        )
        wt_row = cur2.fetchone()
        if wt_row:
            body_weight = float(wt_row["body_weight_lbs"])
        conn2.close()
    except Exception:
        pass

    return render_template(
        "checkin.html",
        active_page="checkin",
        p=data["period"],
        b=data["baseline"],
        t=data["training"],
        scores=scores,
        flags=flags,
        narrative=narrative,
        period_start=data["period_start"],
        period_end=data["period_end"],
        days=days,
        delta=delta_pct,
        weekly_insight=weekly_insight,
        body_weight=body_weight,
    )


@app.route("/insights")
def insights_page():
    """Insights archive — daily, weekly, monthly."""
    tab = request.args.get("tab", "daily")
    if tab not in ("daily", "weekly", "monthly"):
        tab = "daily"
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT date, type, content FROM insights WHERE type = %s ORDER BY date DESC LIMIT 30",
        (tab,),
    )
    items = cur.fetchall()
    conn.close()
    return render_template("insights.html", active_page="insights", tab=tab, items=items)


# ── Correlations ─────────────────────────────────────────────────────


def run_correlations_for_display() -> dict:
    """Run correlation analysis and return results for template rendering."""
    try:
        import numpy as np
        from scipy import stats
        from statsmodels.stats.multitest import multipletests
        import pandas as pd
    except ImportError:
        return {
            "findings": [],
            "total_tests": 0,
            "significant_count": 0,
            "num_pairs": 0,
            "num_lags": 0,
            "alpha": 0.10,
            "error": "Required packages not installed (scipy, statsmodels, pandas).",
        }

    lags = [0, 1, 2, 3, 7]
    min_n = 15
    alpha = 0.10

    pairs = [
        ("hrv_nightly_avg", "hevy_total_volume_lbs"),
        ("sleep_deep_sec", "crono_last_meal_time"),
        ("sleep_deep_sec", "steps"),
        ("body_battery_eod", "steps"),
        ("hrv_nightly_avg", "stress_avg"),
        ("sleep_total_sec", "resting_hr"),
        ("hrv_nightly_avg", "crono_protein_g"),
        ("sleep_deep_sec", "hevy_total_volume_lbs"),
    ]

    interpretations = {
        ("hrv_nightly_avg", "hevy_total_volume_lbs", "pos"):
            "Higher HRV nights precede higher training volume.",
        ("hrv_nightly_avg", "hevy_total_volume_lbs", "neg"):
            "Higher HRV nights precede lower training volume.",
        ("sleep_deep_sec", "crono_last_meal_time", "neg"):
            "Later last meal correlates with less deep sleep.",
        ("sleep_deep_sec", "crono_last_meal_time", "pos"):
            "Earlier last meal correlates with less deep sleep (unexpected).",
        ("sleep_deep_sec", "steps", "pos"):
            "More deep sleep associates with higher step counts.",
        ("sleep_deep_sec", "steps", "neg"):
            "More deep sleep associates with fewer steps (possible rest-day effect).",
        ("body_battery_eod", "steps", "neg"):
            "Higher step days strongly predict lower end-of-day body battery.",
        ("body_battery_eod", "steps", "pos"):
            "Higher step days predict higher end-of-day body battery.",
        ("hrv_nightly_avg", "stress_avg", "neg"):
            "Higher HRV correlates with lower average stress.",
        ("hrv_nightly_avg", "stress_avg", "pos"):
            "Higher HRV correlates with higher stress (unexpected).",
        ("sleep_total_sec", "resting_hr", "neg"):
            "More total sleep associates with lower resting heart rate.",
        ("sleep_total_sec", "resting_hr", "pos"):
            "More total sleep associates with higher resting heart rate.",
        ("hrv_nightly_avg", "crono_protein_g", "pos"):
            "Higher HRV nights associate with higher protein intake.",
        ("hrv_nightly_avg", "crono_protein_g", "neg"):
            "Higher HRV nights associate with lower protein intake.",
        ("sleep_deep_sec", "hevy_total_volume_lbs", "pos"):
            "More deep sleep precedes higher training volume.",
        ("sleep_deep_sec", "hevy_total_volume_lbs", "neg"):
            "More deep sleep precedes lower training volume.",
    }

    # Use a plain connection (no RealDictCursor) for pd.read_sql compatibility
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        import pandas as pd
        df = pd.read_sql("SELECT * FROM daily_log ORDER BY date", conn)
    finally:
        conn.close()

    if df.empty:
        return {
            "findings": [],
            "exploratory": [],
            "total_tests": 0,
            "significant_count": 0,
            "num_pairs": len(pairs),
            "num_lags": len(lags),
            "alpha": alpha,
        }

    df.sort_values("date", inplace=True)
    df.set_index("date", inplace=True)

    # Convert last meal time to decimal hours
    if "crono_last_meal_time" in df.columns:

        def _to_hours(val):
            if pd.isna(val):
                return np.nan
            if hasattr(val, "hour") and hasattr(val, "minute"):
                return val.hour + val.minute / 60.0 + val.second / 3600.0
            if hasattr(val, "total_seconds"):
                return val.total_seconds() / 3600.0
            return np.nan

        df["crono_last_meal_time"] = df["crono_last_meal_time"].apply(_to_hours)

    # Ensure numeric
    for col in [
        "hrv_nightly_avg", "hevy_total_volume_lbs", "sleep_deep_sec",
        "crono_last_meal_time", "steps", "body_battery_eod", "stress_avg",
        "sleep_total_sec", "resting_hr", "crono_protein_g",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Run correlations
    results = []
    for x_col, y_col in pairs:
        if x_col not in df.columns or y_col not in df.columns:
            continue
        for lag in lags:
            x = df[x_col]
            y = df[y_col].shift(-lag)
            mask = x.notna() & y.notna()
            x_clean = x[mask].astype(float)
            y_clean = y[mask].astype(float)
            if len(x_clean) < min_n:
                continue
            if x_clean.std() == 0 or y_clean.std() == 0:
                continue
            r, p = stats.pearsonr(x_clean, y_clean)
            results.append({
                "x": x_col, "y": y_col, "lag_days": lag,
                "r": r, "p_raw": p, "n": len(x_clean),
            })

    if not results:
        return {
            "findings": [],
            "exploratory": [],
            "total_tests": 0,
            "significant_count": 0,
            "num_pairs": len(pairs),
            "num_lags": len(lags),
            "alpha": alpha,
        }

    # FDR correction
    p_values = np.array([r["p_raw"] for r in results])
    reject, p_corrected, _, _ = multipletests(p_values, alpha=alpha, method="fdr_bh")

    findings = []
    for i, res in enumerate(results):
        if reject[i]:
            sign = "pos" if res["r"] > 0 else "neg"
            interp_key = (res["x"], res["y"], sign)
            interp = interpretations.get(
                interp_key,
                f"{res['x']} is {'positively' if res['r'] > 0 else 'negatively'} correlated with {res['y']}.",
            )
            findings.append({
                "x": res["x"],
                "y": res["y"],
                "lag_days": res["lag_days"],
                "r": res["r"],
                "p_corrected": p_corrected[i],
                "n": res["n"],
                "interpretation": interp,
            })

    # Sort by absolute r
    findings.sort(key=lambda f: abs(f["r"]), reverse=True)

    # Build exploratory results (top uncorrected) as fallback
    exploratory = []
    if not findings:
        # Show strongest raw correlations (p < 0.05 uncorrected) so the page isn't empty
        for i, res in enumerate(results):
            if res["p_raw"] < 0.05:
                sign = "pos" if res["r"] > 0 else "neg"
                interp_key = (res["x"], res["y"], sign)
                interp = interpretations.get(
                    interp_key,
                    f"{res['x']} is {'positively' if res['r'] > 0 else 'negatively'} correlated with {res['y']}.",
                )
                exploratory.append({
                    "x": res["x"],
                    "y": res["y"],
                    "lag_days": res["lag_days"],
                    "r": res["r"],
                    "p_corrected": res["p_raw"],  # raw p in this case
                    "n": res["n"],
                    "interpretation": interp,
                })
        exploratory.sort(key=lambda f: abs(f["r"]), reverse=True)
        exploratory = exploratory[:10]  # top 10

    return {
        "findings": findings,
        "exploratory": exploratory,
        "total_tests": len(results),
        "significant_count": len(findings),
        "num_pairs": len(pairs),
        "num_lags": len(lags),
        "alpha": alpha,
    }


@app.route("/correlations")
def correlations_page():
    results = run_correlations_for_display()
    return render_template("correlations.html", active_page="correlations", results=results)


# ── Run ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(port=5100, debug=True)
