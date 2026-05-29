---
created: 2026-05-19T17:41
updated: 2026-05-29T05:05
---
# Nsight MCP server — brief for claude-cowork

Read-only MCP access to Blake's health/training database (`healthdash` Postgres). The server lives on docker-top and is launched over SSH stdio.

## Setup

Wiring depends on the client.

- **Claude Code (CLI):** add the entry below to `~/.claude/mcp.json`. Already done on Blake's workstations (alongside Notion); backup at `~/.claude/mcp.json.bak.20260519-171004`.
- **CoWork desktop:** does **not** read `~/.claude/mcp.json`. Wire it via a packaged `.mcpb` extension installed through CoWork's UI (extensions live at `~/.config/Claude/Claude Extensions/<id>/`), or expose the server over HTTP and register as a custom connector. Either path uses the same `ssh docker-top ... python -m mcp_server` launch — what differs is how the client launches it.
- **Other clients (Cursor, Continue, etc.):** check the client's MCP config docs. The launch command is the same.

```json
{
  "mcpServers": {
    "nsight": {
      "command": "ssh",
      "args": ["docker-top", "cd /home/sysadmin/stacks/nsight && exec ./.venv/bin/python -m mcp_server"]
    }
  }
}
```

**Requires:** `ssh docker-top` works from the client's environment (key-based, BatchMode-friendly). If you're running in a sandboxed shell with no Tailscale/SSH, you won't be able to reach the server — flag this to Blake.

## Tools (read-only)

| Tool | When to use |
| --- | --- |
| `today_status()` | "Should I lift today?" — last night's sleep, current recovery score, training readiness, whether a session is logged today |
| `recent_sessions(days=14)` | "What have I been doing?" — list training sessions with sets/tonnage/muscles per session |
| `lift_history(exercise, start?, end?, working_sets_only=True)` | Per-exercise progression. `exercise` matches ILIKE — partial names work. Defaults to DC program start (2026-02-26) |
| `muscle_group_volume(start, end, granularity)` | Sets + tonnage per muscle group per week/day. Use for "am I shorting back day?" |
| `daily_log(start, end, columns?)` | Daily metrics: nsight scores, sleep/HRV/RHR, training volume, nutrition. Default columns are a curated subset; pass a list to widen |
| `list_tables()` / `describe_table(name)` | Schema exploration |
| `query_sql(sql, limit=500)` | Raw SELECT escape hatch. SELECT/WITH only; hard cap 5000 rows; 30s server-side timeout |

## Resources

- `nsight://schema` — full `schema.sql`
- `nsight://athlete-context` — Blake's training context (program, goals, equipment)
- `nsight://conventions` — querying conventions (the warmup filter, DC blocks, e1RM formula)

Read these once at the start of a session; they're cheap and prevent the most common query mistakes.

## Conventions (non-negotiable)

- **Working sets filter:** Blake trains DC style — most sets are warmups. Every set-level aggregate (top set, e1RM, volume, tonnage) MUST filter `AND COALESCE(set_type, 'normal') != 'warmup'`. The curated tools do this for you by default; if you flip `working_sets_only=False` or write your own SQL, it's on you.
- **DC program blocks:** Program start = 2026-02-26. Old DC → new DC routine shift = 2026-05-07. Pre-2026-02-26 data is a different training block; usually exclude.
- **Top-set picking:** Heaviest weight, tiebreak by reps (`ORDER BY weight_lbs DESC, reps DESC LIMIT 1`).
- **e1RM (Epley):** `weight × (1 + reps/30)`.

## Safety

- DB role is `healthdash_ro` with `SELECT`-only grants and `statement_timeout = '30s'`. Any INSERT/UPDATE/DELETE/DDL fails at the DB layer.
- `query_sql` rejects non-SELECT/WITH at the parser level too (defense in depth).
- Every `query_sql` call is logged on docker-top at `~/.nsight-mcp/queries.log` — assume your queries are auditable.

## Style

- Output is descriptive only. Don't editorialize ("great progress!") or compute PRs unless Blake asks.
- Show dates as `M/D` inline (`5/12`) and `YYYY-MM-DD` in headers.
- Don't normalize exercise names — show them exactly as Hevy stored them.
- Round e1RM to whole int; weight: int if whole, else 1 decimal.

## Known data quirks (observed 2026-05-19)

- A few legacy Hevy entries have units or rep-counts that look off (`Lat Pulldown (Machine)` jumping to `140×14` between 75-lb sessions; `Chin Up (Weighted)` showing `10×13`). Flag these to Blake rather than treating them as PRs.
- The `derived_daily` and `kahunas_checkins` tables are currently empty.
