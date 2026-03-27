#!/usr/bin/env python3
"""
ingest_garmin.py
Pulls daily health metrics from Garmin Connect and upserts into daily_log.
Run nightly via systemd timer or cron.

Usage:
    python ingest_garmin.py              # yesterday only
    python ingest_garmin.py --days 30    # last 30 days
    python ingest_garmin.py --date 2026-03-10  # specific date
    python ingest_garmin.py --backfill   # full historical (slow, be nice to garmin)
"""

import os
import sys
import time
import logging
import argparse
from datetime import date, datetime, timedelta
from dotenv import load_dotenv

from tz import today as local_today, LOCAL_TZ

import garminconnect
import psycopg2
from psycopg2.extras import execute_values

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


def get_db():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def upsert_daily(conn, rows: list[dict]):
    if not rows:
        return
    # normalize — union of all keys across all rows so missing fields become NULL
    all_cols = list(dict.fromkeys(k for r in rows for k in r.keys()))
    vals = [[r.get(c) for c in all_cols] for r in rows]
    update_cols = [c for c in all_cols if c != "date"]
    update_sql = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    sql = f"""
        INSERT INTO daily_log ({", ".join(all_cols)})
        VALUES %s
        ON CONFLICT (date) DO UPDATE SET
            {update_sql},
            updated_at = now()
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, vals)
    conn.commit()


# ---------------------------------------------------------------------------
# Garmin auth
# ---------------------------------------------------------------------------


def get_client() -> garminconnect.Garmin:
    email = os.environ["GARMIN_EMAIL"]
    password = os.environ["GARMIN_PASSWORD"]
    tokenstore = os.environ.get("GARMIN_TOKEN_PATH", "/home/blake/.garmin_tokens")

    client = garminconnect.Garmin(email, password)
    try:
        client.login(tokenstore)
        log.info("Logged in via stored tokens")
    except Exception:
        log.info("Token login failed, doing full login")
        client.login()
        client.garth.dump(tokenstore)
        log.info(f"Tokens saved to {tokenstore}")
    return client


# ---------------------------------------------------------------------------
# Data extraction helpers
# ---------------------------------------------------------------------------


def safe_get(d, *keys, default=None):
    """Safely traverse nested dicts."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, default)
        if d is None:
            return default
    return d


def extract_sleep(client, day: date) -> dict:
    out = {}
    try:
        data = client.get_sleep_data(day.isoformat())
        daily = safe_get(data, "dailySleepDTO", default={})

        out["sleep_total_sec"] = daily.get("sleepTimeSeconds")
        out["sleep_deep_sec"] = daily.get("deepSleepSeconds")
        out["sleep_light_sec"] = daily.get("lightSleepSeconds")
        out["sleep_rem_sec"] = daily.get("remSleepSeconds")
        out["sleep_awake_sec"] = daily.get("awakeSleepSeconds")
        out["sleep_score"] = safe_get(daily, "sleepScores", "overall", "value")

        start = daily.get("sleepStartTimestampGMT")
        end = daily.get("sleepEndTimestampGMT")
        if start:
            out["sleep_start"] = datetime.fromtimestamp(start / 1000, tz=LOCAL_TZ).isoformat()
        if end:
            out["sleep_end"] = datetime.fromtimestamp(end / 1000, tz=LOCAL_TZ).isoformat()
    except Exception as e:
        log.warning(f"Sleep fetch failed for {day}: {e}")
    return out


def extract_hrv(client, day: date) -> dict:
    out = {}
    try:
        data = client.get_hrv_data(day.isoformat())
        summary = safe_get(data, "hrvSummary", default={})
        out["hrv_nightly_avg"] = summary.get("lastNightAvg")
        out["hrv_5min_low"] = summary.get(
            "lastNight5MinHigh"
        )  # confusingly named in API
    except Exception as e:
        log.warning(f"HRV fetch failed for {day}: {e}")
    return out


