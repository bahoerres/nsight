#!/usr/bin/env python3
"""
generate_insights.py — AI-powered health insight generator for Healthdash.

Queries the database for health metrics, sends them to the Anthropic API
with personalized context, and stores the resulting insights.

Usage:
    python generate_insights.py              # generates whatever is due today
    python generate_insights.py --daily      # daily morning report (about yesterday's data)
    python generate_insights.py --weekly     # force weekly insight
    python generate_insights.py --monthly    # force monthly insight
    python generate_insights.py --date 2026-03-15 --daily  # specific date
    python generate_insights.py --backfill --since 2025-09-01
    python generate_insights.py --backfill --since 2025-09-01 --type daily
    python generate_insights.py --force      # regenerate even if already exists
"""

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta
from calendar import monthrange

from tz import today as local_today

import anthropic
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-opus-4-6"
PROMPT_VERSIONS = {"daily": "daily-v2", "weekly": "weekly-v2", "monthly": "monthly-v2"}
MAX_TOKENS = {"daily": 512, "weekly": 768, "monthly": 1024}

# Load athlete context from external file (editable without code changes)
_CONTEXT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "athlete_context.txt"
)
with open(_CONTEXT_PATH) as _f:
    ATHLETE_CONTEXT = _f.read().strip()


def get_db():
    return psycopg2.connect(
        os.environ["DATABASE_URL"], cursor_factory=psycopg2.extras.RealDictCursor
    )


def insight_exists(conn, target_date: date, insight_type: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM insights WHERE date = %s AND type = %s LIMIT 1",
        (target_date, insight_type),
    )
    return cur.fetchone() is not None


