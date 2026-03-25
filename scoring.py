"""
scoring.py — Pure Python scoring engine for nsight.
No Flask dependency. All functions receive a psycopg2 connection
with RealDictCursor (dict-style row access).

Score philosophy: 80 = at personal 90-day baseline, not population norm.
"""

from datetime import date, timedelta


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _f(val):
    """Convert Decimal/None to float."""
    if val is None:
        return None
    return float(val)


def _pct_delta(current, baseline):
    """Return percentage delta of current vs baseline. None if either is None or baseline is 0."""
    if current is None or baseline is None or float(baseline) == 0:
        return None
    return ((float(current) - float(baseline)) / float(baseline)) * 100


def _clamp(val, lo=0, hi=100):
    """Clamp and round a value to [lo, hi]."""
    return max(lo, min(hi, round(val)))


def _score_component(current, baseline, higher_is_better=True):
    """
    Score a single metric vs baseline. Returns 0-100 where 80 = at baseline.
    Above baseline: gains points. Below: loses points (faster penalty for below).
    """
    if current is None or baseline is None:
        return None
    pct = _pct_delta(current, baseline)
    if pct is None:
        return None
    if higher_is_better:
        return _clamp(80 + pct * 1.0 if pct >= 0 else 80 + pct * 1.5)
    else:
        return _clamp(80 - pct * 1.0 if pct >= 0 else 80 - pct * 1.5)


def _metric_status(current, baseline, std_dev, higher_is_better=True):
    """
    Return (status_text, status_color) for trend card pill.
    Within ±1 SD = Normal/green. Outside depends on direction.
    """
    if current is None or baseline is None or std_dev is None or float(std_dev) == 0:
        return ("No data", "muted")
    delta = float(current) - float(baseline)
    if abs(delta) <= float(std_dev):
        return ("Normal", "green")
    if delta > 0:
        return ("Above", "green") if higher_is_better else ("Above", "amber")
    else:
        return ("Below", "amber") if higher_is_better else ("Below", "green")


def _weighted_average(components, weights):
    """
    Compute a weighted average, redistributing weight from None components.
    components: list of (score_or_None, weight) tuples.
    Returns final score (float) or None if all components are None.
    """
    total_weight = 0.0
    total_score = 0.0
    for score, weight in components:
        if score is not None:
            total_score += score * weight
            total_weight += weight
    if total_weight == 0:
        return None
    return total_score / total_weight


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------


def fetch_baselines(conn, target_date):
    """
    Query 90-day rolling averages and std devs for all key metrics.
    Each metric's AVG/STDDEV independently ignores NULLs — no global NULL filter.
    Also fetches median sleep start hour via PERCENTILE_CONT.
    Returns a flat dict of baseline and stddev values.
    """
    sql = """
        SELECT
            AVG(sleep_total_sec)            AS sleep_total_avg,
            STDDEV(sleep_total_sec)         AS sleep_total_std,
            AVG(sleep_deep_sec)             AS sleep_deep_avg,
            STDDEV(sleep_deep_sec)          AS sleep_deep_std,
            AVG(sleep_awake_sec)            AS sleep_awake_avg,
            STDDEV(sleep_awake_sec)         AS sleep_awake_std,
            AVG(hrv_nightly_avg)            AS hrv_avg,
            STDDEV(hrv_nightly_avg)         AS hrv_std,
            AVG(resting_hr)                 AS resting_hr_avg,
            STDDEV(resting_hr)              AS resting_hr_std,
            AVG(body_battery_eod)           AS body_battery_avg,
            STDDEV(body_battery_eod)        AS body_battery_std,
            AVG(steps)                      AS steps_avg,
            STDDEV(steps)                   AS steps_std,
            AVG(stress_avg)                 AS stress_avg_val,
            STDDEV(stress_avg)              AS stress_std,
            AVG(hevy_total_volume_lbs)      AS volume_avg,
            STDDEV(hevy_total_volume_lbs)   AS volume_std,
            AVG(crono_calories)             AS calories_avg,
            STDDEV(crono_calories)          AS calories_std,
            AVG(crono_protein_g)            AS protein_avg,
            STDDEV(crono_protein_g)         AS protein_std,
            AVG(crono_carbs_g)              AS carbs_avg,
            STDDEV(crono_carbs_g)           AS carbs_std,
            AVG(spo2_avg)                   AS spo2_avg_val,
            STDDEV(spo2_avg)                AS spo2_std,
            AVG(respiration_avg)            AS respiration_avg_val,
            STDDEV(respiration_avg)         AS respiration_std,
            PERCENTILE_CONT(0.5) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (sleep_start AT TIME ZONE 'America/Chicago'))::BIGINT %% 86400
            ) AS median_sleep_start_sec
        FROM daily_log
        WHERE date >= %s
          AND date < %s
    """
    start_date = target_date - timedelta(days=90)
    with conn.cursor() as cur:
        cur.execute(sql, (start_date, target_date))
        row = cur.fetchone()

    if row is None:
        return {}

    result = {}
    for key, val in row.items():
        result[key] = _f(val)

    # Convert median_sleep_start_sec (seconds since midnight) to fractional hour
    med_sec = result.get("median_sleep_start_sec")
    if med_sec is not None:
        # Handle after-midnight values (e.g. 1am = 3600, but 11pm = 82800)
        # Express as hour in 24h — keep as-is for comparison
        result["median_sleep_start_hour"] = med_sec / 3600.0
    else:
        result["median_sleep_start_hour"] = None

    return result


