#!/usr/bin/env python3
"""
Lift Progression Report.

Pulls every working set across the DC program window, splits at the routine
shift date, and emits a markdown breakdown of how each lift has moved.

Usage:
  python scripts/lift_progression_report.py
  python scripts/lift_progression_report.py --start-date 2026-02-26 \
      --routine-shift 2026-05-07 --end-date 2026-05-19 \
      --out reports/lift_progression_20260519.md
"""

import argparse
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# Make sibling modules (tz.py) importable when run from any cwd.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tz import today  # noqa: E402

load_dotenv(ROOT / ".env")


SQL = """
SELECT date, session_id, session_title, exercise_name, muscle_group,
       set_index, reps, weight_lbs, rpe
FROM hevy_sets
WHERE date BETWEEN %s AND %s
  AND COALESCE(set_type, 'normal') != 'warmup'
  AND weight_lbs IS NOT NULL
  AND reps > 0
ORDER BY exercise_name, date, set_index;
"""


def get_conn():
    return psycopg2.connect(
        os.environ["DATABASE_URL"],
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def fmt_weight(w) -> str:
    """Whole number if integral, else 1 decimal."""
    f = float(w)
    if f == int(f):
        return str(int(f))
    return f"{f:.1f}"


def fmt_md(d: date) -> str:
    return f"{d.month}/{d.day}"


def fmt_delta(v) -> str:
    if v is None:
        return "—"
    f = float(v)
    sign = "+" if f >= 0 else "−"
    mag = abs(f)
    if mag == int(mag):
        return f"{sign}{int(mag)}"
    return f"{sign}{mag:.1f}"


def epley_1rm(weight, reps) -> int:
    return round(float(weight) * (1 + reps / 30))


def pick_top_set(sets):
    """Heaviest weight; tiebreak by reps."""
    return max(sets, key=lambda s: (float(s["weight_lbs"]), s["reps"]))


def best_e1rm(sets) -> int:
    return max(epley_1rm(s["weight_lbs"], s["reps"]) for s in sets)


def session_log_str(sessions):
    """sessions: list of (date, top_set) tuples, sorted by date."""
    parts = []
    for d, ts in sessions:
        parts.append(f"{fmt_md(d)} {fmt_weight(ts['weight_lbs'])}×{ts['reps']}")
    return ", ".join(parts)


def summarize_era(sessions):
    """sessions: list of (date, top_set) sorted by date. Returns dict or None."""
    if not sessions:
        return None
    first_d, first_ts = sessions[0]
    last_d, last_ts = sessions[-1]
    best_ts = pick_top_set([ts for _, ts in sessions])
    # Find date of best
    best_d = next(d for d, ts in sessions if ts is best_ts)
    best_e1 = max(epley_1rm(ts["weight_lbs"], ts["reps"]) for _, ts in sessions)
    first_e1 = epley_1rm(first_ts["weight_lbs"], first_ts["reps"])
    last_e1 = epley_1rm(last_ts["weight_lbs"], last_ts["reps"])
    n = len(sessions)
    if n == 1:
        dw = None
        de1 = None
    else:
        dw = float(last_ts["weight_lbs"]) - float(first_ts["weight_lbs"])
        de1 = last_e1 - first_e1
    return {
        "first_date": first_d,
        "first_ts": first_ts,
        "last_date": last_d,
        "last_ts": last_ts,
        "best_date": best_d,
        "best_ts": best_ts,
        "best_e1rm": best_e1,
        "first_e1rm": first_e1,
        "last_e1rm": last_e1,
        "sessions": n,
        "d_weight": dw,
        "d_e1rm": de1,
    }


# Baseline window: Block 1 weeks 2-4. Week 1 (2/26 - 3/4) was the
# acclimation / feel-out phase. By week 2 weights had settled into the
# rhythm Blake actually intended to progress from. Used to anchor the
# "Δ from baseline → now" line on each carryover card.
BASELINE_WINDOW_LO = date(2026, 3, 5)
BASELINE_WINDOW_HI = date(2026, 3, 25)


def baseline_session(entry):
    """First top set in the Block 1 W2–4 window (3/5–3/25).

    Returns (date, top_set) or None if the exercise had no sessions in
    the window (e.g. introduced later in Block 1, or Block 2 only).
    """
    in_window = sorted(
        [(d, ts) for d, ts in entry["era1"]
         if BASELINE_WINDOW_LO <= d <= BASELINE_WINDOW_HI],
        key=lambda x: x[0],
    )
    return in_window[0] if in_window else None


def full_arc(entry):
    """Cross-block 'started → now' arc using the earliest and latest sessions
    across both blocks. Different from summarize_era which is single-block."""
    all_sessions = sorted(entry["era1"] + entry["era2"], key=lambda x: x[0])
    if not all_sessions:
        return None
    first_d, first_ts = all_sessions[0]
    last_d, last_ts = all_sessions[-1]
    first_e1 = epley_1rm(first_ts["weight_lbs"], first_ts["reps"])
    last_e1 = epley_1rm(last_ts["weight_lbs"], last_ts["reps"])
    best_ts = pick_top_set([ts for _, ts in all_sessions])
    best_e1 = max(epley_1rm(ts["weight_lbs"], ts["reps"]) for _, ts in all_sessions)
    return {
        "first_date": first_d, "first_ts": first_ts, "first_e1rm": first_e1,
        "last_date":  last_d,  "last_ts":  last_ts,  "last_e1rm":  last_e1,
        "best_ts": best_ts, "best_e1rm": best_e1,
        "d_weight": float(last_ts["weight_lbs"]) - float(first_ts["weight_lbs"]),
        "d_e1rm": last_e1 - first_e1,
        "sessions": len(all_sessions),
    }


def prominence_tag(e1_count, e2_count, e1_total, e2_total):
    """Return ('share-string', 'tag') describing a carryover exercise's
    relative prominence between blocks. tag is None for steady lifts."""
    e1_share = (e1_count / e1_total) if e1_total else 0
    e2_share = (e2_count / e2_total) if e2_total else 0
    share_str = (f"{e1_count}/{e1_total} → {e2_count}/{e2_total} "
                 f"({e1_share*100:.0f}% → {e2_share*100:.0f}%)")
    if e1_share == 0 or e2_share == 0:
        return share_str, None
    ratio = e2_share / e1_share
    if ratio >= 1.5:
        return share_str, "promoted"
    if ratio <= 0.66:
        return share_str, "demoted"
    return share_str, None


def detect_data_flags(rows, per_ex):
    """Surface data-hygiene issues that distort an exercise's arc.

    Tuned for Blake's adaptation pattern — dramatic single-session weight
    jumps (50%+) happen and are legitimate; only ≥2× jumps are flagged as
    likely-anomalous. High-rep sets are not flagged at all; DC programming
    intentionally explores wide rep ranges.
    """
    flags = []

    # 1. Extreme single-session weight jumps within a block. Threshold tuned
    # to skip real adaptation and only catch machine/unit/logging mixups.
    for ex, entry in per_ex.items():
        for label, sessions in (("Block 1", entry["era1"]), ("Block 2", entry["era2"])):
            for i in range(1, len(sessions)):
                prev_d, prev_ts = sessions[i - 1]
                cur_d, cur_ts = sessions[i]
                prev_w = float(prev_ts["weight_lbs"])
                cur_w = float(cur_ts["weight_lbs"])
                if prev_w <= 0:
                    continue
                if cur_w / prev_w >= 2.0:
                    flags.append(
                        f"**{ex}** {label}: weight jumped "
                        f"`{fmt_weight(prev_w)}×{prev_ts['reps']}` ({fmt_md(prev_d)}) → "
                        f"`{fmt_weight(cur_w)}×{cur_ts['reps']}` ({fmt_md(cur_d)}) — "
                        f"likely a machine swap, unit/logging mixup, or load "
                        f"convention change (e.g. added-weight vs total load)."
                    )

    # 2. Assist-machine semantics. Always informational.
    for ex in sorted(per_ex):
        if "Assisted" in ex:
            flags.append(
                f"**{ex}** — Hevy stores the assist machine's load, not the "
                f"force you push/pull. Don't read it as a strength number."
            )

    # 3. Zero-weight sessions. Always a logging issue (machine without a
    # plate added, bodyweight-only entry, or feel-out session). Inflates
    # the arc dramatically when first-session weight is the divisor.
    for ex, entry in per_ex.items():
        for label, sessions in (("Block 1", entry["era1"]), ("Block 2", entry["era2"])):
            for d, ts in sessions:
                if float(ts["weight_lbs"]) == 0:
                    flags.append(
                        f"**{ex}** {label} {fmt_md(d)}: logged with zero added "
                        f"weight (`0×{ts['reps']}`) — bodyweight-only entry or "
                        f"missing load. Excluded from headline rankings."
                    )

    return flags


def build_per_exercise_per_era(rows, routine_shift):
    """
    Returns:
      per_ex: { exercise_name: {
          "muscle_group": str,
          "era1": [(date, top_set), ...],
          "era2": [(date, top_set), ...],
      }}
    """
    # Group rows by (exercise, date)
    by_ex_date = defaultdict(list)
    muscle_groups = {}
    for r in rows:
        by_ex_date[(r["exercise_name"], r["date"])].append(r)
        muscle_groups.setdefault(r["exercise_name"], r["muscle_group"])

    per_ex = {}
    for (ex, d), sets in by_ex_date.items():
        ts = pick_top_set(sets)
        entry = per_ex.setdefault(ex, {
            "muscle_group": muscle_groups[ex],
            "era1": [],
            "era2": [],
        })
        if d < routine_shift:
            entry["era1"].append((d, ts))
        else:
            entry["era2"].append((d, ts))

    for ex, entry in per_ex.items():
        entry["era1"].sort(key=lambda x: x[0])
        entry["era2"].sort(key=lambda x: x[0])
    return per_ex


def routine_fingerprint(rows, routine_shift):
    """Count distinct sessions per title within each era."""
    era1_sessions = {}  # title -> set of session_ids
    era2_sessions = {}
    for r in rows:
        title = r["session_title"] or "(untitled)"
        sid = r["session_id"]
        bucket = era1_sessions if r["date"] < routine_shift else era2_sessions
        bucket.setdefault(title, set()).add(sid)

    def to_sorted(d):
        return sorted(
            ((t, len(ids)) for t, ids in d.items()),
            key=lambda x: (-x[1], x[0]),
        )

    return to_sorted(era1_sessions), to_sorted(era2_sessions)


def count_sessions(rows, lo, hi):
    """Distinct session_ids with date in [lo, hi]."""
    return len({r["session_id"] for r in rows if lo <= r["date"] <= hi})


def render(rows, start_date, routine_shift, end_date):
    out = []
    add = out.append

    era1_end = routine_shift - timedelta(days=1)
    era1_days = (routine_shift - start_date).days
    era2_days = (end_date - routine_shift).days + 1
    total_days = (end_date - start_date).days + 1
    era1_sessions_n = count_sessions(rows, start_date, era1_end)
    era2_sessions_n = count_sessions(rows, routine_shift, end_date)

    add("# Lift Progression Report")
    add(f"**Window:** {start_date} → {end_date} ({total_days} days)")
    add(f"**Block 1 (Old DC):** {start_date} → {era1_end} "
        f"({era1_days} days, {era1_sessions_n} sessions)")
    add(f"**Block 2 (New DC):** {routine_shift} → {end_date} "
        f"({era2_days} days, {era2_sessions_n} sessions)")
    add("")

    per_ex = build_per_exercise_per_era(rows, routine_shift)

    carryover = {ex: e for ex, e in per_ex.items() if e["era1"] and e["era2"]}
    new_only = {ex: e for ex, e in per_ex.items() if not e["era1"] and e["era2"]}
    retired = {ex: e for ex, e in per_ex.items() if e["era1"] and not e["era2"]}

    # --- Headline: started → now ---
    add("## Headline — started → now")
    add("")
    add(f"Top movers across the {total_days}-day window, ranked by e1RM gain "
        f"from earliest working set to latest. Multi-session lifts only "
        f"(single-session lifts can't show progression yet).")
    add("")
    arcs = []
    for ex, entry in per_ex.items():
        arc = full_arc(entry)
        if not arc or arc["sessions"] < 2:
            continue
        first_w = float(arc["first_ts"]["weight_lbs"])
        last_w = float(arc["last_ts"]["weight_lbs"])
        # Skip zero-weight starts (bodyweight-only entry recorded as 0) — the
        # "0 → N" arc is a data artifact, not progression. Same for absurd
        # ratios that are almost certainly variant / unit changes.
        if first_w < 5:
            continue
        if first_w > 0 and last_w / first_w > 5:
            continue
        arcs.append((ex, entry["muscle_group"], arc))
    arcs.sort(key=lambda x: -x[2]["d_e1rm"])
    top_n = 8
    for i, (ex, mg, arc) in enumerate(arcs[:top_n], 1):
        first = f"{fmt_weight(arc['first_ts']['weight_lbs'])}×{arc['first_ts']['reps']} ({fmt_md(arc['first_date'])})"
        last = f"{fmt_weight(arc['last_ts']['weight_lbs'])}×{arc['last_ts']['reps']} ({fmt_md(arc['last_date'])})"
        add(f"{i}. **{ex}** ({mg or '—'}) — `{first}` → `{last}` · "
            f"**{fmt_delta(arc['d_e1rm'])} e1RM** · {arc['sessions']} sessions")
    add("")
    # Plateaus & regressions — anything with d_e1rm <= 0 that has >=3 sessions
    bottom = [(ex, mg, arc) for ex, mg, arc in arcs if arc["d_e1rm"] <= 0 and arc["sessions"] >= 3]
    if bottom:
        add("**Plateaus & regressions worth a look:**")
        add("")
        for ex, mg, arc in bottom:
            first = f"{fmt_weight(arc['first_ts']['weight_lbs'])}×{arc['first_ts']['reps']} ({fmt_md(arc['first_date'])})"
            last = f"{fmt_weight(arc['last_ts']['weight_lbs'])}×{arc['last_ts']['reps']} ({fmt_md(arc['last_date'])})"
            add(f"- **{ex}** — `{first}` → `{last}` · {fmt_delta(arc['d_e1rm'])} e1RM over {arc['sessions']} sessions")
        add("")

    # --- Data flags ---
    flags = detect_data_flags(rows, per_ex)
    if flags:
        add("## Data flags")
        add("")
        add("Surfaced so they don't read as performance signals. None of these are bugs in the report — they're quirks in how the underlying sessions got logged.")
        add("")
        for f in flags:
            add(f"- {f}")
        add("")

    # --- Routine fingerprint ---
    add("## Routine fingerprint")
    add("Top session titles per block (sanity check that the shift date is right):")
    add("")
    e1_titles, e2_titles = routine_fingerprint(rows, routine_shift)
    add("**Block 1**")
    add("")
    if not e1_titles:
        add("- (none)")
    for title, n in e1_titles:
        add(f"- {title} — {n} session{'s' if n != 1 else ''}")
    add("")
    add("**Block 2**")
    add("")
    if not e2_titles:
        add("- (none)")
    for title, n in e2_titles:
        add(f"- {title} — {n} session{'s' if n != 1 else ''}")
    add("")

    # Carryover sort: Block 2 sessions desc, muscle group asc, exercise asc
    carryover_sorted = sorted(
        carryover.items(),
        key=lambda kv: (-len(kv[1]["era2"]), kv[1]["muscle_group"] or "", kv[0]),
    )
    new_sorted = sorted(
        new_only.items(),
        key=lambda kv: (-len(kv[1]["era2"]), kv[1]["muscle_group"] or "", kv[0]),
    )
    retired_sorted = sorted(
        retired.items(),
        key=lambda kv: (-len(kv[1]["era1"]), kv[1]["muscle_group"] or "", kv[0]),
    )

    # --- Carryover ---
    add("## Carryover exercises (in both blocks)")
    add("")
    add(f"_{len(carryover_sorted)} exercises ran in both blocks. Share column shows what fraction of each block's sessions touched the lift — promoted/demoted tags flag big shifts in routine prominence._")
    add("")
    if not carryover_sorted:
        add("_(none)_")
        add("")
    for ex, entry in carryover_sorted:
        mg = entry["muscle_group"] or "—"
        e1 = summarize_era(entry["era1"])
        e2 = summarize_era(entry["era2"])
        share_str, tag = prominence_tag(
            e1["sessions"], e2["sessions"], era1_sessions_n, era2_sessions_n
        )
        tag_str = f" — **{tag}**" if tag else ""
        add(f"### {ex} ({mg})")
        add("")
        add(f"_Share: {share_str}{tag_str}_")
        add("")
        add("| Block | Top set | e1RM | Sessions |")
        add("| --- | --- | --- | --- |")
        baseline = baseline_session(entry)
        if baseline:
            b_d, b_ts = baseline
            b_w = float(b_ts["weight_lbs"])
            b_e1 = epley_1rm(b_w, b_ts["reps"])
            b_str = f"{fmt_weight(b_w)}×{b_ts['reps']} ({fmt_md(b_d)})"
            add(f"| Baseline (W2–4) | {b_str} | {b_e1} | — |")
        for label, s in (("Block 1", e1), ("Block 2", e2)):
            best = f"{fmt_weight(s['best_ts']['weight_lbs'])}×{s['best_ts']['reps']} ({fmt_md(s['best_date'])})"
            add(f"| {label} | {best} | {s['best_e1rm']} | {s['sessions']} |")
        add("")
        arc = full_arc(entry)
        first_str = (f"{fmt_weight(arc['first_ts']['weight_lbs'])}×{arc['first_ts']['reps']} "
                     f"({fmt_md(arc['first_date'])})")
        last_str  = (f"{fmt_weight(arc['last_ts']['weight_lbs'])}×{arc['last_ts']['reps']} "
                     f"({fmt_md(arc['last_date'])})")
        add(f"**Δ arc (started → now):** `{first_str}` → `{last_str}` · "
            f"{fmt_delta(arc['d_weight'])} lbs · {fmt_delta(arc['d_e1rm'])} e1RM")
        add("")
        add(f"Session log Block 1: {session_log_str(entry['era1'])}")
        add("")
        add(f"Session log Block 2: {session_log_str(entry['era2'])}")
        add("")

    # --- New ---
    add("## New exercises (Block 2 only)")
    add("")
    add(f"_{len(new_sorted)} exercises introduced in Block 2 — not enough data yet for a clean arc on most. Details collapsed by default._")
    add("")
    for ex, entry in new_sorted:
        mg = entry["muscle_group"] or "—"
        s = summarize_era(entry["era2"])
        add(f"### {ex} ({mg}) — {s['sessions']} session{'s' if s['sessions'] != 1 else ''}")
        add(f"Session log: {session_log_str(entry['era2'])}")
        first = f"{fmt_weight(s['first_ts']['weight_lbs'])}×{s['first_ts']['reps']} ({fmt_md(s['first_date'])})"
        last = f"{fmt_weight(s['last_ts']['weight_lbs'])}×{s['last_ts']['reps']} ({fmt_md(s['last_date'])})"
        best = f"{fmt_weight(s['best_ts']['weight_lbs'])}×{s['best_ts']['reps']}"
        add(f"First → Last: {first} → {last} | Best: {best} | Best e1RM: {s['best_e1rm']} | "
            f"Δ Weight: {fmt_delta(s['d_weight'])} | Δ e1RM: {fmt_delta(s['d_e1rm'])}")
        add("")

    # --- Retired ---
    add("## Retired exercises (Block 1 only)")
    add("")
    add(f"_{len(retired_sorted)} exercises that ran in Block 1 but were dropped from Block 2. The arc you finished on each. Details collapsed by default._")
    add("")
    for ex, entry in retired_sorted:
        mg = entry["muscle_group"] or "—"
        s = summarize_era(entry["era1"])
        add(f"### {ex} ({mg}) — {s['sessions']} session{'s' if s['sessions'] != 1 else ''}")
        add(f"Session log: {session_log_str(entry['era1'])}")
        first = f"{fmt_weight(s['first_ts']['weight_lbs'])}×{s['first_ts']['reps']} ({fmt_md(s['first_date'])})"
        last = f"{fmt_weight(s['last_ts']['weight_lbs'])}×{s['last_ts']['reps']} ({fmt_md(s['last_date'])})"
        best = f"{fmt_weight(s['best_ts']['weight_lbs'])}×{s['best_ts']['reps']}"
        add(f"First → Last: {first} → {last} | Best: {best} | Best e1RM: {s['best_e1rm']} | "
            f"Δ Weight: {fmt_delta(s['d_weight'])} | Δ e1RM: {fmt_delta(s['d_e1rm'])}")
        add("")

    return "\n".join(out).rstrip() + "\n"


def main():
    p = argparse.ArgumentParser(description="Lift progression report across DC eras")
    p.add_argument("--start-date", default="2026-02-26", help="DC program start (default: 2026-02-26)")
    p.add_argument("--routine-shift", default="2026-05-07",
                   help="Split between old and new DC routine (default: 2026-05-07)")
    p.add_argument("--end-date", default=None, help="End date (default: today())")
    p.add_argument("--out", default=None, help="Output markdown path")
    args = p.parse_args()

    start_date = parse_date(args.start_date)
    routine_shift = parse_date(args.routine_shift)
    end_date = parse_date(args.end_date) if args.end_date else today()

    if not (start_date < routine_shift <= end_date):
        sys.exit(f"Bad window: start={start_date} shift={routine_shift} end={end_date}")

    out_path = Path(args.out) if args.out else (
        ROOT / "reports" / f"lift_progression_{today().strftime('%Y%m%d')}.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SQL, (start_date, end_date))
            rows = cur.fetchall()

    if not rows:
        sys.exit("No working sets in window — check the date range and set_type filter.")

    md = render(rows, start_date, routine_shift, end_date)
    out_path.write_text(md)
    sys.stdout.write(md)
    sys.stderr.write(f"\nWrote {out_path}\n")


if __name__ == "__main__":
    main()