def store_insight(
    conn,
    target_date: date,
    insight_type: str,
    content: str,
    model: str,
    prompt_version: str,
    tokens_used: int,
):
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO insights (date, type, content, model, prompt_version, tokens_used)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (date, type) DO UPDATE SET
               content = EXCLUDED.content,
               model = EXCLUDED.model,
               prompt_version = EXCLUDED.prompt_version,
               tokens_used = EXCLUDED.tokens_used,
               created_at = now()""",
        (target_date, insight_type, content, model, prompt_version, tokens_used),
    )
    conn.commit()


def has_sufficient_data(conn, target_date: date) -> bool:
    cur = conn.cursor()
    cur.execute(
        """SELECT hrv_nightly_avg, sleep_total_sec FROM daily_log WHERE date = %s""",
        (target_date,),
    )
    row = cur.fetchone()
    if not row:
        return False
    return row["hrv_nightly_avg"] is not None or row["sleep_total_sec"] is not None


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


def fetch_daily_data(conn, target_date: date) -> dict | None:
    cur = conn.cursor()

    cur.execute(
        """SELECT date, resting_hr,
                  body_battery_eod, body_battery_max,
                  stress_avg, steps, respiration_avg,
                  hevy_session_count, hevy_total_volume_lbs,
                  hevy_muscle_groups, hevy_session_duration_min,
                  crono_calories, crono_protein_g, crono_carbs_g,
                  crono_fat_g, crono_fiber_g, crono_last_meal_time
           FROM daily_log WHERE date = %s""",
        (target_date,),
    )
    row = cur.fetchone()
    if not row:
        return None
    row = dict(row)

    # Sleep & HRV are keyed by wake date (= target_date + 1)
    wake_date = target_date + timedelta(days=1)
    cur.execute(
        """SELECT hrv_nightly_avg,
                  sleep_total_sec / 3600.0 as sleep_hrs,
                  sleep_deep_sec / 60.0 as deep_min
           FROM daily_log WHERE date = %s""",
        (wake_date,),
    )
    sleep_row = cur.fetchone()
    if sleep_row:
        row.update({k: v for k, v in dict(sleep_row).items() if v is not None})
    else:
        # Fall back to same-row data if wake-date row doesn't exist yet
        cur.execute(
            """SELECT hrv_nightly_avg,
                      sleep_total_sec / 3600.0 as sleep_hrs,
                      sleep_deep_sec / 60.0 as deep_min
               FROM daily_log WHERE date = %s""",
            (target_date,),
        )
        fallback = cur.fetchone()
        if fallback:
            row.update({k: v for k, v in dict(fallback).items() if v is not None})

    baseline_end = target_date - timedelta(days=1)
    baseline_start = target_date - timedelta(days=90)
    cur.execute(
        """SELECT
               AVG(hrv_nightly_avg) as hrv_baseline,
               AVG(resting_hr) as rhr_baseline,
               AVG(sleep_total_sec / 3600.0) as sleep_baseline,
               AVG(sleep_deep_sec / 60.0) as deep_baseline,
               AVG(body_battery_eod) as bb_baseline,
               AVG(steps) as steps_baseline
           FROM daily_log
           WHERE date BETWEEN %s AND %s
             AND hrv_nightly_avg IS NOT NULL""",
        (baseline_start, baseline_end),
    )
    baselines = cur.fetchone()

    return {"day": row, "baselines": dict(baselines)}


def fetch_weekly_data(conn, week_end: date) -> dict | None:
    week_start = week_end - timedelta(days=6)
    prior_end = week_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=6)
    baseline_start = week_end - timedelta(days=90)

    cur = conn.cursor()

    # Current week averages
    cur.execute(
        """SELECT
               ROUND(AVG(hrv_nightly_avg)::numeric, 1) as hrv_avg,
               ROUND(AVG(resting_hr)::numeric, 1) as rhr_avg,
               ROUND(AVG(sleep_total_sec / 3600.0)::numeric, 2) as sleep_avg,
               ROUND(AVG(sleep_deep_sec / 60.0)::numeric, 1) as deep_avg,
               ROUND(AVG(body_battery_eod)::numeric, 1) as bb_avg,
               ROUND(AVG(stress_avg)::numeric, 1) as stress_avg,
               ROUND(AVG(steps)::numeric, 0) as steps_avg,
               SUM(CASE WHEN steps > 15000 THEN 1 ELSE 0 END) as high_step_days,
               COUNT(CASE WHEN hevy_session_count > 0 THEN 1 END) as sessions,
               ROUND(SUM(hevy_total_volume_lbs)::numeric, 0) as volume,
               ROUND(AVG(crono_calories)::numeric, 0) as cal_avg,
               ROUND(AVG(crono_protein_g)::numeric, 0) as protein_avg,
               COUNT(crono_calories) as crono_days
           FROM daily_log WHERE date BETWEEN %s AND %s""",
        (week_start, week_end),
    )
    current = cur.fetchone()
    if not current:
        return None

    # Prior week
    cur.execute(
        """SELECT
               COUNT(CASE WHEN hevy_session_count > 0 THEN 1 END) as prior_sessions,
               ROUND(SUM(hevy_total_volume_lbs)::numeric, 0) as prior_volume
           FROM daily_log WHERE date BETWEEN %s AND %s""",
        (prior_start, prior_end),
    )
    prior = cur.fetchone()

    # Baselines
    cur.execute(
        """SELECT
               ROUND(AVG(hrv_nightly_avg)::numeric, 1) as hrv_baseline,
               ROUND(AVG(resting_hr)::numeric, 1) as rhr_baseline,
               ROUND(AVG(sleep_total_sec / 3600.0)::numeric, 2) as sleep_baseline,
               ROUND(AVG(sleep_deep_sec / 60.0)::numeric, 1) as deep_baseline,
               ROUND(AVG(body_battery_eod)::numeric, 1) as bb_baseline
           FROM daily_log
           WHERE date BETWEEN %s AND %s
             AND hrv_nightly_avg IS NOT NULL""",
        (baseline_start, week_end),
    )
    baselines = cur.fetchone()

    # ACWR
    cur.execute(
        """SELECT ROUND(
               NULLIF(AVG(CASE WHEN date >= %s THEN hevy_total_volume_lbs END), 0) /
               NULLIF(AVG(CASE WHEN date >= %s THEN hevy_total_volume_lbs END), 0)
           ::numeric, 2) as acwr
           FROM daily_log WHERE date BETWEEN %s AND %s""",
        (
            week_end - timedelta(days=6),
            week_end - timedelta(days=27),
            week_end - timedelta(days=27),
            week_end,
        ),
    )
    acwr_row = cur.fetchone()

    # Muscle groups this week
    cur.execute(
        """SELECT DISTINCT unnest(hevy_muscle_groups) as mg
           FROM daily_log
           WHERE date BETWEEN %s AND %s AND hevy_muscle_groups IS NOT NULL""",
        (week_start, week_end),
    )
    muscles = [r["mg"] for r in cur.fetchall()]

    return {
        "week_start": week_start,
        "week_end": week_end,
        "current": dict(current),
        "prior": dict(prior),
        "baselines": dict(baselines),
        "acwr": float(acwr_row["acwr"]) if acwr_row and acwr_row["acwr"] else None,
        "muscles": muscles,
    }


def fetch_monthly_data(conn, month_end: date) -> dict | None:
    month_start = month_end - timedelta(days=29)
    prior_end = month_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=29)

    cur = conn.cursor()

    # Current 30 days
    cur.execute(
        """SELECT
               ROUND(AVG(hrv_nightly_avg)::numeric, 1) as hrv_avg,
               ROUND(AVG(resting_hr)::numeric, 1) as rhr_avg,
               ROUND(AVG(sleep_total_sec / 3600.0)::numeric, 2) as sleep_avg,
               ROUND(AVG(sleep_deep_sec / 60.0)::numeric, 1) as deep_avg,
               ROUND(AVG(body_battery_eod)::numeric, 1) as bb_avg,
               ROUND(AVG(stress_avg)::numeric, 1) as stress_avg,
               ROUND(AVG(steps)::numeric, 0) as steps_avg,
               SUM(CASE WHEN steps > 15000 THEN 1 ELSE 0 END) as high_step_days,
               COUNT(CASE WHEN hevy_session_count > 0 THEN 1 END) as sessions,
               ROUND(SUM(hevy_total_volume_lbs)::numeric, 0) as total_volume,
               ROUND(AVG(hevy_total_volume_lbs) FILTER (WHERE hevy_session_count > 0)::numeric, 0) as avg_session_volume,
               ROUND(AVG(crono_calories)::numeric, 0) as cal_avg,
               ROUND(AVG(crono_protein_g)::numeric, 0) as protein_avg,
               COUNT(crono_calories) as crono_days
           FROM daily_log WHERE date BETWEEN %s AND %s""",
        (month_start, month_end),
    )
    current = cur.fetchone()
    if not current:
        return None

    # Prior 30 days
    cur.execute(
        """SELECT
               ROUND(AVG(hrv_nightly_avg)::numeric, 1) as hrv_avg,
               ROUND(AVG(resting_hr)::numeric, 1) as rhr_avg,
               ROUND(AVG(sleep_total_sec / 3600.0)::numeric, 2) as sleep_avg,
               ROUND(AVG(body_battery_eod)::numeric, 1) as bb_avg,
               COUNT(CASE WHEN hevy_session_count > 0 THEN 1 END) as sessions,
               ROUND(SUM(hevy_total_volume_lbs)::numeric, 0) as total_volume
           FROM daily_log WHERE date BETWEEN %s AND %s""",
        (prior_start, prior_end),
    )
    prior = cur.fetchone()

    # Top correlation findings from derived_daily
    cur.execute(
        """SELECT
               ROUND(AVG(acwr_volume)::numeric, 2) as avg_acwr,
               ROUND(AVG(hrv_delta_pct)::numeric, 1) as avg_hrv_delta,
               SUM(CASE WHEN hrv_anomaly THEN 1 ELSE 0 END) as hrv_anomaly_days,
               SUM(CASE WHEN sleep_anomaly THEN 1 ELSE 0 END) as sleep_anomaly_days,
               SUM(CASE WHEN stress_anomaly THEN 1 ELSE 0 END) as stress_anomaly_days
           FROM derived_daily WHERE date BETWEEN %s AND %s""",
        (month_start, month_end),
    )
    derived = cur.fetchone()

    # Personal records this month
    cur.execute(
        """SELECT exercise_name, MAX(weight_lbs) as max_weight, reps
           FROM hevy_sets
           WHERE date BETWEEN %s AND %s AND weight_lbs IS NOT NULL
           GROUP BY exercise_name, reps
           ORDER BY max_weight DESC LIMIT 5""",
        (month_start, month_end),
    )
    prs = cur.fetchall()

    return {
        "month_start": month_start,
        "month_end": month_end,
        "current": dict(current),
        "prior": dict(prior),
        "derived": dict(derived) if derived else {},
        "prs": [dict(r) for r in prs],
    }


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


def _fmt(val, fmt=".1f", suffix=""):
    if val is None:
        return "no data"
    return f"{val:{fmt}}{suffix}"


def _pct_delta(current, baseline):
    if current is None or baseline is None or float(baseline) == 0:
        return None
    return ((float(current) - float(baseline)) / float(baseline)) * 100


def build_daily_prompt(data: dict) -> str:
    d = data["day"]
    b = data["baselines"]

    steps = d.get("steps")
    steps_note = ""
    if steps and steps >= 18000:
        steps_note = " (high activity day)"

    training_section = ""
    if d.get("hevy_session_count") and d["hevy_session_count"] > 0:
        muscles = (
            ", ".join(d["hevy_muscle_groups"])
            if d.get("hevy_muscle_groups")
            else "not logged"
        )
        training_section = f"""