# ---------------------------------------------------------------------------
# Sleep Score
# ---------------------------------------------------------------------------


def compute_sleep_score(conn, target_date, baselines):
    """
    Sleep score (0-100):
      - Total sleep vs 90d baseline:    30%
      - Deep sleep vs 90d baseline:     30%
      - Sleep efficiency:               20%  (total - awake) / total
      - Sleep consistency (timing):     20%  vs median sleep start from baselines

    Returns {'score': int, 'components': dict}
    """
    sql = """
        SELECT
            sleep_total_sec,
            sleep_deep_sec,
            sleep_awake_sec,
            sleep_start,
            sleep_end
        FROM daily_log
        WHERE date = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (target_date))
        row = cur.fetchone()

    if row is None:
        return {"score": None, "components": {}}

    total    = _f(row.get("sleep_total_sec"))
    deep     = _f(row.get("sleep_deep_sec"))
    awake    = _f(row.get("sleep_awake_sec"))
    sleep_start = row.get("sleep_start")

    # Component 1: total sleep vs baseline
    total_score = _score_component(total, baselines.get("sleep_total_avg"), higher_is_better=True)

    # Component 2: deep sleep vs baseline
    deep_score = _score_component(deep, baselines.get("sleep_deep_avg"), higher_is_better=True)

    # Component 3: sleep efficiency — (total - awake) / total * 100
    eff_score = None
    efficiency_pct = None
    if total is not None and total > 0 and awake is not None:
        efficiency_pct = ((total - awake) / total) * 100
        # 85% efficiency = 80 points; scale linearly
        # Each point of efficiency above/below 85% maps to ~1.33 score points
        eff_score = _clamp(80 + (efficiency_pct - 85) * 1.33)

    # Component 4: sleep consistency — how close is sleep start to median?
    consistency_score = None
    actual_sleep_hour = None
    if sleep_start is not None:
        import datetime as _dt
        # Extract hour-of-day in seconds since midnight (local timezone)
        try:
            from zoneinfo import ZoneInfo
            import os
            tz = ZoneInfo(os.environ.get("TZ", "America/Chicago"))
            local_start = sleep_start.astimezone(tz)
            # Seconds since midnight
            actual_sleep_sec = (
                local_start.hour * 3600
                + local_start.minute * 60
                + local_start.second
            )
            actual_sleep_hour = actual_sleep_sec / 3600.0
            median_sec = baselines.get("median_sleep_start_hour")
            if median_sec is not None:
                median_sec_actual = median_sec * 3600.0
                # Delta in seconds — wrap around midnight
                delta_sec = abs(actual_sleep_sec - median_sec_actual)
                if delta_sec > 43200:  # more than 12h difference means wrap
                    delta_sec = 86400 - delta_sec
                # Within 30 min (1800s) = 80 pts; each extra 30 min = -10 pts
                delta_30min_units = delta_sec / 1800.0
                consistency_score = _clamp(80 - (delta_30min_units - 1) * 10 if delta_30min_units > 1 else 80)
        except Exception:
            consistency_score = None

    components = {
        "total_sleep":    total_score,
        "deep_sleep":     deep_score,
        "efficiency":     eff_score,
        "consistency":    consistency_score,
        "efficiency_pct": round(efficiency_pct, 1) if efficiency_pct is not None else None,
        "actual_sleep_hour": round(actual_sleep_hour, 2) if actual_sleep_hour is not None else None,
    }

    parts = [
        (total_score,       0.30),
        (deep_score,        0.30),
        (eff_score,         0.20),
        (consistency_score, 0.20),
    ]
    total_w = sum(w for s, w in parts if s is not None)
    score_val = sum(s * w for s, w in parts if s is not None) / total_w if total_w > 0 else None
    final_score = _clamp(score_val) if score_val is not None else None

    return {"score": final_score, "components": components}


# ---------------------------------------------------------------------------
# Recovery Score
# ---------------------------------------------------------------------------


def compute_recovery_score(conn, target_date, baselines):
    """
    Recovery score (0-100):
      - HRV vs baseline:             40%
      - Deep sleep vs baseline:      25%
      - Resting HR vs baseline:      20%  (lower is better — inverted)
      - Body battery:                15%

    Returns {'score': int, 'components': dict}
    """
    sql = """
        SELECT
            hrv_nightly_avg,
            resting_hr,
            body_battery_eod,
            sleep_deep_sec
        FROM daily_log
        WHERE date = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (target_date))
        row = cur.fetchone()

    if row is None:
        return {"score": None, "components": {}}

    hrv          = _f(row.get("hrv_nightly_avg"))
    resting_hr   = _f(row.get("resting_hr"))
    body_battery = _f(row.get("body_battery_eod"))
    deep         = _f(row.get("sleep_deep_sec"))

    # Component 1: HRV vs baseline (higher is better)
    hrv_score = _score_component(hrv, baselines.get("hrv_avg"), higher_is_better=True)

    # Component 2: deep sleep vs baseline (higher is better)
    deep_score = _score_component(deep, baselines.get("sleep_deep_avg"), higher_is_better=True)

    # Component 3: resting HR vs baseline (lower is better)
    rhr_score = _score_component(resting_hr, baselines.get("resting_hr_avg"), higher_is_better=False)

    # Component 4: body battery — absolute scale 0-100, 80 = 80 points
    bb_score = None
    if body_battery is not None:
        bb_score = _clamp(body_battery)

    components = {
        "hrv":          hrv_score,
        "deep_sleep":   deep_score,
        "resting_hr":   rhr_score,
        "body_battery": bb_score,
        "hrv_raw":          hrv,
        "resting_hr_raw":   resting_hr,
        "body_battery_raw": body_battery,
    }

    parts = [
        (hrv_score,  0.40),
        (deep_score, 0.25),
        (rhr_score,  0.20),
        (bb_score,   0.15),
    ]
    total_w = sum(w for s, w in parts if s is not None)
    score_val = sum(s * w for s, w in parts if s is not None) / total_w if total_w > 0 else None
    final_score = _clamp(score_val) if score_val is not None else None

    return {"score": final_score, "components": components}


