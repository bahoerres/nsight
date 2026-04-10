-- healthdash schema
-- one row per day, all sources joined on date
-- nulls are fine — not every source populates every day

CREATE TABLE IF NOT EXISTS daily_log (
    date            DATE PRIMARY KEY,

    -- garmin: sleep
    sleep_total_sec         INT,        -- total sleep in seconds
    sleep_deep_sec          INT,        -- deep sleep in seconds
    sleep_light_sec         INT,
    sleep_rem_sec           INT,
    sleep_awake_sec         INT,
    sleep_score             INT,        -- garmin's own 0-100 score
    sleep_start             TIMESTAMPTZ,
    sleep_end               TIMESTAMPTZ,

    -- garmin: heart & hrv
    hrv_nightly_avg         NUMERIC(5,1),   -- ms
    hrv_5min_low            NUMERIC(5,1),   -- lowest 5min reading of night
    resting_hr              INT,            -- bpm
    hr_max_day              INT,

    -- garmin: stress & body battery
    stress_avg              INT,            -- 0-100
    body_battery_eod        INT,            -- end of day, 0-100
    body_battery_max        INT,

    -- garmin: activity
    steps                   INT,
    active_calories         INT,
    total_calories          INT,
    floors_climbed          INT,
    intensity_minutes       INT,            -- moderate + vigorous combined
    vo2max                  NUMERIC(4,1),

    -- garmin: body composition
    body_weight_lbs         NUMERIC(5,1),   -- manual weigh-in from Garmin Connect

    -- garmin: respiration & spo2
    spo2_avg                NUMERIC(4,1),   -- percent
    respiration_avg         NUMERIC(4,1),   -- breaths per min

    -- hevy: training
    hevy_session_count      INT,            -- sessions on this date
    hevy_total_volume_lbs   NUMERIC(10,1),  -- sum of sets * reps * weight
    hevy_total_sets         INT,
    hevy_session_duration_min INT,
    hevy_muscle_groups      TEXT[],         -- e.g. {chest,triceps,shoulders}

    -- cronometer: nutrition
    crono_calories          NUMERIC(7,1),
    crono_protein_g         NUMERIC(6,1),
    crono_carbs_g           NUMERIC(6,1),
    crono_fat_g             NUMERIC(6,1),
    crono_fiber_g           NUMERIC(5,1),
    crono_sugar_g           NUMERIC(5,1),
    crono_sodium_mg         NUMERIC(7,1),
    crono_magnesium_mg      NUMERIC(6,1),
    crono_zinc_mg           NUMERIC(5,2),
    crono_vitamin_d_iu      NUMERIC(7,1),
    crono_potassium_mg      NUMERIC(7,1),
    crono_water_g           NUMERIC(7,1),
    crono_last_meal_time    TIME,           -- time of last logged meal
    crono_meal_count        INT,

    -- nsight computed scores (materialized by scoring.py)
    nsight_sleep_score      NUMERIC(5,1),
    nsight_recovery_score   NUMERIC(5,1),
    nsight_training_score   NUMERIC(5,1),
    nsight_nutrition_score  NUMERIC(5,1),
    nsight_overall_score    NUMERIC(5,1),

    -- metadata
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

-- separate table for hevy sets (granular, needed for ACWR and per-exercise trends)
CREATE TABLE IF NOT EXISTS hevy_sets (
    id              SERIAL PRIMARY KEY,
    date            DATE NOT NULL REFERENCES daily_log(date),
    session_id      TEXT,
    exercise_name   TEXT NOT NULL,
    muscle_group    TEXT,
    set_index       INT,
    reps            INT,
    weight_lbs      NUMERIC(6,1),
    rpe             NUMERIC(3,1),        -- rate of perceived exertion, if logged
    set_type        TEXT DEFAULT 'normal', -- normal, warmup, dropset, failure
    session_title   TEXT,                -- workout title from Hevy (e.g. "Upper C")
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- derived metrics table — populated by analysis scripts, not ingest
CREATE TABLE IF NOT EXISTS derived_daily (
    date            DATE PRIMARY KEY REFERENCES daily_log(date),

    -- acute:chronic workload ratio (7-day acute / 28-day chronic)
    acwr_volume     NUMERIC(4,2),
    acwr_intensity  NUMERIC(4,2),

    -- rolling baselines (90-day)
    hrv_baseline_90d        NUMERIC(5,1),
    hrv_delta_pct           NUMERIC(5,1),   -- % above/below baseline
    rhr_baseline_90d        NUMERIC(5,1),
    sleep_deep_baseline_90d INT,            -- seconds

    -- anomaly flags (MAD z-score, weekday/weekend stratified)
    hrv_anomaly             BOOLEAN,
    sleep_anomaly           BOOLEAN,
    stress_anomaly          BOOLEAN,

    updated_at  TIMESTAMPTZ DEFAULT now()
);

-- kahunas check-ins (store actuals so we can compare data vs subjective over time)
CREATE TABLE IF NOT EXISTS kahunas_checkins (
    id              SERIAL PRIMARY KEY,
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    submitted_at    TIMESTAMPTZ,

    -- the 1-10 scales from the form
    sleep_quality   INT CHECK (sleep_quality BETWEEN 1 AND 10),
    stress          INT CHECK (stress BETWEEN 1 AND 10),
    fatigue         INT CHECK (fatigue BETWEEN 1 AND 10),
    hunger          INT CHECK (hunger BETWEEN 1 AND 10),
    recovery        INT CHECK (recovery BETWEEN 1 AND 10),
    energy          INT CHECK (energy BETWEEN 1 AND 10),
    digestion       INT CHECK (digestion BETWEEN 1 AND 10),

    -- objective fields
    weight_lbs      NUMERIC(5,1),
    waist_in        NUMERIC(4,1),
    hrv_reported    NUMERIC(5,1),
    blood_glucose_breakfast NUMERIC(5,1),
    blood_glucose_lunch     NUMERIC(5,1),
    missed_meals    INT,

    -- data-suggested scores (what the dashboard recommended)
    suggested_sleep_quality INT,
    suggested_recovery      INT,
    suggested_energy        INT,
    suggested_stress        INT,
    suggested_fatigue       INT,

    notes           TEXT
);

-- AI-generated insights (daily/weekly/monthly summaries)
CREATE TABLE IF NOT EXISTS insights (
    id              SERIAL PRIMARY KEY,
    date            DATE NOT NULL,
    type            TEXT NOT NULL CHECK (type IN ('daily', 'weekly', 'monthly', 'correlation', 'sleep', 'recovery')),
    content         TEXT NOT NULL,
    model           TEXT,           -- which Claude model generated it
    prompt_version  TEXT,           -- for tracking prompt iterations
    tokens_used     INT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_insights_date_type ON insights(date, type);

CREATE INDEX IF NOT EXISTS idx_daily_log_date ON daily_log(date);
CREATE INDEX IF NOT EXISTS idx_hevy_sets_date ON hevy_sets(date);
CREATE INDEX IF NOT EXISTS idx_hevy_sets_exercise ON hevy_sets(exercise_name);
