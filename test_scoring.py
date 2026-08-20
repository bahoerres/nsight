"""Self-checks for the rules that broke under the PB3 switch.

Run: .venv/bin/python test_scoring.py
"""
from datetime import date, timedelta

from scoring import (
    ACWR_MIN_CHRONIC_SESSIONS,
    CARB_DAY_MATCH_TOLERANCE,
    HIGH_DAY,
    LOW_DAY,
    acwr_from_volume_by_date,
    compute_acwr,
    compute_nutrition_score,
)

TARGET = date(2026, 8, 20)


def _series(days_and_volumes):
    return {TARGET - timedelta(days=d): v for d, v in days_and_volumes}


class FakeCursor:
    def __init__(self, rows, sink):
        self.rows, self.sink = rows, sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.sink.append(params)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, rows):
        self.rows = rows if isinstance(rows, list) else [rows]
        self.params = []

    def cursor(self):
        return FakeCursor(self.rows, self.params)


def test_acwr_windows_do_not_overlap():
    # 9 chronic sessions of 7000 in days 7..27, 30000 across the acute week.
    vol = _series([(d, 7000.0) for d in range(7, 28, 2)] + [(0, 30000.0)])
    assert sum(1 for d in range(7, 28, 2)) == 11
    acwr = acwr_from_volume_by_date(vol, TARGET)
    # chronic = 11 * 7000 over 21 days -> /3 for a weekly figure
    assert abs(acwr - 30000.0 / (11 * 7000.0 / 3.0)) < 1e-9


def test_acwr_cannot_be_pinned_at_four():
    """The coupled form returned exactly 4.00 whenever all 28-day volume landed
    inside the acute window — Blake's real 2026-08-08..08-14 readings. The
    uncoupled form reports nothing, because there is nothing to compare to."""
    only_this_week = _series([(0, 15904.5), (2, 33060.0), (3, 24885.0)])
    assert acwr_from_volume_by_date(only_this_week, TARGET) is None


def test_acwr_guards_sparse_baseline():
    acute = [(0, 70000.0)]
    sparse = _series(acute + [(7 + i, 5000.0) for i in range(ACWR_MIN_CHRONIC_SESSIONS - 1)])
    assert acwr_from_volume_by_date(sparse, TARGET) is None
    enough = _series(acute + [(7 + i, 5000.0) for i in range(ACWR_MIN_CHRONIC_SESSIONS)])
    assert acwr_from_volume_by_date(enough, TARGET) is not None


def test_acwr_ignores_zero_volume_days():
    """Rest days sit in the map as 0.0 and must not count toward the baseline."""
    vol = _series([(0, 70000.0)] + [(d, 0.0) for d in range(7, 28)])
    assert acwr_from_volume_by_date(vol, TARGET) is None


def test_compute_acwr_reads_the_right_window():
    rows = [{"date": TARGET - timedelta(days=d), "hevy_total_volume_lbs": 5000.0}
            for d in range(7, 7 + ACWR_MIN_CHRONIC_SESSIONS)]
    rows.append({"date": TARGET, "hevy_total_volume_lbs": 20000.0})
    conn = FakeConn(rows)
    assert compute_acwr(conn, TARGET) is not None
    lo, hi = conn.params[0]
    assert (lo, hi) == (TARGET - timedelta(days=27), TARGET)


def _nutrition(carbs, sessions, calories=3000):
    row = {"crono_calories": calories, "crono_protein_g": 280, "crono_carbs_g": carbs,
           "crono_fiber_g": 30, "crono_sodium_mg": 4000, "hevy_session_count": sessions}
    return compute_nutrition_score(FakeConn(row), TARGET)["targets"]


def test_carb_target_follows_the_session_when_nothing_contradicts_it():
    assert _nutrition(None, 1)["carbs_g"] == HIGH_DAY[1]
    assert _nutrition(None, 0)["carbs_g"] == LOW_DAY[1]
    # TARGET is a Thursday — under the old 5/2 cycle both would have been low-carb.
    assert TARGET.weekday() == 3


def test_logged_carbs_override_the_session_on_a_swap():
    """Blake's real 2026-08-17 and 08-19: a high day on a rest day and a low day
    on a training day. The week was still exactly 4 high / 3 low, so neither is
    a miss."""
    high_on_rest = _nutrition(430.8, 0)
    assert high_on_rest["carbs_g"] == HIGH_DAY[1] and high_on_rest["high_day"]
    assert high_on_rest["training_day"] is False

    low_on_training = _nutrition(330.6, 1)
    assert low_on_training["carbs_g"] == LOW_DAY[1] and not low_on_training["high_day"]
    assert low_on_training["training_day"] is True


def test_a_genuine_miss_still_scores_against_the_session_target():
    """500g is 18% past the high target — matches neither, so it does not get to
    pick the target that flatters it."""
    assert _nutrition(500.0, 0)["carbs_g"] == LOW_DAY[1]
    assert _nutrition(200.0, 1)["carbs_g"] == HIGH_DAY[1]
    # A day stranded between the two bands also falls back to the session.
    stranded = (LOW_DAY[1] * (1 + CARB_DAY_MATCH_TOLERANCE)
                + HIGH_DAY[1] * (1 - CARB_DAY_MATCH_TOLERANCE)) / 2
    assert _nutrition(stranded, 1)["carbs_g"] == HIGH_DAY[1]
    assert _nutrition(stranded, 0)["carbs_g"] == LOW_DAY[1]


def test_the_two_carb_bands_never_overlap():
    """The override is only safe because a day cannot satisfy both targets."""
    assert LOW_DAY[1] * (1 + CARB_DAY_MATCH_TOLERANCE) < HIGH_DAY[1] * (1 - CARB_DAY_MATCH_TOLERANCE)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("all passed")