# ---------------------------------------------------------------------------
# Training Score
# ---------------------------------------------------------------------------


def compute_training_score(conn, target_date):
    """
    Training score (0-100):
      - ACWR zone:                   40%
      - Volume trend vs 28d avg:     30%
      - Session consistency:         20%  (days since last session; 3-4 day gaps are normal for DoggCrapp)
      - Muscle group coverage:       10%  (unique groups in last 7 days)

    Returns {'score': int, 'acwr': float, 'components': dict}
    """
    # --- ACWR: try derived_daily first, then compute inline ---
    acwr = None
    sql_acwr = """
        SELECT acwr_volume FROM derived_daily WHERE date = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql_acwr, (target_date))
        row = cur.fetchone()
        if row and row.get("acwr_volume") is not None:
            acwr = _f(row["acwr_volume"])

    if acwr is None:
        # Compute inline: 7-day acute / 28-day chronic volume
        sql_inline = """
            SELECT
                (SELECT COALESCE(SUM(hevy_total_volume_lbs), 0)
                 FROM daily_log
                 WHERE date >= %s AND date <= %s) AS acute_vol,
                (SELECT COALESCE(SUM(hevy_total_volume_lbs), 0)
                 FROM daily_log
                 WHERE date >= %s AND date <= %s) AS chronic_vol
        """
        acute_start   = target_date - timedelta(days=6)
        chronic_start = target_date - timedelta(days=27)
        with conn.cursor() as cur:
            cur.execute(sql_inline, (target_date, acute_start, chronic_start))
            row = cur.fetchone()
        if row:
            acute_vol   = _f(row.get("acute_vol"))  or 0.0
            chronic_vol = _f(row.get("chronic_vol")) or 0.0
            # Normalize chronic to 7-day window for comparison
            weekly_chronic = chronic_vol / 4.0 if chronic_vol > 0 else 0.0
            if weekly_chronic > 0:
                acwr = acute_vol / weekly_chronic
            else:
                acwr = None

    # Score ACWR zone
    # 0.8-1.3 = optimal (95 pts), gradual falloff outside
    acwr_score = None
    if acwr is not None:
        if 0.8 <= acwr <= 1.3:
            acwr_score = 95
        elif acwr < 0.8:
            # Below 0.8: detraining zone; each 0.1 below 0.8 = -10 pts
            acwr_score = _clamp(95 - ((0.8 - acwr) / 0.1) * 10)
        elif 1.3 < acwr <= 1.7:
            # Overreach zone: each 0.1 above 1.3 = -12 pts
            acwr_score = _clamp(95 - ((acwr - 1.3) / 0.1) * 12)
        else:
            # > 1.7: high injury risk zone
            acwr_score = _clamp(95 - ((1.7 - 1.3) / 0.1) * 12 - ((acwr - 1.7) / 0.1) * 20)

    # --- Volume trend: today's volume vs 28-day average ---
    sql_vol = """
        SELECT
            (SELECT hevy_total_volume_lbs FROM daily_log WHERE date = %s) AS today_vol,
            (SELECT AVG(hevy_total_volume_lbs)
             FROM daily_log
             WHERE date >= %s AND date < %s
               AND hevy_total_volume_lbs IS NOT NULL
               AND hevy_session_count > 0) AS avg_vol_28d
    """
    vol_start = target_date - timedelta(days=28)
    with conn.cursor() as cur:
        cur.execute(sql_vol, (target_date, vol_start))
        row = cur.fetchone()

    today_vol   = _f(row.get("today_vol"))  if row else None
    avg_vol_28d = _f(row.get("avg_vol_28d")) if row else None

    volume_score = None
    if today_vol is not None and avg_vol_28d is not None and avg_vol_28d > 0:
        # On a training day: compare volume vs avg
        volume_score = _score_component(today_vol, avg_vol_28d, higher_is_better=True)
    elif today_vol is None or today_vol == 0:
        # Rest day — neutral score for volume component (expected in DoggCrapp)
        volume_score = 80

    # --- Session consistency: days since last session ---
    sql_last = """
        SELECT MAX(date) AS last_session_date
        FROM daily_log
        WHERE date < %s
          AND hevy_session_count > 0
    """
    with conn.cursor() as cur:
        cur.execute(sql_last, (target_date))
        row = cur.fetchone()

    days_since = None
    consistency_score = None
    if row and row.get("last_session_date") is not None:
        last_date = row["last_session_date"]
        days_since = (target_date - last_date).days

        # DoggCrapp: 3-4 day gaps are normal. Ideal = 3-4 days.
        if 3 <= days_since <= 4:
            consistency_score = 90
        elif days_since == 2:
            consistency_score = 80
        elif days_since == 5:
            consistency_score = 75
        elif days_since == 1:
            # Trained yesterday — possibly too frequent
            consistency_score = 65
        elif days_since >= 6:
            # Getting stale
            consistency_score = _clamp(75 - (days_since - 5) * 8)
        else:
            consistency_score = 70

    # --- Muscle group coverage: unique groups in last 7 days ---
    sql_muscles = """
        SELECT hevy_muscle_groups
        FROM daily_log
        WHERE date >= %s
          AND date <= %s
          AND hevy_muscle_groups IS NOT NULL
    """
    muscle_start = target_date - timedelta(days=6)
    with conn.cursor() as cur:
        cur.execute(sql_muscles, (target_date, muscle_start))
        rows = cur.fetchall()

    unique_groups = set()
    for r in rows:
        mg = r.get("hevy_muscle_groups")
        if mg:
            unique_groups.update(mg)

    coverage_score = None
    n_groups = len(unique_groups)
    if n_groups >= 6:
        coverage_score = 95
    elif n_groups == 5:
        coverage_score = 85
    elif n_groups == 4:
        coverage_score = 75
    elif n_groups == 3:
        coverage_score = 65
    elif n_groups == 2:
        coverage_score = 55
    elif n_groups == 1:
        coverage_score = 45
    else:
        coverage_score = None  # No training data this week

    components = {
        "acwr_zone":    acwr_score,
        "volume_trend": volume_score,
        "consistency":  consistency_score,
        "mg_coverage":  coverage_score,
        "days_since_session": days_since,
        "unique_muscle_groups": n_groups,
    }

    parts = [
        (acwr_score,       0.40),
        (volume_score,     0.30),
        (consistency_score,0.20),
        (coverage_score,   0.10),
    ]
    total_w = sum(w for s, w in parts if s is not None)
    score_val = sum(s * w for s, w in parts if s is not None) / total_w if total_w > 0 else None
    final_score = _clamp(score_val) if score_val is not None else None

    return {"score": final_score, "acwr": acwr, "components": components}


# ---------------------------------------------------------------------------
# Nutrition Score
# ---------------------------------------------------------------------------


def compute_nutrition_score(conn, target_date):
    """
    Nutrition score (0-100), day-of-week aware:
      - Calorie adherence:           30%
      - Protein adherence (280g):    30%
      - Carb adherence (day-specific):20%
      - Micro coverage (fiber, sodium):20%

    Tue/Wed = high-carb day (400g carbs, 3170 kcal)
    Other days = low-carb day (300g carbs, 2770 kcal)

    Returns {'score': int, 'targets': dict, 'components': dict}
    """
    # Determine day-of-week targets
    dow = target_date.weekday()  # 0=Mon, 1=Tue, 2=Wed, ...
    high_carb_day = dow in (1, 2)  # Tuesday, Wednesday

    if high_carb_day:
        cal_target  = 3170.0
        carb_target = 400.0
    else:
        cal_target  = 2770.0
        carb_target = 300.0

    protein_target = 280.0

    targets = {
        "calories":  cal_target,
        "protein_g": protein_target,
        "carbs_g":   carb_target,
        "high_carb_day": high_carb_day,
    }

    sql = """
        SELECT
            crono_calories,
            crono_protein_g,
            crono_carbs_g,
            crono_fiber_g,
            crono_sodium_mg
        FROM daily_log
        WHERE date = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (target_date))
        row = cur.fetchone()

    if row is None:
        return {"score": None, "targets": targets, "components": {}}

    calories  = _f(row.get("crono_calories"))
    protein   = _f(row.get("crono_protein_g"))
    carbs     = _f(row.get("crono_carbs_g"))
    fiber     = _f(row.get("crono_fiber_g"))
    sodium    = _f(row.get("crono_sodium_mg"))

    def _adherence_score(actual, target, tolerance_pct=10.0):
        """
        Score adherence to a target with a tolerance band.
        Within tolerance_pct% of target = 90 points.
        Farther away = penalized. Returns 0-100.
        """
        if actual is None or target is None or target == 0:
            return None
        pct_diff = abs((actual - target) / target) * 100
        if pct_diff <= tolerance_pct:
            # Within tolerance — full marks minus small penalty for being off
            return _clamp(90 + (tolerance_pct - pct_diff) * 1.0)
        else:
            # Outside tolerance — lose points faster
            return _clamp(90 - (pct_diff - tolerance_pct) * 2.5)

    # Component 1: calorie adherence
    cal_score = _adherence_score(calories, cal_target, tolerance_pct=10.0)

    # Component 2: protein adherence
    protein_score = _adherence_score(protein, protein_target, tolerance_pct=10.0)

    # Component 3: carb adherence
    carb_score = _adherence_score(carbs, carb_target, tolerance_pct=12.0)

    # Component 4: micro coverage — fiber (26-34g target range) and sodium (3500-4500mg)
    fiber_score  = None
    sodium_score = None

    if fiber is not None:
        if 26.0 <= fiber <= 34.0:
            fiber_score = 90
        elif fiber < 26.0:
            deficit = 26.0 - fiber
            fiber_score = _clamp(90 - deficit * 3.0)
        else:
            excess = fiber - 34.0
            fiber_score = _clamp(90 - excess * 2.0)

    if sodium is not None:
        if 3500.0 <= sodium <= 4500.0:
            sodium_score = 90
        elif sodium < 3500.0:
            deficit = 3500.0 - sodium
            sodium_score = _clamp(90 - (deficit / 100) * 3.0)
        else:
            excess = sodium - 4500.0
            sodium_score = _clamp(90 - (excess / 100) * 3.0)

    # Micro component: average of fiber + sodium (skip if both None)
    micro_parts = [s for s in [fiber_score, sodium_score] if s is not None]
    micro_score = round(sum(micro_parts) / len(micro_parts)) if micro_parts else None

    components = {
        "calories":   cal_score,
        "protein":    protein_score,
        "carbs":      carb_score,
        "micros":     micro_score,
        "fiber_score":  fiber_score,
        "sodium_score": sodium_score,
        "calories_raw":  calories,
        "protein_raw":   protein,
        "carbs_raw":     carbs,
        "fiber_raw":     fiber,
        "sodium_raw":    sodium,
    }

    parts = [
        (cal_score,     0.30),
        (protein_score, 0.30),
        (carb_score,    0.20),
        (micro_score,   0.20),
    ]
    total_w = sum(w for s, w in parts if s is not None)
    score_val = sum(s * w for s, w in parts if s is not None) / total_w if total_w > 0 else None
    final_score = _clamp(score_val) if score_val is not None else None

    return {"score": final_score, "targets": targets, "components": components}


