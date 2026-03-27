#!/usr/bin/env python3
"""
ingest_cronometer.py
Imports Cronometer CSV exports into daily_log.

Usage:
    python ingest_cronometer.py --servings servings.csv
    python ingest_cronometer.py --servings servings.csv --summary dailysummary.csv

Export from Cronometer: Settings → Export Data
  - Daily Nutrition → dailysummary.csv
  - Food & Recipe Entries → servings.csv
"""

import os
import csv
import argparse
import logging
from datetime import datetime, date
from collections import defaultdict
from dotenv import load_dotenv

import psycopg2

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


def get_db():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def upsert_cronometer(conn, rows: list[dict]):
    if not rows:
        return
    for row in rows:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO daily_log (
                    date,
                    crono_calories,
                    crono_protein_g,
                    crono_carbs_g,
                    crono_fat_g,
                    crono_fiber_g,
                    crono_sugar_g,
                    crono_sodium_mg,
                    crono_magnesium_mg,
                    crono_zinc_mg,
                    crono_vitamin_d_iu,
                    crono_potassium_mg,
                    crono_water_g,
                    crono_last_meal_time,
                    crono_meal_count
                ) VALUES (
                    %(date)s,
                    %(crono_calories)s,
                    %(crono_protein_g)s,
                    %(crono_carbs_g)s,
                    %(crono_fat_g)s,
                    %(crono_fiber_g)s,
                    %(crono_sugar_g)s,
                    %(crono_sodium_mg)s,
                    %(crono_magnesium_mg)s,
                    %(crono_zinc_mg)s,
                    %(crono_vitamin_d_iu)s,
                    %(crono_potassium_mg)s,
                    %(crono_water_g)s,
                    %(crono_last_meal_time)s,
                    %(crono_meal_count)s
                )
                ON CONFLICT (date) DO UPDATE SET
                    crono_calories        = EXCLUDED.crono_calories,
                    crono_protein_g       = EXCLUDED.crono_protein_g,
                    crono_carbs_g         = EXCLUDED.crono_carbs_g,
                    crono_fat_g           = EXCLUDED.crono_fat_g,
                    crono_fiber_g         = EXCLUDED.crono_fiber_g,
                    crono_sugar_g         = EXCLUDED.crono_sugar_g,
                    crono_sodium_mg       = EXCLUDED.crono_sodium_mg,
                    crono_magnesium_mg    = EXCLUDED.crono_magnesium_mg,
                    crono_zinc_mg         = EXCLUDED.crono_zinc_mg,
                    crono_vitamin_d_iu    = EXCLUDED.crono_vitamin_d_iu,
                    crono_potassium_mg   = EXCLUDED.crono_potassium_mg,
                    crono_water_g        = EXCLUDED.crono_water_g,
                    crono_last_meal_time  = EXCLUDED.crono_last_meal_time,
                    crono_meal_count      = EXCLUDED.crono_meal_count,
                    updated_at            = now()
            """,
                row,
            )
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def safe_float(val):
    try:
        return float(val) if val and val.strip() else None
    except (ValueError, AttributeError):
        return None


def parse_time(val):
    """Parse '4:07 PM' style time strings."""
    if not val or not val.strip():
        return None
    for fmt in ("%I:%M %p", "%H:%M"):
        try:
            return datetime.strptime(val.strip(), fmt).strftime("%H:%M:%S")
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Servings CSV (primary source — has meal timing)
# ---------------------------------------------------------------------------


def ingest_servings(path: str) -> list[dict]:
    log.info(f"Reading servings from {path}")
    by_date = defaultdict(
        lambda: {
            "calories": 0.0,
            "protein": 0.0,
            "carbs": 0.0,
            "fat": 0.0,
            "fiber": 0.0,
            "sugar": 0.0,
            "sodium": 0.0,
            "magnesium": 0.0,
            "zinc": 0.0,
            "vitamin_d": 0.0,
            "potassium": 0.0,
            "water": 0.0,
            "times": [],
            "meal_count": 0,
        }
    )

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            day_str = row.get("Day", "").strip()
            if not day_str:
                continue
            try:
                d = date.fromisoformat(day_str)
            except ValueError:
                continue

            day = by_date[d]
            day["calories"] += safe_float(row.get("Energy (kcal)")) or 0
            day["protein"] += safe_float(row.get("Protein (g)")) or 0
            day["carbs"] += safe_float(row.get("Carbs (g)")) or 0
            day["fat"] += safe_float(row.get("Fat (g)")) or 0
            day["fiber"] += safe_float(row.get("Fiber (g)")) or 0
            day["sugar"] += safe_float(row.get("Sugars (g)")) or 0
            day["sodium"] += safe_float(row.get("Sodium (mg)")) or 0
            day["magnesium"] += safe_float(row.get("Magnesium (mg)")) or 0
            day["zinc"] += safe_float(row.get("Zinc (mg)")) or 0
            day["vitamin_d"] += safe_float(row.get("Vitamin D (IU)")) or 0
            day["potassium"] += safe_float(row.get("Potassium (mg)")) or 0
            day["water"] += safe_float(row.get("Water (g)")) or 0
            day["meal_count"] += 1

            t = parse_time(row.get("Time", ""))
            if t:
                day["times"].append(t)

    rows = []
    for d, day in sorted(by_date.items()):
        last_meal = max(day["times"]) if day["times"] else None
        rows.append(
            {
                "date": d.isoformat(),
                "crono_calories": round(day["calories"], 1) or None,
                "crono_protein_g": round(day["protein"], 1) or None,
                "crono_carbs_g": round(day["carbs"], 1) or None,
                "crono_fat_g": round(day["fat"], 1) or None,
                "crono_fiber_g": round(day["fiber"], 1) or None,
                "crono_sugar_g": round(day["sugar"], 1) or None,
                "crono_sodium_mg": round(day["sodium"], 1) or None,
                "crono_magnesium_mg": round(day["magnesium"], 1) or None,
                "crono_zinc_mg": round(day["zinc"], 2) or None,
                "crono_vitamin_d_iu": round(day["vitamin_d"], 1) or None,
                "crono_potassium_mg": round(day["potassium"], 1) or None,
                "crono_water_g": round(day["water"], 1) or None,
                "crono_last_meal_time": last_meal,
                "crono_meal_count": day["meal_count"],
            }
        )

    log.info(f"Parsed {len(rows)} days from servings")
    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args():
    p = argparse.ArgumentParser(description="Cronometer CSV → Postgres ingest")
    p.add_argument("--servings", required=True, help="Path to servings CSV export")
    p.add_argument("--summary", help="Path to dailysummary CSV (optional)")
    return p.parse_args()


def main():
    args = parse_args()
    rows = ingest_servings(args.servings)

    if not rows:
        log.info("No data found")
        return

    conn = get_db()
    upsert_cronometer(conn, rows)
    log.info(f"Upserted {len(rows)} days of nutrition data")
    conn.close()
    log.info("Done.")


if __name__ == "__main__":
    main()
