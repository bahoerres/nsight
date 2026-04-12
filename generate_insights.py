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
    python generate_insights.py --rolling    # generate rolling weekly_current + monthly_current
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
PROMPT_VERSIONS = {
    "daily": "daily-v3",
    "weekly": "weekly-v3",
    "monthly": "monthly-v3",
    "weekly_current": "rolling-weekly-v1",
    "monthly_current": "rolling-monthly-v1",
}
MAX_TOKENS = {"daily": 640, "weekly": 1024, "monthly": 1280, "weekly_current": 1024, "monthly_current": 1280}

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

    return {
        "day": row,
        "baselines": dict(baselines),
        "session_detail": fetch_session_detail(conn, target_date),
    }


def fetch_weekly_data(conn, week_end: date) -> dict | None:
    # Training week is Fri-Thu. Anchor week_end to the most recent Thursday.
    days_past_thu = (week_end.weekday() - 3) % 7
    week_end = week_end - timedelta(days=days_past_thu)
    week_start = week_end - timedelta(days=6)  # Friday
    return _fetch_weekly_data_raw(conn, week_start, week_end)


def _fetch_weekly_data_raw(conn, week_start: date, week_end: date) -> dict | None:
    """Core weekly data fetch for an arbitrary 7-day window."""
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
        "exercise_data": fetch_weekly_exercise_data(conn, week_start, week_end),
    }


def fetch_rolling_weekly_data(conn, as_of_date: date) -> dict | None:
    """Rolling last-7-days window ending on as_of_date (no Fri-Thu snapping)."""
    # Call the core weekly query directly with the raw date range.
    # fetch_weekly_data snaps to Fri-Thu; we want a literal last-7-days window.
    return _fetch_weekly_data_raw(conn, week_start=as_of_date - timedelta(days=6), week_end=as_of_date)


def fetch_rolling_monthly_data(conn, as_of_date: date) -> dict | None:
    """Rolling last-30-days window ending on as_of_date."""
    return fetch_monthly_data(conn, as_of_date)


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

    exercise_data = fetch_monthly_exercise_data(conn, month_start, month_end)

    return {
        "month_start": month_start,
        "month_end": month_end,
        "current": dict(current),
        "prior": dict(prior),
        "derived": dict(derived) if derived else {},
        "exercise_data": exercise_data,
    }


# ---------------------------------------------------------------------------
# Training detail helpers
# ---------------------------------------------------------------------------


def fetch_session_detail(conn, target_date: date) -> list[dict]:
    """
    For a training day, return the top working set per exercise with PR detection.
    Returns list of dicts: exercise_name, muscle_group, weight_lbs, reps, rpe, is_pr, prev_best_weight
    """
    cur = conn.cursor()

    # Top set per exercise (heaviest weight, tiebreak by reps) — working sets only
    cur.execute(
        """WITH ranked AS (
               SELECT exercise_name, muscle_group, weight_lbs, reps, rpe,
                      ROW_NUMBER() OVER (
                          PARTITION BY exercise_name
                          ORDER BY weight_lbs DESC, reps DESC
                      ) as rn
               FROM hevy_sets
               WHERE date = %s AND weight_lbs IS NOT NULL AND reps > 0
                 AND COALESCE(set_type, 'normal') != 'warmup'
           )
           SELECT exercise_name, muscle_group, weight_lbs, reps, rpe
           FROM ranked WHERE rn = 1
           ORDER BY weight_lbs DESC""",
        (target_date,),
    )
    top_sets = [dict(r) for r in cur.fetchall()]
    if not top_sets:
        return []

    # PR detection: for each exercise, find prior best at same or higher reps (working sets only)
    for s in top_sets:
        # Check if this is an established lift (3+ prior sessions)
        cur.execute(
            """SELECT COUNT(DISTINCT date) as prior_sessions
               FROM hevy_sets
               WHERE exercise_name = %s AND date < %s
                 AND COALESCE(set_type, 'normal') != 'warmup'
                 AND weight_lbs IS NOT NULL""",
            (s["exercise_name"], target_date),
        )
        hist = cur.fetchone()
        s["prior_sessions"] = hist["prior_sessions"] if hist else 0

        cur.execute(
            """SELECT MAX(weight_lbs) as prev_best
               FROM hevy_sets
               WHERE exercise_name = %s
                 AND date < %s
                 AND reps >= %s
                 AND weight_lbs IS NOT NULL
                 AND COALESCE(set_type, 'normal') != 'warmup'""",
            (s["exercise_name"], target_date, s["reps"]),
        )
        row = cur.fetchone()
        prev = float(row["prev_best"]) if row and row["prev_best"] else None
        s["prev_best_weight"] = prev
        s["is_pr"] = (
            prev is not None
            and float(s["weight_lbs"]) > prev
            and s["prior_sessions"] >= 3
        )

    return top_sets