# ---------------------------------------------------------------------------
# Overall Score
# ---------------------------------------------------------------------------


def compute_overall_score(sleep, recovery, training, nutrition):
    """
    Overall health score (0-100):
      - Sleep:     30%
      - Recovery:  30%
      - Training:  20%
      - Nutrition: 20%

    Handles None inputs gracefully by redistributing weight.
    Each argument should be the dict returned by compute_*_score(),
    or None if unavailable.
    """
    def _extract(result):
        if result is None:
            return None
        if isinstance(result, dict):
            return result.get("score")
        return None

    s = _extract(sleep)
    r = _extract(recovery)
    t = _extract(training)
    n = _extract(nutrition)

    parts = [
        (s, 0.30),
        (r, 0.30),
        (t, 0.20),
        (n, 0.20),
    ]
    total_w = sum(w for sc, w in parts if sc is not None)
    if total_w == 0:
        return None
    score_val = sum(sc * w for sc, w in parts if sc is not None) / total_w
    return _clamp(score_val)


# ---------------------------------------------------------------------------
# 30-day classification
# ---------------------------------------------------------------------------


def classify_30_days(conn, target_date, category):
    """
    For each of the last 30 days, compute category score and classify:
      >80 = Good, 60-79 = Fair, <60 = Poor

    category: one of 'sleep', 'recovery', 'training', 'nutrition'
    Returns {'good': N, 'fair': N, 'poor': N}
    """
    counts = {"good": 0, "fair": 0, "poor": 0}

    for i in range(1, 31):
        day = target_date - timedelta(days=i)

        score = None
        try:
            if category == "sleep":
                bl = fetch_baselines(conn, day)
                result = compute_sleep_score(conn, day, bl)
                score = result.get("score")
            elif category == "recovery":
                bl = fetch_baselines(conn, day)
                result = compute_recovery_score(conn, day, bl)
                score = result.get("score")
            elif category == "training":
                result = compute_training_score(conn, day)
                score = result.get("score")
            elif category == "nutrition":
                result = compute_nutrition_score(conn, day)
                score = result.get("score")
        except Exception:
            score = None

        if score is None:
            continue
        if score > 80:
            counts["good"] += 1
        elif score >= 60:
            counts["fair"] += 1
        else:
            counts["poor"] += 1

    return counts


