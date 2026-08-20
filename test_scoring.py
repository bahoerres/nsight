"""Self-checks for the two rules that broke under the PB3 switch.

Run: .venv/bin/python test_scoring.py
"""
from datetime import date

from scoring import ACWR_MIN_CHRONIC_SESSIONS, compute_acwr, compute_nutrition_score


class FakeCursor:
    """Records params, replays a canned row."""

    def __init__(self, row, sink):
        self.row, self.sink = row, sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.sink.append(params)

    def fetchone(self):
        return self.row


class FakeConn:
    def __init__(self, row):
        self.row, self.params = row, []

    def cursor(self):
        return FakeCursor(self.row, self.params)


TARGET = date(2026, 8, 20)


def test_acwr_windows_do_not_overlap():
    conn = FakeConn({"acute_vol": 30000, "chronic_vol": 63000, "chronic_sessions": 9})
    acwr = compute_acwr(conn, TARGET)
    acute_start, acute_end, chronic_start, chronic_end, _, _ = conn.params[0]
    assert (acute_start, acute_end) == (date(2026, 8, 14), TARGET)
    assert (chronic_start, chronic_end) == (date(2026, 7, 24), date(2026, 8, 13))
    # Uncoupled: acute must end after chronic does, and never sit inside it.
    assert chronic_end < acute_start
    # 63000 over 21 days = 21000/week; 30000/21000
    assert abs(acwr - 30000 / 21000) < 1e-9


def test_acwr_cannot_be_pinned_at_four():
    """The old coupled form returned exactly 4.00 whenever all 28-day volume
    landed inside the acute window. The uncoupled form reports nothing."""
    conn = FakeConn({"acute_vol": 73849.5, "chronic_vol": 0, "chronic_sessions": 0})
    assert compute_acwr(conn, TARGET) is None


def test_acwr_guards_sparse_baseline():
    sparse = {"acute_vol": 70000, "chronic_vol": 26230,
              "chronic_sessions": ACWR_MIN_CHRONIC_SESSIONS - 1}
    assert compute_acwr(FakeConn(sparse), TARGET) is None
    enough = dict(sparse, chronic_sessions=ACWR_MIN_CHRONIC_SESSIONS)
    assert compute_acwr(FakeConn(enough), TARGET) is not None


def test_carb_target_follows_the_session_not_the_weekday():
    base = {"crono_calories": 3000, "crono_protein_g": 280, "crono_carbs_g": 400,
            "crono_fiber_g": 30, "crono_sodium_mg": 4000}
    trained = compute_nutrition_score(FakeConn(dict(base, hevy_session_count=1)), TARGET)
    rested = compute_nutrition_score(FakeConn(dict(base, hevy_session_count=0)), TARGET)
    assert trained["targets"] == {"calories": 3360.0, "protein_g": 280.0,
                                  "carbs_g": 425.0, "training_day": True}
    assert rested["targets"] == {"calories": 2960.0, "protein_g": 280.0,
                                 "carbs_g": 325.0, "training_day": False}
    # TARGET is a Thursday — under the old 5/2 cycle both would have been "low-carb".
    assert TARGET.weekday() == 3


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("all passed")