def fetch_weekly_exercise_data(conn, week_start: date, week_end: date) -> dict:
    """
    Per-exercise progression and muscle group volume breakdown for weekly insights.
    Returns: {exercises: [...], muscle_volume: {...}, prs: [...]}
    """
    cur = conn.cursor()

    # Best working set per exercise this week (exclude warmups)
    cur.execute(
        """WITH ranked AS (
               SELECT exercise_name, muscle_group, weight_lbs, reps, date,
                      ROW_NUMBER() OVER (
                          PARTITION BY exercise_name
                          ORDER BY weight_lbs DESC, reps DESC
                      ) as rn
               FROM hevy_sets
               WHERE date BETWEEN %s AND %s AND weight_lbs IS NOT NULL AND reps > 0
                 AND COALESCE(set_type, 'normal') != 'warmup'
           )
           SELECT exercise_name, muscle_group, weight_lbs, reps, date
           FROM ranked WHERE rn = 1
           ORDER BY weight_lbs DESC""",
        (week_start, week_end),
    )
    this_week = [dict(r) for r in cur.fetchall()]

    # For each exercise, find best set from the prior 4 weeks
    prior_start = week_start - timedelta(days=28)
    prior_end = week_start - timedelta(days=1)
    exercises = []
    prs = []

    for ex in this_week:
        cur.execute(
            """SELECT MAX(weight_lbs) as prev_best
               FROM hevy_sets
               WHERE exercise_name = %s AND date BETWEEN %s AND %s
                 AND weight_lbs IS NOT NULL AND reps > 0
                 AND COALESCE(set_type, 'normal') != 'warmup'""",
            (ex["exercise_name"], prior_start, prior_end),
        )
        prev = cur.fetchone()
        prev_weight = float(prev["prev_best"]) if prev and prev["prev_best"] else None
        ex["prev_4wk_best"] = prev_weight

        # Check all-time PR (only for established lifts with 3+ prior sessions)
        cur.execute(
            """SELECT COUNT(DISTINCT date) as prior_sessions
               FROM hevy_sets
               WHERE exercise_name = %s AND date < %s
                 AND COALESCE(set_type, 'normal') != 'warmup'
                 AND weight_lbs IS NOT NULL""",
            (ex["exercise_name"], week_start),
        )
        hist = cur.fetchone()
        prior_sessions = hist["prior_sessions"] if hist else 0

        if prior_sessions >= 3:
            cur.execute(
                """SELECT MAX(weight_lbs) as all_time_best
                   FROM hevy_sets
                   WHERE exercise_name = %s AND date < %s
                     AND reps >= %s AND weight_lbs IS NOT NULL
                     AND COALESCE(set_type, 'normal') != 'warmup'""",
                (ex["exercise_name"], week_start, ex["reps"]),
            )
            at_row = cur.fetchone()
            at_best = (
                float(at_row["all_time_best"])
                if at_row and at_row["all_time_best"]
                else None
            )
            if at_best is None or float(ex["weight_lbs"]) > at_best:
                prs.append(ex)

        exercises.append(ex)

    # Volume by muscle group this week vs last week (exclude warmups)
    cur.execute(
        """SELECT muscle_group,
                  ROUND(SUM(weight_lbs * reps)::numeric, 0) as volume
           FROM hevy_sets
           WHERE date BETWEEN %s AND %s AND weight_lbs IS NOT NULL AND reps > 0
             AND COALESCE(set_type, 'normal') != 'warmup'
           GROUP BY muscle_group
           ORDER BY volume DESC""",
        (week_start, week_end),
    )
    this_vol = {r["muscle_group"]: int(r["volume"]) for r in cur.fetchall()}

    cur.execute(
        """SELECT muscle_group,
                  ROUND(SUM(weight_lbs * reps)::numeric, 0) as volume
           FROM hevy_sets
           WHERE date BETWEEN %s AND %s AND weight_lbs IS NOT NULL AND reps > 0
             AND COALESCE(set_type, 'normal') != 'warmup'
           GROUP BY muscle_group
           ORDER BY volume DESC""",
        (prior_end - timedelta(days=6), prior_end),
    )
    prev_vol = {r["muscle_group"]: int(r["volume"]) for r in cur.fetchall()}

    return {
        "exercises": exercises,
        "muscle_volume": this_vol,
        "prev_muscle_volume": prev_vol,
        "prs": prs,
    }


