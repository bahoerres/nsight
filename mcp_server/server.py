"""
Nsight MCP server.

Read-only access to the healthdash Postgres database via a curated tool
surface plus a SQL escape hatch. Designed to be launched over stdio.

Required environment:
  MCP_DATABASE_URL  postgresql://healthdash_ro:...@127.0.0.1:5432/healthdash
                    (must be a role with SELECT-only grants — see install_role.sql)
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP


# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

LOG_DIR = Path(os.environ.get("NSIGHT_MCP_LOG_DIR", str(Path.home() / ".nsight-mcp")))
LOG_DIR.mkdir(parents=True, exist_ok=True)

# FastMCP owns stdout for JSON-RPC. Log to file + stderr only.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "server.log"),
        logging.StreamHandler(sys.stderr),
    ],
)
log = logging.getLogger("nsight-mcp")
query_log = logging.getLogger("nsight-mcp.queries")
query_log.propagate = False
query_log.addHandler(logging.FileHandler(LOG_DIR / "queries.log"))
query_log.setLevel(logging.INFO)

DSN = os.environ.get("MCP_DATABASE_URL")
if not DSN:
    log.error("MCP_DATABASE_URL is not set. Refusing to start without a read-only DSN.")
    sys.exit(2)

mcp = FastMCP("nsight")


# --------------------------------------------------------------------------
# DB plumbing
# --------------------------------------------------------------------------


def get_conn():
    """Fresh connection per call. Sub-ms on localhost; no pool needed."""
    return psycopg2.connect(DSN, cursor_factory=psycopg2.extras.RealDictCursor)


def run_query(sql: str, params: tuple = ()) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            try:
                return cur.fetchall()
            except psycopg2.ProgrammingError:
                return []  # no result set (shouldn't happen for SELECT)


# --------------------------------------------------------------------------
# SQL safety — first layer (parser). The DB role is the real defense.
# --------------------------------------------------------------------------

_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


def validate_select(sql: str) -> str:
    """
    Reject anything that doesn't start with SELECT or WITH after stripping
    comments and whitespace. The healthdash_ro role would block mutations
    anyway, but failing fast gives a better error message and saves a
    round-trip.
    """
    cleaned = _COMMENT_RE.sub(" ", sql).strip().rstrip(";").strip()
    if not cleaned:
        raise ValueError("Empty query.")
    first = cleaned.split(None, 1)[0].upper()
    if first not in ("SELECT", "WITH"):
        raise ValueError(
            f"Only SELECT / WITH queries are allowed (got: {first!r}). "
            f"This server has read-only access by design."
        )
    return cleaned


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------


def _stringify(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, Decimal):
        f = float(v)
        return str(int(f)) if f == int(f) else f"{f:g}"
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, list):
        return "[" + ", ".join(_stringify(x) for x in v) + "]"
    s = str(v)
    return s.replace("|", "\\|").replace("\n", " ")


def to_markdown_table(rows: list[dict], max_rows: int = 50) -> str:
    if not rows:
        return "_(no rows)_"
    truncated = len(rows) > max_rows
    shown = rows[:max_rows]
    cols = list(shown[0].keys())
    out = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for r in shown:
        out.append("| " + " | ".join(_stringify(r.get(c)) for c in cols) + " |")
    suffix = f"\n\n_{len(rows)} rows total, showing first {max_rows}_" if truncated else f"\n\n_{len(rows)} row{'s' if len(rows) != 1 else ''}_"
    return "\n".join(out) + suffix


def fmt_weight(w) -> str:
    if w is None:
        return "—"
    f = float(w)
    return str(int(f)) if f == int(f) else f"{f:.1f}"


def fmt_md_date(d: date | datetime | None) -> str:
    if d is None:
        return "—"
    if isinstance(d, datetime):
        d = d.date()
    return f"{d.month}/{d.day}"


def epley_1rm(weight, reps) -> int:
    return round(float(weight) * (1 + reps / 30))


# --------------------------------------------------------------------------
# Resources
# --------------------------------------------------------------------------


@mcp.resource("nsight://schema")
def schema_resource() -> str:
    p = ROOT / "schema.sql"
    return p.read_text() if p.exists() else "(schema.sql not found)"


@mcp.resource("nsight://athlete-context")
def athlete_context_resource() -> str:
    p = ROOT / "athlete_context.txt"
    return p.read_text() if p.exists() else "(athlete_context.txt not found)"


@mcp.resource("nsight://conventions")
def conventions_resource() -> str:
    return CONVENTIONS_DOC


CONVENTIONS_DOC = """\
# Nsight querying conventions