def extract_stats(client, day: date) -> dict:
    out = {}
    try:
        data = client.get_stats(day.isoformat())
        out["resting_hr"] = data.get("restingHeartRate")
        out["steps"] = data.get("totalSteps")
        out["active_calories"] = data.get("activeKilocalories")
        out["total_calories"] = data.get("totalKilocalories")
        out["floors_climbed"] = data.get("floorsAscended")
        out["intensity_minutes"] = (
            (data.get("moderateIntensityMinutes") or 0)
            + (data.get("vigorousIntensityMinutes") or 0)
        ) or None
        out["stress_avg"] = data.get("averageStressLevel")
        out["body_battery_eod"] = safe_get(data, "bodyBatteryMostRecentValue")
        out["body_battery_max"] = safe_get(data, "bodyBatteryHighestValue")
        out["spo2_avg"] = safe_get(data, "averageSpO2Value")
        out["respiration_avg"] = safe_get(data, "avgWakingRespirationValue")
        out["vo2max"] = safe_get(data, "vo2MaxPreciseValue") or safe_get(
            data, "vo2MaxValue"
        )
    except Exception as e:
        log.warning(f"Stats fetch failed for {day}: {e}")
    return out


def extract_hr(client, day: date) -> dict:
    out = {}
    try:
        data = client.get_heart_rates(day.isoformat())
        out["hr_max_day"] = safe_get(data, "maxHeartRate")
    except Exception as e:
        log.warning(f"HR fetch failed for {day}: {e}")
    return out


def extract_weight(client, day: date) -> dict:
    out = {}
    try:
        data = client.get_body_composition(day.isoformat())
        # Body composition returns a list of weigh-ins for the date range
        weighins = safe_get(data, "dateWeightList", default=[])
        if weighins:
            # Get the most recent weigh-in for this day
            # Weight comes in grams from Garmin
            for w in weighins:
                weight_g = safe_get(w, "weight")
                if weight_g:
                    out["body_weight_lbs"] = round(float(weight_g) / 453.592, 1)
                    break
    except Exception as e:
        log.warning(f"Weight fetch failed for {day}: {e}")
    return out


# ---------------------------------------------------------------------------
# Main fetch loop
# ---------------------------------------------------------------------------


def fetch_day(client, day: date) -> dict:
    log.info(f"Fetching {day}")
    row = {"date": day.isoformat()}

    row.update(extract_sleep(client, day))
    time.sleep(0.5)
    row.update(extract_hrv(client, day))
    time.sleep(0.5)
    row.update(extract_stats(client, day))
    time.sleep(0.5)
    row.update(extract_hr(client, day))
    time.sleep(0.5)
    row.update(extract_weight(client, day))
    time.sleep(0.5)

    return row


def date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args():
    p = argparse.ArgumentParser(description="Garmin → Postgres ingest")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--days", type=int, help="Last N days (default: 1)")
    group.add_argument("--date", type=str, help="Specific date YYYY-MM-DD")
    group.add_argument(
        "--backfill", action="store_true", help="Pull all available history"
    )
    group.add_argument("--since", type=str, help="Pull from date to today")
    return p.parse_args()


def main():
    args = parse_args()
    today = local_today()

    if args.date:
        days = [datetime.strptime(args.date, "%Y-%m-%d").date()]
    elif args.backfill:
        # Garmin typically holds ~13 months of detailed data
        # your GDPR export covers further back but needs separate handling
        days = list(date_range(today - timedelta(days=365), today - timedelta(days=1)))
        log.info(f"Backfill mode: {len(days)} days — this will take a while")
    elif args.since:
        start = datetime.strptime(args.since, "%Y-%m-%d").date()
        days = list(date_range(start, today - timedelta(days=1)))
    else:
        n = args.days or 1
        days = list(date_range(today - timedelta(days=n), today))

    log.info(f"Fetching {len(days)} day(s): {days[0]} → {days[-1]}")

    client = get_client()
    conn = get_db()

    rows = []
    for day in days:
        try:
            row = fetch_day(client, day)
            rows.append(row)
            # batch upsert every 10 days to avoid losing everything if interrupted
            if len(rows) >= 10:
                upsert_daily(conn, rows)
                log.info(f"Upserted batch of {len(rows)}")
                rows = []
        except Exception as e:
            log.error(f"Failed on {day}: {e}")
            time.sleep(2)  # back off on error

    if rows:
        upsert_daily(conn, rows)
        log.info(f"Upserted final batch of {len(rows)}")

    log.info("Done.")
    conn.close()


if __name__ == "__main__":
    main()