def fetch_monthly_exercise_data(conn, month_start: date, month_end: date) -> dict:
    """
    Per-exercise strength curves (best set per week) and true PR detection for monthly insights.
    Returns: {progressions: {exercise: [{week, weight, reps}, ...]}, prs: [...]}
    """
    cur = conn.cursor()

    # Best working set per exercise per week across the month (exclude warmups)
    cur.execute(
        """WITH weekly_best AS (
               SELECT exercise_name, muscle_group, weight_lbs, reps, date,
                      date_trunc('week', date::timestamp)::date as week_start,
                      ROW_NUMBER() OVER (
                          PARTITION BY exercise_name, date_trunc('week', date::timestamp)
                          ORDER BY weight_lbs DESC, reps DESC
                      ) as rn
               FROM hevy_sets
               WHERE date BETWEEN %s AND %s AND weight_lbs IS NOT NULL AND reps > 0
                 AND COALESCE(set_type, 'normal') != 'warmup'
           )
           SELECT exercise_name, muscle_group, weight_lbs, reps, week_start
           FROM weekly_best WHERE rn = 1
           ORDER BY exercise_name, week_start""",
        (month_start, month_end),
    )
    rows = [dict(r) for r in cur.fetchall()]

    # Group by exercise
    progressions = {}
    for r in rows:
        name = r["exercise_name"]
        if name not in progressions:
            progressions[name] = {"muscle_group": r["muscle_group"], "weeks": []}
        progressions[name]["weeks"].append(
            {
                "week_start": r["week_start"],
                "weight": float(r["weight_lbs"]),
                "reps": r["reps"],
            }
        )

    # Filter to exercises with 2+ weeks of data (can actually show a trend)
    progressions = {k: v for k, v in progressions.items() if len(v["weeks"]) >= 2}

    # True PR detection: established lifts (3+ prior sessions) where month's best beat all prior
    cur.execute(
        """WITH month_best AS (
               SELECT exercise_name, weight_lbs, reps,
                      ROW_NUMBER() OVER (
                          PARTITION BY exercise_name
                          ORDER BY weight_lbs DESC, reps DESC
                      ) as rn
               FROM hevy_sets
               WHERE date BETWEEN %s AND %s AND weight_lbs IS NOT NULL AND reps > 0
                 AND COALESCE(set_type, 'normal') != 'warmup'
           ),
           top AS (
               SELECT exercise_name, weight_lbs, reps FROM month_best WHERE rn = 1
           ),
           history AS (
               SELECT exercise_name, COUNT(DISTINCT date) as prior_sessions
               FROM hevy_sets
               WHERE date < %s AND weight_lbs IS NOT NULL
                 AND COALESCE(set_type, 'normal') != 'warmup'
               GROUP BY exercise_name
           ),
           prior AS (
               SELECT t.exercise_name, MAX(h.weight_lbs) as prior_best
               FROM top t
               LEFT JOIN hevy_sets h ON h.exercise_name = t.exercise_name
                   AND h.date < %s AND h.reps >= t.reps AND h.weight_lbs IS NOT NULL
                   AND COALESCE(h.set_type, 'normal') != 'warmup'
               GROUP BY t.exercise_name
           )
           SELECT t.exercise_name, t.weight_lbs, t.reps, p.prior_best
           FROM top t
           JOIN history hi ON hi.exercise_name = t.exercise_name AND hi.prior_sessions >= 3
           LEFT JOIN prior p ON p.exercise_name = t.exercise_name
           WHERE p.prior_best IS NULL OR t.weight_lbs > p.prior_best
           ORDER BY t.weight_lbs DESC""",
        (month_start, month_end, month_start, month_start),
    )
    prs = [dict(r) for r in cur.fetchall()]

    # Muscle group frequency (how many sessions hit each group)
    cur.execute(
        """SELECT muscle_group, COUNT(DISTINCT date) as session_days
           FROM hevy_sets
           WHERE date BETWEEN %s AND %s AND muscle_group != 'other'
             AND COALESCE(set_type, 'normal') != 'warmup'
           GROUP BY muscle_group
           ORDER BY session_days DESC""",
        (month_start, month_end),
    )
    muscle_freq = {r["muscle_group"]: r["session_days"] for r in cur.fetchall()}

    return {
        "progressions": progressions,
        "prs": prs,
        "muscle_frequency": muscle_freq,
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

        # Add per-exercise top sets
        session_detail = data.get("session_detail", [])
        if session_detail:
            training_section += "\n- Top working sets:"
            for s in session_detail:
                pr_tag = " *** ALL-TIME PR ***" if s["is_pr"] else ""
                prev = ""
                if s.get("prev_best_weight") and not s["is_pr"]:
                    prev = f" (prev best: {s['prev_best_weight']:.0f} lbs)"
                rpe_str = f" @RPE {s['rpe']}" if s.get("rpe") else ""
                training_section += (
                    f"\n    {s['exercise_name']}: {float(s['weight_lbs']):.0f} lbs x {s['reps']}"
                    f"{rpe_str}{prev}{pr_tag}"
                )

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
    carb_target = (
        "400g carbs / 3,170 kcal" if is_high_carb else "300g carbs / 2,770 kcal"
    )
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

Write 2-3 sentences of plain-English insight about {day_name}. Be specific about what the numbers mean for this athlete, not for a generic person. If he trained, lead with exercise-level detail — PRs, how working weights compare to recent history, which movements progressed. Total volume is context, not the headline. Be direct and useful, not generic wellness advice.

SIGNAL FILTERING RULES (follow strictly):
- Only flag a metric if it is clearly outside the athlete's normal variance (±1 SD or violates a rule from the athlete context above). If everything is within range, say so briefly and move on — do not fill space with negligible deviations.
- If a deviation is small enough to be called "negligible" or "minor", do not mention it at all. Do not flag something and then immediately dismiss it.
- Body battery is unreliable for this athlete due to low HRV baseline distorting Garmin's algorithm. Mention it only as minor supporting context, never as a primary finding or concern.
- HRV: Do not characterize single-day changes under 3ms or 15% as meaningful. Only comment on HRV if the absolute change exceeds 4-5ms or there is a multi-day directional trend.
- Nutrition: hitting within ~5% of a macro target is on-target. Do not itemize small overages/underages. Only flag if a macro is off by >15% or a pattern of misses is forming.
- Fiber, sodium, and micronutrient targets: only mention if meaningfully outside range AND likely to cause a functional issue the athlete would notice. "Above target" alone is not worth a sentence.
- Prefer saying "everything looks solid" over manufacturing observations from noise.

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

    # Build exercise detail section
    ex_data = data.get("exercise_data", {})
    exercise_section = ""
    if ex_data.get("exercises"):
        exercise_section = (
            "\n\nEXERCISE PROGRESSION (this week's best set vs prior 4 weeks):"
        )
        for ex in ex_data["exercises"]:
            prev = ex.get("prev_4wk_best")
            delta = ""
            if prev:
                diff = float(ex["weight_lbs"]) - prev
                if diff > 0:
                    delta = f" (up {diff:.0f} lbs)"
                elif diff < 0:
                    delta = f" (down {abs(diff):.0f} lbs)"
                else:
                    delta = " (same)"
            elif prev is None:
                delta = " (new exercise this cycle)"
            exercise_section += f"\n- {ex['exercise_name']}: {float(ex['weight_lbs']):.0f} lbs x {ex['reps']}{delta}"

    pr_section = ""
    if ex_data.get("prs"):
        pr_section = "\n\nNEW ALL-TIME PRs THIS WEEK:"
        for pr in ex_data["prs"]:
            pr_section += f"\n- {pr['exercise_name']}: {float(pr['weight_lbs']):.0f} lbs x {pr['reps']}"

    muscle_vol_section = ""
    if ex_data.get("muscle_volume"):
        muscle_vol_section = "\n\nVOLUME BY MUSCLE GROUP:"
        prev_vol = ex_data.get("prev_muscle_volume", {})
        for mg, vol in ex_data["muscle_volume"].items():
            pv = prev_vol.get(mg)
            delta = ""
            if pv and pv > 0:
                pct = ((vol - pv) / pv) * 100
                delta = f" ({pct:+.0f}% vs last week)"
            elif pv is None:
                delta = " (not hit last week)"
            muscle_vol_section += f"\n- {mg}: {vol:,.0f} lbs{delta}"

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
{exercise_section}{pr_section}{muscle_vol_section}
{nutrition_section}

ATHLETE CONTEXT:
{ATHLETE_CONTEXT}

Write a 3-4 sentence weekly summary. Lead with the most important training story — exercise progression, PRs hit, any stalls or regressions on key lifts, and muscle group coverage. Total volume and ACWR provide context. Then address nutrition compliance. Be specific and actionable.

SIGNAL FILTERING RULES (follow strictly):
- Body battery is unreliable for this athlete due to low HRV baseline distorting Garmin's algorithm. Do not use body battery as a primary recovery indicator. It may appear as minor supporting context only, never as a headline finding or basis for a recommendation.
- HRV: Do not characterize weekly average changes under 2ms or 15% as trends, declines, or concerns. At an 18-24ms baseline, small absolute movements are normal variance, not signals.
- Do not build narrative arcs around metrics that are within normal variance. If recovery metrics are stable, say "recovery held steady" and move on — do not speculate about what might happen if they drift.
- ACWR is the primary training load indicator. Resting HR trend direction is a useful recovery signal. Sleep architecture (deep sleep minutes) matters more than total sleep hours. Prioritize these over body battery and raw HRV values.
- If there is nothing genuinely concerning, say the week was clean. Do not invent watch-items from noise.

Do not include a title or heading — the UI already provides one. Write plain prose only."""


def build_rolling_weekly_prompt(data: dict) -> str:
    """Like build_weekly_prompt but framed as a rolling 7-day snapshot, not a completed week."""
    base = build_weekly_prompt(data)
    # Replace the fixed-week framing with rolling-window framing
    base = base.replace(
        f"Here is his health summary for the week of {data['week_start']} to {data['week_end']}:",
        f"Here is a rolling 7-day snapshot as of {data['week_end']} (covering {data['week_start']} through {data['week_end']}). "
        "This is NOT a completed training week — it's a live window into the last 7 days.",
    )
    base = base.replace(
        "Write a 3-4 sentence weekly summary. Lead with the most important training story",
        "Write a 3-4 sentence snapshot of where things stand right now. Lead with the most important training story",
    )
    base = base.replace(
        "Be specific and actionable.",
        "Frame as current status, not a retrospective. Be specific and actionable.",
    )
    return base


def build_rolling_monthly_prompt(data: dict) -> str:
    """Like build_monthly_prompt but framed as a rolling 30-day snapshot."""
    base = build_monthly_prompt(data)
    base = base.replace(
        f"Here is his 30-day health report ({data['month_start']} to {data['month_end']}):",
        f"Here is a rolling 30-day snapshot as of {data['month_end']} (covering {data['month_start']} through {data['month_end']}). "
        "This is NOT a completed calendar month — it's a live window into the last 30 days.",
    )
    base = base.replace(
        "Write a 5-6 sentence monthly analysis. Lead with the strength story",
        "Write a 5-6 sentence snapshot of where things stand over the last 30 days. Lead with the strength story",
    )
    base = base.replace(
        "Be analytical, not cheerleader-y.",
        "Frame as current status, not a retrospective. Be analytical, not cheerleader-y.",
    )
    return base


def build_monthly_prompt(data: dict) -> str:
    c = data["current"]
    p = data["prior"]
    d = data.get("derived", {})
    ex_data = data.get("exercise_data", {})

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

    # Exercise progression section (best set per week, showing trend)
    progression_section = ""
    if ex_data.get("progressions"):
        progression_section = "\n\nEXERCISE PROGRESSION (best working set per week):"
        for name, info in ex_data["progressions"].items():
            weeks = info["weeks"]
            trend_parts = []
            for w in weeks:
                trend_parts.append(f"{w['weight']:.0f}x{w['reps']}")
            direction = ""
            if len(weeks) >= 2:
                first_w = weeks[0]["weight"]
                last_w = weeks[-1]["weight"]
                if last_w > first_w:
                    direction = " ↑"
                elif last_w < first_w:
                    direction = " ↓"
                else:
                    direction = " →"
            progression_section += f"\n- {name} ({info['muscle_group']}): {' → '.join(trend_parts)}{direction}"

    # True PRs (beat all prior history)
    pr_section = ""
    if ex_data.get("prs"):
        pr_section = "\n\nALL-TIME PRs THIS MONTH:"
        for pr in ex_data["prs"]:
            prev = ""
            if pr.get("prior_best"):
                prev = f" (prev best: {float(pr['prior_best']):.0f} lbs)"
            pr_section += f"\n- {pr['exercise_name']}: {float(pr['weight_lbs']):.0f} lbs x {pr['reps']}{prev}"
    else:
        pr_section = "\n\nALL-TIME PRs THIS MONTH:\n  None this period"

    # Muscle group frequency
    freq_section = ""
    if ex_data.get("muscle_frequency"):
        freq_section = "\n\nMUSCLE GROUP FREQUENCY (training days per group):"
        for mg, days in ex_data["muscle_frequency"].items():
            freq_section += f"\n- {mg}: {days} days"

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
{progression_section}{pr_section}{freq_section}
{nutrition_section}

ATHLETE CONTEXT:
{ATHLETE_CONTEXT}

Write a 5-6 sentence monthly analysis. Lead with the strength story — which lifts progressed, which stalled, any all-time PRs, and how the muscle group rotation looked. Then address training volume trajectory, nutrition compliance, and one forward-looking observation. Reference specific numbers. Be analytical, not cheerleader-y.

SIGNAL FILTERING RULES (follow strictly):
- Body battery is unreliable for this athlete due to low HRV baseline distorting Garmin's algorithm. Do not use body battery changes as evidence that recovery isn't keeping pace with training. It may appear as minor context only, never as a headline finding.
- HRV: Do not characterize month-over-month changes under 2ms as meaningful trends. A shift from 18.6 to 17.7ms is within normal variance at this baseline — do not frame it as "moving in the wrong direction" or build projections around it. Only flag HRV if the monthly average drops below 15ms or shows a clear multi-week directional trend of 3ms+.
- Prioritize actionable signals: training volume trajectory, ACWR trend, nutrition logging consistency, macro compliance on logged days, sleep architecture, resting HR trend. These are reliable and controllable.
- When sleep improves but Garmin-derived recovery scores don't follow, the explanation is Garmin's algorithm miscalibrating on low HRV — not that "the body is absorbing load." Do not construct recovery narratives from body battery or Garmin recovery scores.
- If nutrition logging is sparse, note the gap and what it means for data confidence, but do not extrapolate deficits from partial data.
- "What needs attention" should only include genuinely actionable items, not metrics drifting within their normal range.

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

    elif insight_type == "weekly_current":
        data = fetch_rolling_weekly_data(conn, target_date)
        if not data:
            print(f"  No data for rolling week ending {target_date}, skipping.")
            return False
        prompt = build_rolling_weekly_prompt(data)

    elif insight_type == "monthly_current":
        data = fetch_rolling_monthly_data(conn, target_date)
        if not data:
            print(f"  No data for rolling month ending {target_date}, skipping.")
            return False
        prompt = build_rolling_monthly_prompt(data)

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
        # Find first Thursday on or after since (Fri-Thu training week ends Thursday)
        d = since
        while d.weekday() != 3:  # Thursday
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
        "--date",
        type=str,
        help="Target date (YYYY-MM-DD), default today for daily, yesterday for weekly/monthly",
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
    parser.add_argument(
        "--rolling",
        action="store_true",
        help="Generate rolling weekly_current and monthly_current insights",
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
                type            TEXT NOT NULL CHECK (type IN ('daily', 'weekly', 'monthly', 'correlation', 'weekly_current', 'monthly_current')),
                content         TEXT NOT NULL,
                model           TEXT,
                prompt_version  TEXT,
                tokens_used     INT,
                created_at      TIMESTAMPTZ DEFAULT now()
            )
        """)
        # Migrate: add rolling insight types to CHECK constraint
        cur.execute("""
            ALTER TABLE insights DROP CONSTRAINT IF EXISTS insights_type_check;
            ALTER TABLE insights ADD CONSTRAINT insights_type_check
                CHECK (type IN ('daily', 'weekly', 'monthly', 'correlation', 'sleep', 'recovery', 'weekly_current', 'monthly_current'));
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
        target = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else today

        explicit = args.daily or args.weekly or args.monthly or args.rolling

        if args.rolling:
            # Manual rolling generation
            generate_insight(conn, target, "weekly_current", force=True)
            generate_insight(conn, target, "monthly_current", force=True)

        if not explicit:
            # Auto mode: generate whatever is due
            # Daily: target is today (report date); data is fetched for yesterday
            generate_insight(conn, target, "daily", force=args.force)
            # Weekly on Fridays (for the Fri-Thu week ending yesterday/Thursday)
            if today.weekday() == 4:  # Friday
                yesterday = today - timedelta(days=1)
                generate_insight(conn, yesterday, "weekly", force=args.force)
            # Monthly on the 1st
            if today.day == 1:
                last_month_end = today - timedelta(days=1)
                generate_insight(conn, last_month_end, "monthly", force=args.force)
            # Rolling current insights — always regenerate nightly
            generate_insight(conn, target, "weekly_current", force=True)
            generate_insight(conn, target, "monthly_current", force=True)
        elif not args.rolling:
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
