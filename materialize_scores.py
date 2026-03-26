#!/usr/bin/env python3
"""
Materialize nsight computed scores into daily_log.

Usage:
  python materialize_scores.py                # last 2 days (for nightly pipeline)
  python materialize_scores.py --days 365     # backfill last year
  python materialize_scores.py --date 2025-01-15  # single date
"""

import argparse
import os
import sys
from datetime import date, timedelta

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from scoring import materialize_scores, backfill_scores

load_dotenv()

# Ensure nsight_score columns exist (idempotent)
ADD_COLUMNS_SQL = """
DO $$
BEGIN
    ALTER TABLE daily_log ADD COLUMN IF NOT EXISTS nsight_sleep_score NUMERIC(5,1);
    ALTER TABLE daily_log ADD COLUMN IF NOT EXISTS nsight_recovery_score NUMERIC(5,1);
    ALTER TABLE daily_log ADD COLUMN IF NOT EXISTS nsight_training_score NUMERIC(5,1);
    ALTER TABLE daily_log ADD COLUMN IF NOT EXISTS nsight_nutrition_score NUMERIC(5,1);
    ALTER TABLE daily_log ADD COLUMN IF NOT EXISTS nsight_overall_score NUMERIC(5,1);
END $$;
"""


def get_conn():
    return psycopg2.connect(
        os.environ["DATABASE_URL"],
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def main():
    parser = argparse.ArgumentParser(description="Materialize nsight scores")
    parser.add_argument("--days", type=int, default=2,
                        help="Backfill last N days (default: 2)")
    parser.add_argument("--date", type=str, default=None,
                        help="Single date to compute (YYYY-MM-DD)")
    args = parser.parse_args()

    conn = get_conn()
    try:
        # Ensure columns exist
        with conn.cursor() as cur:
            cur.execute(ADD_COLUMNS_SQL)
        conn.commit()

        if args.date:
            d = date.fromisoformat(args.date)
            scores = materialize_scores(conn, d)
            print(f"{d}: {scores}")
        else:
            count = backfill_scores(conn, days=args.days)
            print(f"Materialized scores for {count} days")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
