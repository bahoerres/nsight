#!/usr/bin/env python3
"""
ingest_hevy.py
Pulls workout data from Hevy API and upserts into daily_log + hevy_sets.

Usage:
    python ingest_hevy.py              # last 30 days
    python ingest_hevy.py --days 90    # last 90 days
    python ingest_hevy.py --since 2025-01-01
    python ingest_hevy.py --all        # full history
"""

import os
import sys
import logging
import argparse
from datetime import date, datetime, timedelta
from collections import defaultdict
from dotenv import load_dotenv

from tz import today as local_today

import requests
import psycopg2
from psycopg2.extras import execute_values

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

HEVY_BASE = "https://api.hevyapp.com/v1"


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


def get_db():
    return psycopg2.connect(os.environ["DATABASE_URL"])


# ---------------------------------------------------------------------------
# Hevy API
# ---------------------------------------------------------------------------


def get_headers():
    return {"api-key": os.environ["HEVY_API_KEY"], "Content-Type": "application/json"}


def get_workouts_page(page: int, page_size: int = 10) -> dict:
    r = requests.get(
        f"{HEVY_BASE}/workouts",
        headers=get_headers(),
        params={"page": page, "pageSize": page_size},
    )
    r.raise_for_status()
    return r.json()


def get_all_workouts(since: date = None) -> list[dict]:
    """Paginate through all workouts, optionally filtering by date."""
    workouts = []
    page = 1
    while True:
        data = get_workouts_page(page)
        batch = data.get("workouts", [])
        if not batch:
            break

        for w in batch:
            # Hevy returns ISO timestamps like "2025-03-17T14:23:00Z"
            start_str = w.get("start_time") or w.get("created_at", "")
            if not start_str:
                continue
            workout_date = datetime.fromisoformat(
                start_str.replace("Z", "+00:00")
            ).date()

            if since and workout_date < since:
                log.info(f"Reached cutoff date {since}, stopping pagination")
                return workouts

            workouts.append({**w, "_date": workout_date})

        log.info(f"Page {page}: got {len(batch)} workouts")

        # check if there are more pages
        total = data.get("page_count", 1)
        if page >= total:
            break
        page += 1

    return workouts


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


# ---------------------------------------------------------------------------
# Weight conversion
# ---------------------------------------------------------------------------


def kg_to_lbs(kg):
    if kg is None:
        return None
    return round(kg * 2.20462, 1)


# ---------------------------------------------------------------------------
# Muscle group mapping
# Hevy uses its own exercise names — map to broad groups for ACWR calc
# ---------------------------------------------------------------------------