TRAINING:
- Volume: {_fmt(d.get("hevy_total_volume_lbs"), ",.0f", " lbs")}
- Duration: {d.get("hevy_session_duration_min") or "?"} min
- Muscle groups: {muscles}"""

    nutrition_section = ""
    if d.get("crono_calories"):
        last_meal = ""
        if d.get("crono_last_meal_time"):
            lm = d["crono_last_meal_time"]
            if hasattr(lm, "strftime"):
                last_meal = f"\n- Last meal: {lm.strftime('%I:%M %p')}"
            else:
                last_meal = f"\n- Last meal: {lm}"
        nutrition_section = f"""
NUTRITION:
- Calories: {_fmt(d.get("crono_calories"), ",.0f")}
- Protein: {_fmt(d.get("crono_protein_g"), ".0f", "g")}
- Carbs: {_fmt(d.get("crono_carbs_g"), ".0f", "g")}
- Fat: {_fmt(d.get("crono_fat_g"), ".0f", "g")}
- Fiber: {_fmt(d.get("crono_fiber_g"), ".1f", "g")}{last_meal}"""

    hrv_delta = _pct_delta(d.get("hrv_nightly_avg"), b.get("hrv_baseline"))
    hrv_delta_str = f" ({hrv_delta:+.0f}% vs baseline)" if hrv_delta is not None else ""

    day_name = date.fromisoformat(str(d["date"])).strftime("%A")
    is_high_carb = day_name in ("Tuesday", "Wednesday")
    carb_day_type = "HIGH-CARB" if is_high_carb else "LOW-CARB"
    carb_target = "400g carbs / 3,170 kcal" if is_high_carb else "300g carbs / 2,770 kcal"
    return f"""You are a personal health analyst. Here is the athlete's data for {day_name}, {d["date"]}:
NOTE: {day_name} is a {carb_day_type} day. Evaluate nutrition against {carb_day_type} targets: 280g protein / {carb_target} / 50g fat.

{day_name.upper()}'S METRICS:
- HRV: {_fmt(d.get("hrv_nightly_avg"), ".1f", " ms")}{hrv_delta_str} (personal baseline: {_fmt(b.get("hrv_baseline"), ".1f", " ms")})
- Resting HR: {_fmt(d.get("resting_hr"), ".0f", " bpm")} (baseline: {_fmt(b.get("rhr_baseline"), ".0f", " bpm")})
- Sleep: {_fmt(d.get("sleep_hrs"), ".1f", " hours")} total, {_fmt(d.get("deep_min"), ".0f", " min")} deep sleep
- Body battery end of day: {_fmt(d.get("body_battery_eod"), ".0f")} (baseline: {_fmt(b.get("bb_baseline"), ".0f")})
- Steps: {f"{d['steps']:,}" if isinstance(d.get("steps"), int) else "no data"}{steps_note}
- Stress avg: {_fmt(d.get("stress_avg"), ".0f")}/100
- Respiration: {_fmt(d.get("respiration_avg"), ".1f", " brpm")}
{training_section}
{nutrition_section}

ATHLETE CONTEXT:
{ATHLETE_CONTEXT}