## Working sets filter (non-negotiable)
Blake trains DC style — most sets are warmups. ALWAYS filter:

    AND COALESCE(set_type, 'normal') != 'warmup'

Failing to filter poisons every aggregate (top set, e1RM, volume, tonnage).

## DC program blocks
- Program start: 2026-02-26
- Routine shift: 2026-05-07 (old DC routine → new DC routine)
- Pre-2026-02-26 data is from a different training block; usually exclude.

## Top-set picking
Heaviest weight; tiebreak by reps. Use:
    ORDER BY weight_lbs DESC, reps DESC LIMIT 1

## e1RM (Epley)
    weight * (1 + reps / 30)

## Date filters
Use BETWEEN with explicit DATE bounds, e.g.
    date BETWEEN '2026-02-26' AND CURRENT_DATE

## Connection
Server uses MCP_DATABASE_URL — a SELECT-only role. INSERT/UPDATE/DELETE/DDL
will fail at the DB layer even if a query slips past the parser.
"""


# --------------------------------------------------------------------------
# Tools — schema / escape hatch
# --------------------------------------------------------------------------


@mcp.tool()
def list_tables() -> str:
    """List user tables in the healthdash DB with row counts and (where applicable) the date range covered."""
    tables = run_query(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name;
        """
    )
    out = ["| table | rows | date range |", "| --- | --- | --- |"]
    for t in tables:
        name = t["table_name"]
        try:
            count_row = run_query(f"SELECT COUNT(*) AS n FROM {name}")
            n = count_row[0]["n"]
        except Exception as e:
            out.append(f"| {name} | error: {e} | — |")
            continue
        has_date = run_query(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s AND column_name = 'date'
            LIMIT 1;
            """,
            (name,),
        )
        date_range = "—"
        if has_date:
            try:
                dr = run_query(f"SELECT MIN(date) AS lo, MAX(date) AS hi FROM {name}")
                if dr and dr[0]["lo"]:
                    date_range = f"{dr[0]['lo']} → {dr[0]['hi']}"
            except Exception:
                pass
        out.append(f"| {name} | {n} | {date_range} |")
    return "\n".join(out)


@mcp.tool()
def describe_table(name: str) -> str:
    """Show columns, types, nullability, and a sample row for one table."""
    # Validate the name to a safe identifier before interpolation.
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"Invalid table name: {name!r}")
    cols = run_query(
        """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position;
        """,
        (name,),
    )
    if not cols:
        return f"No table named `{name}` in schema `public`."
    sample = run_query(f"SELECT * FROM {name} LIMIT 1")
    out = [f"## `{name}`", "", "| column | type | nullable | default |", "| --- | --- | --- | --- |"]
    for c in cols:
        out.append(
            f"| {c['column_name']} | {c['data_type']} | "
            f"{'yes' if c['is_nullable'] == 'YES' else 'no'} | "
            f"{c['column_default'] or ''} |"
        )
    out.append("")
    out.append("**Sample row:**")
    out.append("")
    out.append(to_markdown_table(sample, max_rows=1) if sample else "_(empty table)_")
    return "\n".join(out)


@mcp.tool()
def query_sql(sql: str, limit: int = 500) -> str:
    """Run a read-only SQL query against the healthdash DB.

    Only SELECT and WITH (CTE) statements are accepted. Results are returned
    as a markdown table. The query is hard-capped at min(limit, 5000) rows
    and a 30-second server-side timeout. The healthdash_ro role enforces
    read-only at the DB layer — mutations will fail even if they pass the
    parser.

    Remember the working-sets filter for hevy_sets:
        AND COALESCE(set_type, 'normal') != 'warmup'
    """
    try:
        cleaned = validate_select(sql)
    except ValueError as e:
        return f"**Query rejected:** {e}"
    limit = max(1, min(int(limit), 5000))
    query_log.info("query_sql limit=%d sql=%s", limit, cleaned.replace("\n", " "))

    # Wrap so we cap rows regardless of caller's LIMIT clause.
    wrapped = f"SELECT * FROM ({cleaned}) AS __nsight_sub LIMIT {limit + 1}"
    try:
        rows = run_query(wrapped)
    except psycopg2.errors.InsufficientPrivilege as e:
        return f"**Permission denied** (read-only role): {e}"
    except psycopg2.Error as e:
        return f"**SQL error:** {type(e).__name__}: {e}"

    truncated_by_limit = len(rows) > limit
    rows = rows[:limit]
    body = to_markdown_table(rows, max_rows=min(50, limit))
    if truncated_by_limit:
        body += f"\n\n_(capped at limit={limit}; raise --limit if you need more)_"
    return body


# --------------------------------------------------------------------------
# Tools — workouts
# --------------------------------------------------------------------------


@mcp.tool()
def recent_sessions(days: int = 14) -> str:
    """List recent Hevy training sessions in the last N days with high-level stats and the heaviest working set per session."""
    rows = run_query(
        """
        WITH session_top AS (
          SELECT DISTINCT ON (date, session_id)
                 date, session_id,
                 exercise_name AS top_exercise,
                 weight_lbs    AS top_weight,
                 reps          AS top_reps
          FROM hevy_sets
          WHERE date >= CURRENT_DATE - %s::int
            AND COALESCE(set_type,'normal') != 'warmup'
            AND weight_lbs IS NOT NULL
          ORDER BY date, session_id, weight_lbs DESC, reps DESC
        ),
        session_agg AS (
          SELECT date, session_id, session_title,
                 COUNT(DISTINCT exercise_name) AS exercises,
                 COUNT(*) FILTER (WHERE COALESCE(set_type,'normal') != 'warmup') AS working_sets,
                 COUNT(*) FILTER (WHERE COALESCE(set_type,'normal') = 'warmup')  AS warmup_sets,
                 ROUND(SUM(weight_lbs * reps)
                       FILTER (WHERE COALESCE(set_type,'normal') != 'warmup')::numeric, 0)
                     AS working_tonnage_lbs,
                 array_agg(DISTINCT muscle_group) FILTER (WHERE muscle_group IS NOT NULL) AS muscle_groups
          FROM hevy_sets
          WHERE date >= CURRENT_DATE - %s::int
            AND weight_lbs IS NOT NULL
          GROUP BY date, session_id, session_title
        )
        SELECT a.date, a.session_id,
               COALESCE(a.session_title, '(untitled)') AS title,
               a.exercises, a.working_sets, a.warmup_sets,
               a.working_tonnage_lbs, a.muscle_groups,
               t.top_exercise, t.top_weight, t.top_reps
        FROM session_agg a
        LEFT JOIN session_top t
               ON t.date = a.date
              AND t.session_id IS NOT DISTINCT FROM a.session_id
        ORDER BY a.date DESC, a.session_id;
        """,
        (days, days),
    )
    if not rows:
        return f"_(no sessions in the last {days} days)_"
    out = [
        f"# Recent sessions — last {days} days",
        "",
        "| date | title | exercises | working sets | warmup sets | tonnage (lbs) | top set | muscles |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        mg = ", ".join(r["muscle_groups"] or [])
        if r["top_exercise"]:
            top = f"{fmt_weight(r['top_weight'])}×{r['top_reps']} {r['top_exercise']}"
        else:
            top = "—"
        out.append(
            f"| {r['date']} | {r['title']} | {r['exercises']} | {r['working_sets']} | "
            f"{r['warmup_sets']} | {fmt_weight(r['working_tonnage_lbs'])} | {top} | {mg} |"
        )
    return "\n".join(out)


@mcp.tool()
def lift_history(
    exercise: str,
    start: str | None = None,
    end: str | None = None,
    working_sets_only: bool = True,
) -> str:
    """Show progression for a single exercise: top set + e1RM per session.

    For each session (exercise_name, date) the top set is selected (heaviest
    weight, tiebreak by reps) and an Epley e1RM computed. Returns a session
    log plus first/last/best summary.

    `exercise` is matched case-insensitively with ILIKE — so 'bench press'
    matches 'Bench Press (Smith Machine)'. Use a more specific query if it
    matches more than you wanted.

    Defaults: start='2026-02-26' (DC program start), end=today,
    working_sets_only=True (warmup filter is on by default — flip only to
    audit raw data).
    """
    start_d = start or "2026-02-26"
    end_clause = "AND date <= %s::date" if end else ""
    warmup_clause = "AND COALESCE(set_type,'normal') != 'warmup'" if working_sets_only else ""
    params: tuple = (f"%{exercise}%", start_d) + ((end,) if end else ())
    rows = run_query(
        f"""
        WITH ranked AS (
          SELECT date, exercise_name, muscle_group, weight_lbs, reps,
                 ROW_NUMBER() OVER (
                   PARTITION BY exercise_name, date
                   ORDER BY weight_lbs DESC, reps DESC
                 ) AS rk
          FROM hevy_sets
          WHERE exercise_name ILIKE %s
            AND date >= %s::date
            {end_clause}
            {warmup_clause}
            AND weight_lbs IS NOT NULL
            AND reps > 0
        )
        SELECT date, exercise_name, muscle_group, weight_lbs, reps
        FROM ranked
        WHERE rk = 1
        ORDER BY exercise_name, date;
        """,
        params,
    )
    if not rows:
        return f"_(no matching sets for `{exercise}` between {start_d} and {end or 'today'})_"

    # Group by exercise (the ILIKE may match multiple).
    by_ex: dict[str, list[dict]] = {}
    for r in rows:
        by_ex.setdefault(r["exercise_name"], []).append(r)

    out = []
    for ex, ex_rows in by_ex.items():
        mg = ex_rows[0]["muscle_group"] or "—"
        log_str = ", ".join(
            f"{fmt_md_date(r['date'])} {fmt_weight(r['weight_lbs'])}×{r['reps']}"
            for r in ex_rows
        )
        first, last = ex_rows[0], ex_rows[-1]
        best = max(ex_rows, key=lambda r: (float(r["weight_lbs"]), r["reps"]))
        best_e1 = max(epley_1rm(r["weight_lbs"], r["reps"]) for r in ex_rows)
        first_e1 = epley_1rm(first["weight_lbs"], first["reps"])
        last_e1 = epley_1rm(last["weight_lbs"], last["reps"])
        n = len(ex_rows)
        d_w = float(last["weight_lbs"]) - float(first["weight_lbs"]) if n > 1 else None
        d_e1 = last_e1 - first_e1 if n > 1 else None

        def dfmt(v):
            if v is None:
                return "—"
            f = float(v)
            sign = "+" if f >= 0 else "−"
            mag = abs(f)
            return f"{sign}{int(mag) if mag == int(mag) else f'{mag:.1f}'}"

        out.append(f"### {ex} ({mg}) — {n} session{'s' if n != 1 else ''}")
        out.append(f"Session log: {log_str}")
        out.append(
            f"First: {fmt_weight(first['weight_lbs'])}×{first['reps']} ({fmt_md_date(first['date'])}) | "
            f"Last: {fmt_weight(last['weight_lbs'])}×{last['reps']} ({fmt_md_date(last['date'])}) | "
            f"Best: {fmt_weight(best['weight_lbs'])}×{best['reps']} ({fmt_md_date(best['date'])}) | "
            f"Best e1RM: {best_e1} | Δ Weight: {dfmt(d_w)} | Δ e1RM: {dfmt(d_e1)}"
        )
        out.append("")
    return "\n".join(out).rstrip()


@mcp.tool()
def muscle_group_volume(start: str, end: str, granularity: str = "week") -> str:
    """Working-set count and tonnage by muscle group, bucketed by week or day.

    `granularity` must be 'week' or 'day'. Warmups excluded.
    """
    if granularity not in ("week", "day"):
        raise ValueError("granularity must be 'week' or 'day'")
    rows = run_query(
        f"""
        SELECT date_trunc('{granularity}', date)::date AS bucket,
               COALESCE(muscle_group, '(unknown)') AS muscle_group,
               COUNT(*) AS working_sets,
               ROUND(SUM(weight_lbs * reps)::numeric, 0) AS tonnage_lbs
        FROM hevy_sets
        WHERE date BETWEEN %s::date AND %s::date
          AND COALESCE(set_type,'normal') != 'warmup'
          AND weight_lbs IS NOT NULL
        GROUP BY bucket, muscle_group
        ORDER BY bucket, muscle_group;
        """,
        (start, end),
    )
    return to_markdown_table(rows, max_rows=200)


# --------------------------------------------------------------------------
# Tools — daily metrics
# --------------------------------------------------------------------------


_DEFAULT_DAILY_COLS = [
    "date",
    "nsight_overall_score",
    "nsight_sleep_score",
    "nsight_recovery_score",
    "nsight_training_score",
    "nsight_nutrition_score",
    "sleep_total_sec",
    "hrv_nightly_avg",
    "resting_hr",
    "body_battery_eod",
    "hevy_total_volume_lbs",
    "hevy_total_sets",
    "crono_calories",
    "crono_protein_g",
]


@mcp.tool()
def daily_log(start: str, end: str, columns: list[str] | None = None) -> str:
    """Pull rows from daily_log in [start, end].

    Without `columns`, returns a curated default subset of nsight scores plus
    sleep / HRV / training / nutrition headline metrics. Pass an explicit list
    (must be real column names) to see anything else.
    """
    if columns is None:
        cols = _DEFAULT_DAILY_COLS
    else:
        cols = list(columns)
        for c in cols:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", c):
                raise ValueError(f"Invalid column name: {c!r}")
    cols_sql = ", ".join(cols)
    rows = run_query(
        f"SELECT {cols_sql} FROM daily_log WHERE date BETWEEN %s::date AND %s::date ORDER BY date;",
        (start, end),
    )
    return to_markdown_table(rows, max_rows=120)


@mcp.tool()
def today_status() -> str:
    """Snapshot for 'should I lift today?': last night's sleep, current recovery, training readiness, and whether a session has been logged today."""
    rows = run_query(
        """
        SELECT date,
               nsight_overall_score,
               nsight_sleep_score,
               nsight_recovery_score,
               nsight_training_score,
               nsight_nutrition_score,
               sleep_total_sec,
               sleep_score,
               hrv_nightly_avg,
               resting_hr,
               body_battery_eod,
               hevy_total_sets,
               hevy_total_volume_lbs
        FROM daily_log
        WHERE date >= CURRENT_DATE - 2
        ORDER BY date DESC
        LIMIT 3;
        """
    )
    today_row = run_query(
        """
        SELECT date,
               COALESCE(session_title, '(untitled)') AS title,
               COUNT(*) FILTER (WHERE COALESCE(set_type,'normal') != 'warmup') AS working_sets,
               ROUND(SUM(weight_lbs * reps)
                     FILTER (WHERE COALESCE(set_type,'normal') != 'warmup')::numeric, 0) AS tonnage
        FROM hevy_sets
        WHERE date = CURRENT_DATE
        GROUP BY date, session_title;
        """
    )
    out = ["# Today status"]
    if not rows:
        out.append("_(no daily_log rows in the last 3 days)_")
    else:
        out.append("")
        out.append("| date | overall | sleep | recovery | training | nutrition | sleep hr | HRV | RHR |")
        out.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for r in rows:
            sh = f"{r['sleep_total_sec']/3600:.1f}" if r["sleep_total_sec"] else "—"
            out.append(
                f"| {r['date']} | {fmt_weight(r['nsight_overall_score'])} | "
                f"{fmt_weight(r['nsight_sleep_score'])} | {fmt_weight(r['nsight_recovery_score'])} | "
                f"{fmt_weight(r['nsight_training_score'])} | {fmt_weight(r['nsight_nutrition_score'])} | "
                f"{sh} | {fmt_weight(r['hrv_nightly_avg'])} | {r['resting_hr'] or '—'} |"
            )
    out.append("")
    if today_row:
        for r in today_row:
            out.append(
                f"**Today ({r['date']}):** {r['title']} — {r['working_sets']} working sets, "
                f"{fmt_weight(r['tonnage'])} lbs tonnage"
            )
    else:
        out.append("**Today:** no Hevy session logged yet.")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------


def main():
    log.info("Starting nsight MCP server (DSN host=%s)", DSN.split("@")[-1] if "@" in DSN else "?")
    mcp.run()


if __name__ == "__main__":
    main()