# ---------------------------------------------------------------------------
# Hero summary generation
# ---------------------------------------------------------------------------


def generate_hero_summary(category, score, data):
    """
    Generate a plain-english summary string for hero banners.
    category: 'sleep', 'recovery', 'training', 'nutrition', 'overall'
    score: int 0-100 (or None)
    data: dict with relevant metrics (components, raw values, etc.)

    Returns a string.
    """
    if score is None:
        return "No data available for today."

    def _level(s):
        if s >= 80:
            return "solid"
        elif s >= 60:
            return "decent"
        else:
            return "below your usual"

    level = _level(score)

    # --- Sleep ---
    if category == "sleep":
        components = data.get("components", {})
        eff_pct    = components.get("efficiency_pct")
        deep_sc    = components.get("deep_sleep")
        cons_sc    = components.get("consistency")

        if score >= 80:
            if deep_sc and deep_sc >= 80:
                return (
                    f"Sleep looks solid tonight — good deep sleep and timing were both on point."
                )
            return f"You got a solid night's sleep. Efficiency and duration were both in your normal range."
        elif score >= 60:
            weakest = []
            if deep_sc is not None and deep_sc < 70:
                weakest.append("deep sleep was a bit light")
            if cons_sc is not None and cons_sc < 70:
                weakest.append("sleep timing was off your usual window")
            if eff_pct is not None and eff_pct < 80:
                weakest.append("you spent more time awake than usual")
            detail = " and ".join(weakest) if weakest else "some metrics came in below your baseline"
            return f"Decent sleep — {detail}."
        else:
            weakest = []
            if deep_sc is not None and deep_sc < 60:
                weakest.append("deep sleep was significantly below your baseline")
            if eff_pct is not None and eff_pct < 75:
                weakest.append(f"sleep efficiency was low ({eff_pct:.0f}%)")
            if cons_sc is not None and cons_sc < 60:
                weakest.append("sleep timing was well outside your normal window")
            detail = "; ".join(weakest) if weakest else "multiple sleep metrics were below your baseline"
            return f"Below your usual sleep quality — {detail}."

    # --- Recovery ---
    elif category == "recovery":
        components  = data.get("components", {})
        hrv_raw     = components.get("hrv_raw")
        rhr_raw     = components.get("resting_hr_raw")
        bb_raw      = components.get("body_battery_raw")
        hrv_sc      = components.get("hrv")
        rhr_sc      = components.get("resting_hr")
        bb_sc       = components.get("body_battery")

        if score >= 80:
            parts = []
            if hrv_raw is not None:
                parts.append(f"HRV at {hrv_raw:.0f}ms")
            if rhr_raw is not None:
                parts.append(f"resting HR {rhr_raw:.0f}bpm")
            if bb_raw is not None:
                parts.append(f"body battery at {bb_raw:.0f}")
            detail = ", ".join(parts) if parts else "all markers"
            return f"Recovery looks solid — {detail} — all within your normal range."
        elif score >= 60:
            weak = []
            if hrv_sc is not None and hrv_sc < 70:
                val = f" ({hrv_raw:.0f}ms)" if hrv_raw else ""
                weak.append(f"HRV{val} is slightly suppressed")
            if rhr_sc is not None and rhr_sc < 70:
                val = f" ({rhr_raw:.0f}bpm)" if rhr_raw else ""
                weak.append(f"resting HR{val} is a bit elevated")
            if bb_sc is not None and bb_sc < 60:
                val = f" ({bb_raw:.0f})" if bb_raw else ""
                weak.append(f"body battery{val} is low")
            detail = " and ".join(weak) if weak else "a couple markers are off"
            return f"Decent recovery, though {detail}."
        else:
            weak = []
            if hrv_sc is not None and hrv_sc < 60:
                val = f" ({hrv_raw:.0f}ms)" if hrv_raw else ""
                weak.append(f"HRV{val} is well below your baseline")
            if rhr_sc is not None and rhr_sc < 60:
                val = f" ({rhr_raw:.0f}bpm)" if rhr_raw else ""
                weak.append(f"resting HR{val} is elevated")
            if bb_sc is not None and bb_sc < 50:
                val = f" ({bb_raw:.0f})" if bb_raw else ""
                weak.append(f"body battery{val} is very low")
            detail = "; ".join(weak) if weak else "recovery markers are below your baseline"
            return f"Recovery is below your usual — {detail}."

    # --- Training ---
    elif category == "training":
        acwr       = data.get("acwr")
        components = data.get("components", {})
        days_since = components.get("days_since_session")
        mg_count   = components.get("unique_muscle_groups", 0)

        if score >= 80:
            acwr_str = f"ACWR at {acwr:.2f}" if acwr else "workload ratio"
            return (
                f"Training load is dialed in — {acwr_str} sits in the optimal zone "
                f"and you've hit {mg_count} muscle group{'s' if mg_count != 1 else ''} this week."
            )
        elif score >= 60:
            if acwr and acwr > 1.3:
                return (
                    f"Decent training week, but your ACWR of {acwr:.2f} is nudging into overreach territory — "
                    f"consider whether the next session needs to be scaled back."
                )
            elif acwr and acwr < 0.8:
                return (
                    f"Training volume is on the low side this week (ACWR {acwr:.2f}). "
                    f"If this is a planned deload, you're on track."
                )
            return f"Decent training load this week. Keep an eye on volume and recovery balance."
        else:
            if days_since is not None and days_since >= 6:
                return (
                    f"It's been {days_since} days since your last session — "
                    f"longer than your usual DoggCrapp cycle. Worth checking in on fatigue or scheduling."
                )
            if acwr and acwr > 1.7:
                return (
                    f"Training load is high — ACWR at {acwr:.2f} is in the risk zone. "
                    f"Prioritize recovery before the next session."
                )
            return f"Training metrics are below your usual standard this week."

    # --- Nutrition ---
    elif category == "nutrition":
        targets    = data.get("targets", {})
        components = data.get("components", {})
        protein    = components.get("protein_raw")
        protein_sc = components.get("protein")
        cal        = components.get("calories_raw")
        cal_sc     = components.get("calories")
        high_carb  = targets.get("high_carb_day", False)
        day_type   = "high-carb" if high_carb else "low-carb"

        if score >= 80:
            parts = []
            if protein is not None:
                parts.append(f"protein at {protein:.0f}g")
            if cal is not None:
                parts.append(f"calories at {cal:.0f}kcal")
            detail = " and ".join(parts) if parts else "macros"
            return f"Nutrition was on point for a {day_type} day — {detail} hit your targets."
        elif score >= 60:
            weak = []
            if protein_sc is not None and protein_sc < 70:
                val = f" ({protein:.0f}g vs 280g target)" if protein else ""
                weak.append(f"protein{val} came up short")
            if cal_sc is not None and cal_sc < 70:
                val = f" ({cal:.0f}kcal)" if cal else ""
                weak.append(f"calories{val} were off target")
            detail = " and ".join(weak) if weak else "a couple macros missed"
            return f"Decent {day_type} day nutrition, though {detail}."
        else:
            weak = []
            if protein_sc is not None and protein_sc < 60:
                val = f" ({protein:.0f}g vs 280g)" if protein else ""
                weak.append(f"protein{val} was well below target")
            if cal_sc is not None and cal_sc < 60:
                val = f" ({cal:.0f}kcal)" if cal else ""
                weak.append(f"calorie intake{val} missed the mark")
            detail = "; ".join(weak) if weak else "nutrition was below your targets"
            return f"Below your usual nutrition standards for a {day_type} day — {detail}."

    # --- Overall ---
    elif category == "overall":
        sleep_sc   = data.get("sleep_score")
        recovery_sc= data.get("recovery_score")
        training_sc= data.get("training_score")
        nutrition_sc = data.get("nutrition_score")

        scores_present = {
            "sleep":     sleep_sc,
            "recovery":  recovery_sc,
            "training":  training_sc,
            "nutrition": nutrition_sc,
        }
        weak = [k for k, v in scores_present.items() if v is not None and v < 65]
        strong = [k for k, v in scores_present.items() if v is not None and v >= 80]

        if score >= 80:
            if strong:
                strong_str = " and ".join(strong)
                return f"A solid day overall — {strong_str} were standouts. Keep the momentum going."
            return "A solid day across the board. All four pillars are tracking well."
        elif score >= 60:
            if weak:
                weak_str = " and ".join(weak)
                return (
                    f"A decent day overall, with {weak_str} pulling the score down slightly. "
                    f"Nothing critical — just areas to watch."
                )
            return "A decent day overall — most metrics are near your baseline."
        else:
            if weak:
                weak_str = " and ".join(weak)
                return (
                    f"Today is below your usual — {weak_str} are both below par. "
                    f"Prioritize rest and fueling to bounce back tomorrow."
                )
            return "Overall health metrics are below your baseline today. Focus on recovery."

    # Fallback
    return f"Score: {score}."