Write 2-3 sentences of plain-English insight about {day_name}. Be specific about what the numbers mean for this athlete, not for a generic person. Note any meaningful deviations from baseline. If he trained, comment on how recovery metrics look given that load. Be direct and useful, not generic wellness advice.
Do not include a title or heading — the UI already provides one. Write plain prose only."""


def build_weekly_prompt(data: dict) -> str:
    c = data["current"]
    p = data["prior"]
    b = data["baselines"]

    hrv_delta = _pct_delta(c.get("hrv_avg"), b.get("hrv_baseline"))
    hrv_delta_str = f"{hrv_delta:+.0f}%" if hrv_delta is not None else "n/a"

    nutrition_section = ""
    if c.get("crono_days") and c["crono_days"] > 0:
        nutrition_section = f"""
NUTRITION ({c["crono_days"]} days logged):
- Avg calories: {_fmt(c.get("cal_avg"), ",.0f")} (weekly avg target ~2,884)
- Avg protein: {_fmt(c.get("protein_avg"), ".0f", "g")} (target 280g)"""

    muscles_str = ", ".join(data["muscles"]) if data["muscles"] else "none logged"

    return f"""You are a personal health analyst for Blake. Here is his health summary for the week of {data["week_start"]} to {data["week_end"]}:

WEEKLY AVERAGES vs BASELINE:
- HRV: {_fmt(c.get("hrv_avg"), ".1f", " ms")} (baseline: {_fmt(b.get("hrv_baseline"), ".1f", " ms")}, {hrv_delta_str})
- Resting HR: {_fmt(c.get("rhr_avg"), ".1f", " bpm")} (baseline: {_fmt(b.get("rhr_baseline"), ".1f", " bpm")})
- Sleep: {_fmt(c.get("sleep_avg"), ".1f", " hrs")} avg, {_fmt(c.get("deep_avg"), ".0f", " min")} deep sleep avg
- Body battery EOD: {_fmt(c.get("bb_avg"), ".0f")} (baseline: {_fmt(b.get("bb_baseline"), ".0f")})
- Steps: {_fmt(c.get("steps_avg"), ",.0f")} avg daily
- Stress: {_fmt(c.get("stress_avg"), ".0f")}/100

TRAINING THIS WEEK:
- Sessions: {c.get("sessions", 0)} (prior week: {p.get("prior_sessions", "n/a")})
- Total volume: {_fmt(c.get("volume"), ",.0f", " lbs")} (prior week: {_fmt(p.get("prior_volume"), ",.0f", " lbs")})
- ACWR at week end: {data["acwr"] or "n/a"}
- Muscle groups hit: {muscles_str}
{nutrition_section}

ATHLETE CONTEXT:
{ATHLETE_CONTEXT}

Write a 3-4 sentence weekly summary. Identify the most important trend or pattern from this week. Note any correlations you observe between training load and recovery metrics. Be specific and actionable.
Do not include a title or heading — the UI already provides one. Write plain prose only."""


def build_monthly_prompt(data: dict) -> str:
    c = data["current"]
    p = data["prior"]
    d = data.get("derived", {})

    # PR section
    pr_lines = []
    for pr in data.get("prs", []):
        pr_lines.append(
            f"  - {pr['exercise_name']}: {pr['max_weight']} lbs x {pr['reps']}"
        )
    pr_section = "\n".join(pr_lines) if pr_lines else "  No heavy sets logged"

    # Volume comparison
    vol_delta = ""
    if c.get("total_volume") and p.get("total_volume"):
        pct = _pct_delta(c["total_volume"], p["total_volume"])
        if pct is not None:
            vol_delta = f" ({pct:+.0f}% vs prior 30 days)"

    anomaly_section = ""
    if d:
        anomaly_section = f"""
ANOMALY SUMMARY:
- HRV anomaly days: {d.get("hrv_anomaly_days", 0)}
- Sleep anomaly days: {d.get("sleep_anomaly_days", 0)}
- Stress anomaly days: {d.get("stress_anomaly_days", 0)}
- Avg HRV delta from baseline: {_fmt(d.get("avg_hrv_delta"), "+.1f", "%")}
- Avg ACWR: {_fmt(d.get("avg_acwr"), ".2f")}"""

    nutrition_section = ""
    if c.get("crono_days") and c["crono_days"] > 0:
        nutrition_section = f"""