MUSCLE_MAP = {
    # chest
    "Legend Chess Press": "chest",
    "High Incline Smith Press": "chest",
    "Incline Bench Press (Smith Machine)": "chest",
    "Incline Hammer Press": "chest",
    "Hammer Incline Press": "chest",
    "Butterfly (Pec Deck)": "chest",
    "Downward Fly (Cable)": "chest",
    "Chest Dip": "chest",
    "Chest Dip (Assisted)": "chest",
    "Extreme Pec Stretch": "chest",
    # back
    "Lat Pulldown (Cable)": "back",
    "Lat Pulldown (Machine)": "back",
    "Lat Pulldown - Close Grip (Cable)": "back",
    "Bent Over Row (Barbell)": "back",
    "Chest Supported Row": "back",
    "Iso-Lateral Low Row": "back",
    "Single Arm Cable Row": "back",
    "Smith Machine Row": "back",
    "Rack Pull": "back",
    "Back Extension (Hyperextension)": "back",
    "Extreme Lat Stretch": "back",
    # shoulders
    "Seated Shoulder Press (Machine)": "shoulders",
    "Cybex Lateral Raises": "shoulders",
    "Front Raise (Cable)": "shoulders",
    "Single Arm Lateral Raise (Cable)": "shoulders",
    "Rear Delt Reverse Fly (Cable)": "shoulders",
    "Rear Delt Reverse Fly (Machine)": "shoulders",
    "Shrug (Smith Machine)": "shoulders",
    "Extreme Shoulder Stretch": "shoulders",
    # biceps
    "EZ Bar Biceps Curl": "biceps",
    "Cross Body Hammer Curl": "biceps",
    "Seated Curl (Dumbbell)": "biceps",
    "Preacher Curl (Machine)": "biceps",
    # triceps
    "Bench Press (Smith Machine)": "triceps",
    "Skullcrusher (Barbell)": "triceps",
    "Triceps Extension (Cable)": "triceps",
    "Triceps Pushdown": "triceps",
    "Tricep Rope Extension - Single Arm (Cable)": "triceps",
    # legs
    "Hack Squat (Machine)": "legs",
    "Leg Press (Machine)": "legs",
    "Leg Press Horizontal (Machine)": "legs",
    "Pendulum Squat (Machine)": "legs",
    "Sumo Squat (Dumbbell)": "legs",
    "Leg Extension (Machine)": "legs",
    "Lying Leg Curl (Machine)": "legs",
    "Seated Leg Curl (Machine)": "legs",
    "Calf Press (Machine)": "legs",
    "Hip Abduction (Machine)": "legs",
    # core
    "Cable Crunch": "core",
    "Hanging Leg Raise": "core",
    # chest (additional)
    "Arsenal Coastal Fly": "chest",
    "Cable Fly Crossovers": "chest",
    "Chest Fly (Machine)": "chest",
    "Chest Press (Machine)": "chest",
    "Decline Push Up": "chest",
    "Floor Press (Barbell)": "chest",
    "Incline Bench Press (Barbell)": "chest",
    "Incline Bench Press (Dumbbell)": "chest",
    "Incline Chest Press (Machine)": "chest",
    "Incline Hex Press": "chest",
    "Push Up": "chest",
    "Seated Chest Flys (Cable)": "chest",
    "Bench Press (Barbell)": "chest",
    "Bench Press - Close Grip (Barbell)": "chest",
    "Chest Dip (Weighted)": "chest",
    "Wide-Grip Hammer Press": "chest",
    # back (additional)
    "Arsenal T-Bar Row": "back",
    "Atlantis Row": "back",
    "Back Extension (Weighted Hyperextension)": "back",
    "Chin Up (Weighted)": "back",
    "Deadlift (Barbell)": "back",
    "Glute Ham Raise": "back",
    "Iso-Lateral High Row (Machine)": "back",
    "Iso-Lateral Row (Machine)": "back",
    "Meadows Rows (Barbell)": "back",
    "Reverse Hyperextension": "back",
    "Seated Cable Row - Bar Wide Grip": "back",
    "Seated Cable Row - V Grip (Cable)": "back",
    "Seated Row (Machine)": "back",
    "Single Arm Lat Pulldown": "back",
    "Single Arm Lat Pulldown (Cable)": "back",
    "Straight Arm Lat Pulldown (Cable)": "back",
    "T Bar Row": "back",
    "Chest Supported Y Raise (Dumbbell)": "back",
    "Y Raises (Cable)": "back",
    # shoulders (additional)
    "Band Pullaparts": "shoulders",
    "Face Pull": "shoulders",
    "Front Raise (Barbell)": "shoulders",
    "Front Raise (Dumbbell)": "shoulders",
    "Lateral Raise (Cable)": "shoulders",
    "Lateral Raise (Dumbbell)": "shoulders",
    "Lateral Raise (Machine)": "shoulders",
    "Overhead Press (Smith Machine)": "shoulders",
    "Rogers Shoulder Press": "shoulders",
    "Seated Overhead Press (Barbell)": "shoulders",
    "Shoulder Press (Dumbbell)": "shoulders",
    "Shrug (Dumbbell)": "shoulders",
    "Shrug (Machine)": "shoulders",
    "Upright Row (Kettlebell)": "shoulders",
    # biceps (additional)
    "Bicep Curl (Cable)": "biceps",
    "Extreme Biceps Stretch": "biceps",
    "Hammer Curl (Dumbbell)": "biceps",
    "Reverse Curl (Barbell)": "biceps",
    "Seated Incline Curl (Dumbbell)": "biceps",
    "Single Arm Curl (Cable)": "biceps",
    "Spider Curl (Barbell)": "biceps",
    "Spider Curl (Dumbbell)": "biceps",
    # triceps (additional)
    "Extreme Triceps Stretch": "triceps",
    "JM Press": "triceps",
    "Seated Dip Machine": "triceps",
    "Single Arm Triceps Pushdown (Cable)": "triceps",
    "Tate Press": "triceps",
    "Tricep Kickout": "triceps",
    "Triceps Dip (Weighted)": "triceps",
    "Triceps Extension (Barbell)": "triceps",
    "Triceps Rope Pushdown": "triceps",
    # legs (additional)
    "Belt Squat": "legs",
    "Extreme Hamstring Stretch": "legs",
    "Extreme Quad Stretch": "legs",
    "Front Squat": "legs",
    "Hip Adduction (Machine)": "legs",
    "Hip Thrust (Smith Machine)": "legs",
    "Lunge (Dumbbell)": "legs",
    "Pendulum Hip Press": "legs",
    "Rogers Hip Press": "legs",
    "Rogers Squat": "legs",
    "Romanian Deadlift (Barbell)": "legs",
    "Seated Calf Raise": "legs",
    "Single Leg Press (Machine)": "legs",
    "Single-Leg RDL (Landmine)": "legs",
    "Sissy Squat (Weighted)": "legs",
    "Sled Push": "legs",
    "Heavy Sleds": "legs",
    "Squat (Barbell)": "legs",
    "Standing Calf Raise (Smith)": "legs",
    "Walking Lunge": "legs",
    "GHR Reverse Crunch": "legs",
    # core (additional)
    "Ab Wheel": "core",
    "Hanging Knee Raise": "core",
    "Plank": "core",
    "Side Bend (Cable)": "core",
    "Side Plank": "core",
    "Torso Rotation": "core",
}