NUTRITION ({c["crono_days"]} days logged of 30):
- Avg calories: {_fmt(c.get("cal_avg"), ",.0f")} (weekly avg target ~2,884)
- Avg protein: {_fmt(c.get("protein_avg"), ".0f", "g")} (target 280g)"""

    return f"""You are a personal health analyst for Blake. Here is his 30-day health report ({data["month_start"]} to {data["month_end"]}):

30-DAY AVERAGES:
- HRV: {_fmt(c.get("hrv_avg"), ".1f", " ms")} (prior 30d: {_fmt(p.get("hrv_avg"), ".1f", " ms")})
- Resting HR: {_fmt(c.get("rhr_avg"), ".1f", " bpm")} (prior 30d: {_fmt(p.get("rhr_avg"), ".1f", " bpm")})
- Sleep: {_fmt(c.get("sleep_avg"), ".1f", " hrs")} avg (prior 30d: {_fmt(p.get("sleep_avg"), ".1f", " hrs")})
- Deep sleep: {_fmt(c.get("deep_avg"), ".0f", " min")} avg
- Body battery EOD: {_fmt(c.get("bb_avg"), ".0f")} (prior 30d: {_fmt(p.get("bb_avg"), ".0f")})
- Stress: {_fmt(c.get("stress_avg"), ".0f")}/100
- Steps: {_fmt(c.get("steps_avg"), ",.0f")} avg ({c.get("high_step_days", 0)} days >15k)

TRAINING:
- Sessions: {c.get("sessions", 0)} (prior 30d: {p.get("sessions", "n/a")})
- Total volume: {_fmt(c.get("total_volume"), ",.0f", " lbs")}{vol_delta}
- Avg session volume: {_fmt(c.get("avg_session_volume"), ",.0f", " lbs")}
{anomaly_section}

TOP LIFTS THIS MONTH:
{pr_section}
{nutrition_section}

ATHLETE CONTEXT:
{ATHLETE_CONTEXT}

Write a 5-6 sentence monthly analysis. Identify the 2-3 most significant patterns or findings. Reference specific numbers. Note what's working and what might need attention. Include one forward-looking observation about what to watch in the coming month. Be analytical, not cheerleader-y.
Do not include a title or heading — the UI already provides one. Write plain prose only."""


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def generate_insight(
    conn, target_date: date, insight_type: str, force: bool = False
) -> bool:
    # For daily insights, target_date is the *report* date (today).
    # The data comes from the previous day (yesterday).
    if insight_type == "daily":
        data_date = target_date - timedelta(days=1)
    else:
        data_date = target_date

    if not force and insight_exists(conn, target_date, insight_type):
        print(f"  Insight already exists for {target_date} ({insight_type}), skipping.")
        return False

    # Fetch data
    if insight_type == "daily":
        data = fetch_daily_data(conn, data_date)
        if not data:
            print(f"  No data for {data_date}, skipping daily insight.")
            return False
        prompt = build_daily_prompt(data)

    elif insight_type == "weekly":
        data = fetch_weekly_data(conn, target_date)
        if not data:
            print(f"  No data for week ending {target_date}, skipping weekly insight.")
            return False
        prompt = build_weekly_prompt(data)

    elif insight_type == "monthly":
        data = fetch_monthly_data(conn, target_date)
        if not data:
            print(
                f"  No data for month ending {target_date}, skipping monthly insight."
            )
            return False
        prompt = build_monthly_prompt(data)

    else:
        print(f"  Unknown insight type: {insight_type}")
        return False

    # Call Anthropic API
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    max_tokens = MAX_TOKENS[insight_type]

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        insight_text = message.content[0].text
        tokens_used = message.usage.input_tokens + message.usage.output_tokens
    except Exception as e:
        print(f"  API error for {target_date} ({insight_type}): {e}")
        return False

    # Store result
    store_insight(
        conn,
        target_date,
        insight_type,
        insight_text,
        MODEL,
        PROMPT_VERSIONS[insight_type],
        tokens_used,
    )
    print(
        f"  Generated {insight_type} insight for {target_date} ({tokens_used} tokens)"
    )
    return True


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


def backfill(conn, since: date, insight_type: str | None, delay: float, force: bool):
    today = local_today()

    if insight_type is None or insight_type == "daily":
        print(f"Backfilling daily insights from {since}...")
        d = since
        while d < today:
            # For daily, d is the data date; report date is d+1
            report_date = d + timedelta(days=1)
            if has_sufficient_data(conn, d):
                generate_insight(conn, report_date, "daily", force=force)
                time.sleep(delay)
            else:
                print(f"  {d}: insufficient data, skipping")
            d += timedelta(days=1)

    if insight_type is None or insight_type == "weekly":
        print(f"Backfilling weekly insights from {since}...")
        # Find first Sunday on or after since
        d = since
        while d.weekday() != 6:  # Sunday
            d += timedelta(days=1)
        while d < today:
            generate_insight(conn, d, "weekly", force=force)
            time.sleep(delay)
            d += timedelta(days=7)

    if insight_type is None or insight_type == "monthly":
        print(f"Backfilling monthly insights from {since}...")
        # Last day of each month
        d = since.replace(day=1)
        while d < today:
            last_day = d.replace(day=monthrange(d.year, d.month)[1])
            if last_day < today:
                generate_insight(conn, last_day, "monthly", force=force)
                time.sleep(delay)
            # Next month
            if d.month == 12:
                d = d.replace(year=d.year + 1, month=1)
            else:
                d = d.replace(month=d.month + 1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Generate AI health insights")
    parser.add_argument("--daily", action="store_true", help="Generate daily insight")
    parser.add_argument("--weekly", action="store_true", help="Generate weekly insight")
    parser.add_argument(
        "--monthly", action="store_true", help="Generate monthly insight"
    )
    parser.add_argument(
        "--date", type=str, help="Target date (YYYY-MM-DD), default today for daily, yesterday for weekly/monthly"
    )
    parser.add_argument(
        "--force", action="store_true", help="Regenerate even if exists"
    )
    parser.add_argument(
        "--backfill", action="store_true", help="Backfill historical insights"
    )
    parser.add_argument("--since", type=str, help="Backfill start date (YYYY-MM-DD)")
    parser.add_argument(
        "--type",
        type=str,
        choices=["daily", "weekly", "monthly"],
        help="Backfill type filter",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds between API calls during backfill (default 1.0)",
    )
    args = parser.parse_args()

    conn = get_db()

    try:
        # Ensure insights table exists
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS insights (
                id              SERIAL PRIMARY KEY,
                date            DATE NOT NULL,
                type            TEXT NOT NULL CHECK (type IN ('daily', 'weekly', 'monthly', 'correlation')),
                content         TEXT NOT NULL,
                model           TEXT,
                prompt_version  TEXT,
                tokens_used     INT,
                created_at      TIMESTAMPTZ DEFAULT now()
            )
        """)
        # Migrate: replace non-unique index with unique constraint
        # First, deduplicate any existing rows (keep the latest)
        cur.execute("""
            DELETE FROM insights a USING insights b
            WHERE a.id < b.id AND a.date = b.date AND a.type = b.type
        """)
        cur.execute("DROP INDEX IF EXISTS idx_insights_date_type")
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_insights_date_type ON insights(date, type)"
        )
        conn.commit()

        if args.backfill:
            if not args.since:
                print("--backfill requires --since YYYY-MM-DD")
                sys.exit(1)
            since = datetime.strptime(args.since, "%Y-%m-%d").date()
            backfill(conn, since, args.type, args.delay, args.force)
            return

        today = local_today()
        target = (
            datetime.strptime(args.date, "%Y-%m-%d").date()
            if args.date
            else today
        )

        explicit = args.daily or args.weekly or args.monthly

        if not explicit:
            # Auto mode: generate whatever is due
            # Daily: target is today (report date); data is fetched for yesterday
            generate_insight(conn, target, "daily", force=args.force)
            # Weekly on Mondays (for the week ending yesterday/Sunday)
            if today.weekday() == 0:
                yesterday = today - timedelta(days=1)
                generate_insight(conn, yesterday, "weekly", force=args.force)
            # Monthly on the 1st
            if today.day == 1:
                last_month_end = today - timedelta(days=1)
                generate_insight(conn, last_month_end, "monthly", force=args.force)
        else:
            if args.daily:
                generate_insight(conn, target, "daily", force=args.force)
            if args.weekly:
                generate_insight(conn, target, "weekly", force=args.force)
            if args.monthly:
                generate_insight(conn, target, "monthly", force=args.force)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