# ---------------------------------------------------------------------------
# Bodyweight exercise handling
# Hevy sends weight_kg=0 for these; substitute athlete's bodyweight
# For "(Weighted)" variants, Hevy sends only the added weight — add BW on top
# ---------------------------------------------------------------------------

BODYWEIGHT_EXERCISES = {
    "Chin Up",
    "Pull Up",
    "Dip",
    "Chest Dip",
    "Chest Dip (Assisted)",
    "Push Up",
    "Decline Push Up",
    "Muscle Up",
    "Inverted Row",
    "Hanging Knee Raise",
    "Hanging Leg Raise",
    "Glute Ham Raise",
    "GHR Reverse Crunch",
    "Back Extension (Hyperextension)",
    "Reverse Hyperextension",
    "Plank",
    "Side Plank",
    "Ab Wheel",
    "Seated Dip Machine",
}

# Weighted variants: Hevy records only the added load — total = BW + added
WEIGHTED_BODYWEIGHT_EXERCISES = {
    "Chin Up (Weighted)",
    "Chest Dip (Weighted)",
    "Triceps Dip (Weighted)",
    "Sissy Squat (Weighted)",
    "Back Extension (Weighted Hyperextension)",
}


def get_recent_bodyweight(conn) -> float | None:
    """Get most recent body_weight_lbs from daily_log (bridged from Hevy body measurements)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT body_weight_lbs FROM daily_log
            WHERE body_weight_lbs IS NOT NULL
            ORDER BY date DESC LIMIT 1
            """
        )
        row = cur.fetchone()
    return float(row[0]) if row else None


def infer_muscle_group(exercise_name: str) -> str:
    # exact match first
    if exercise_name in MUSCLE_MAP:
        return MUSCLE_MAP[exercise_name]
    # fallback: log unknown so we can add it
    import logging

    logging.getLogger(__name__).debug(
        f"Unknown exercise, tagging as other: {exercise_name!r}"
    )
    return "other"


# ---------------------------------------------------------------------------
# Process workouts into DB rows
# ---------------------------------------------------------------------------


def process_workouts(
    workouts: list[dict], athlete_bw: float | None = None
) -> tuple[list[dict], list[dict]]:
    """
    Returns:
        daily_rows: list of dicts to upsert into daily_log
        set_rows:   list of dicts to upsert into hevy_sets

    athlete_bw: bodyweight in lbs, used for bodyweight exercise volume.
    """
    # group by date (multiple sessions possible on one day)
    by_date = defaultdict(list)
    for w in workouts:
        by_date[w["_date"]].append(w)

    daily_rows = []
    set_rows = []

    for workout_date, day_workouts in sorted(by_date.items()):
        total_volume = 0.0
        total_sets = 0
        total_duration = 0
        muscle_groups = set()
        session_count = len(day_workouts)

        for workout in day_workouts:
            session_id = workout.get("id", "")
            session_title = workout.get("title", "")

            # duration
            start_str = workout.get("start_time", "")
            end_str = workout.get("end_time", "")
            if start_str and end_str:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                total_duration += int((end_dt - start_dt).total_seconds() / 60)

            for exercise in workout.get("exercises", []):
                exercise_name = exercise.get("title", "Unknown")
                muscle_group = infer_muscle_group(exercise_name)
                muscle_groups.add(muscle_group)

                for i, s in enumerate(exercise.get("sets", [])):
                    reps = s.get("reps")
                    weight_kg = s.get("weight_kg")
                    weight_lbs = kg_to_lbs(weight_kg)
                    rpe = s.get("rpe")
                    set_type = s.get("set_type", "normal")

                    # Bodyweight substitution
                    effective_weight = weight_lbs
                    if exercise_name in WEIGHTED_BODYWEIGHT_EXERCISES and athlete_bw:
                        # Weighted variant: Hevy has added load only → add BW
                        added = weight_lbs or 0
                        effective_weight = athlete_bw + added
                    elif (
                        not effective_weight
                        and exercise_name in BODYWEIGHT_EXERCISES
                        and athlete_bw
                    ):
                        effective_weight = athlete_bw

                    # only count sets with actual reps
                    if reps and reps > 0:
                        total_sets += 1
                        if effective_weight:
                            total_volume += reps * effective_weight

                    set_rows.append(
                        {
                            "date": workout_date.isoformat(),
                            "session_id": session_id,
                            "exercise_name": exercise_name,
                            "muscle_group": muscle_group,
                            "set_index": i,
                            "reps": reps,
                            "weight_lbs": effective_weight,
                            "rpe": rpe,
                            "set_type": set_type,
                            "session_title": session_title,
                        }
                    )

        daily_rows.append(
            {
                "date": workout_date.isoformat(),
                "hevy_session_count": session_count,
                "hevy_total_volume_lbs": round(total_volume, 1)
                if total_volume
                else None,
                "hevy_total_sets": total_sets or None,
                "hevy_session_duration_min": total_duration or None,
                "hevy_muscle_groups": list(muscle_groups) if muscle_groups else None,
            }
        )

    return daily_rows, set_rows


# ---------------------------------------------------------------------------
# Upsert helpers
# ---------------------------------------------------------------------------


def upsert_daily_hevy(conn, rows: list[dict]):
    if not rows:
        return
    for row in rows:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO daily_log (
                    date,
                    hevy_session_count,
                    hevy_total_volume_lbs,
                    hevy_total_sets,
                    hevy_session_duration_min,
                    hevy_muscle_groups
                ) VALUES (
                    %(date)s,
                    %(hevy_session_count)s,
                    %(hevy_total_volume_lbs)s,
                    %(hevy_total_sets)s,
                    %(hevy_session_duration_min)s,
                    %(hevy_muscle_groups)s
                )
                ON CONFLICT (date) DO UPDATE SET
                    hevy_session_count        = EXCLUDED.hevy_session_count,
                    hevy_total_volume_lbs     = EXCLUDED.hevy_total_volume_lbs,
                    hevy_total_sets           = EXCLUDED.hevy_total_sets,
                    hevy_session_duration_min = EXCLUDED.hevy_session_duration_min,
                    hevy_muscle_groups        = EXCLUDED.hevy_muscle_groups,
                    updated_at                = now()
            """,
                row,
            )
    conn.commit()


def upsert_sets(conn, rows: list[dict]):
    if not rows:
        return
    # delete existing sets for these dates then reinsert (cleaner than per-set upsert)
    dates = list({r["date"] for r in rows})
    with conn.cursor() as cur:
        cur.execute("DELETE FROM hevy_sets WHERE date = ANY(%s::date[])", (dates,))
        execute_values(
            cur,
            """
            INSERT INTO hevy_sets (
                date, session_id, exercise_name, muscle_group,
                set_index, reps, weight_lbs, rpe, set_type, session_title
            ) VALUES %s
        """,
            [
                (
                    r["date"],
                    r["session_id"],
                    r["exercise_name"],
                    r["muscle_group"],
                    r["set_index"],
                    r["reps"],
                    r["weight_lbs"],
                    r["rpe"],
                    r["set_type"],
                    r["session_title"],
                )
                for r in rows
            ],
        )
    conn.commit()


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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args():
    p = argparse.ArgumentParser(description="Hevy → Postgres ingest")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--days", type=int, help="Last N days (default: 30)")
    group.add_argument("--since", type=str, help="Since date YYYY-MM-DD")
    group.add_argument("--all", action="store_true", help="Full history")
    return p.parse_args()


def main():
    args = parse_args()
    today = local_today()

    if args.all:
        since = None
        log.info("Fetching full Hevy history")
    elif args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d").date()
        log.info(f"Fetching Hevy workouts since {since}")
    else:
        days = args.days or 30
        since = today - timedelta(days=days)
        log.info(f"Fetching Hevy workouts since {since} ({days} days)")

    # ── Body measurements ──────────────────────────────────────────
    body_measurements = get_all_body_measurements(since=since)
    log.info(f"Retrieved {len(body_measurements)} body measurements")

    if body_measurements:
        conn = get_db()
        bm_count = upsert_body_measurements(conn, body_measurements)
        log.info(f"Upserted {bm_count} body measurement rows")
        conn.close()

    # ── Workouts ───────────────────────────────────────────────────
    workouts = get_all_workouts(since=since)
    log.info(f"Retrieved {len(workouts)} workouts")

    if not workouts:
        log.info("No workouts found in range")
        return

    conn = get_db()

    athlete_bw = get_recent_bodyweight(conn)
    if athlete_bw:
        log.info(f"Using athlete bodyweight: {athlete_bw} lbs")
    else:
        log.warning("No bodyweight found in daily_log — bodyweight exercises will have 0 volume")

    daily_rows, set_rows = process_workouts(workouts, athlete_bw=athlete_bw)
    log.info(f"Processed {len(daily_rows)} training days, {len(set_rows)} sets")

    upsert_daily_hevy(conn, daily_rows)
    log.info(f"Upserted {len(daily_rows)} daily_log rows")
    upsert_sets(conn, set_rows)
    log.info(f"Upserted {len(set_rows)} set rows")

    log.info("Done.")
    conn.close()


if __name__ == "__main__":
    main()
