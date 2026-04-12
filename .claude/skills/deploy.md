---
name: deploy
description: Commit, push, and deploy nsight changes to production — runs DB migrations, refreshes insights, restarts web service, and verifies health. Use when done with a round of edits and ready to ship.
user_invocable: true
---

# nsight Deploy Skill

Full commit-push-deploy-verify workflow for nsight. Invoked via `/deploy` when a round of edits is complete.

## Infrastructure Context

- **Server**: `docker-top` (SSH as `sysadmin`, Tailscale or local)
- **Remote path**: `/home/sysadmin/stacks/nsight/`
- **Web service**: systemd user service `nsight-web` (gunicorn on port 5100)
- **Database**: PostgreSQL `healthdash` on localhost:5432
- **CI**: GitHub Actions deploys on push to main (git pull + pip install + restart)
- **SSH host**: Use `docker-top` first; fall back to `docker-top-ts` (Tailscale)

## Workflow

Execute these steps in order. Stop and report if any step fails.

### Step 1: Commit

1. Run `git status` and `git diff --stat` to see what changed
2. Draft a concise commit message summarizing the changes (imperative mood, 1-2 lines)
3. Stage only the relevant files (never `git add -A` — skip `.env`, credentials, etc.)
4. Commit with:
   ```
   Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
   ```

### Step 2: Push

1. `git push origin main`
2. This triggers CI (`.github/workflows/deploy.yml`) which handles: git pull, pip install, restart nsight-web

### Step 3: Post-deploy via SSH

Wait ~15 seconds for CI to complete the git pull, then SSH to docker-top for post-deploy steps.

Use this SSH pattern for all commands:
```bash
ssh docker-top "cd /home/sysadmin/stacks/nsight && <command>"
```

If `ssh docker-top` fails (not on local network), retry with `ssh docker-top-ts`.

#### 3a: Verify code is current
```bash
ssh docker-top "cd /home/sysadmin/stacks/nsight && git log --oneline -1"
```
Confirm the commit hash matches what was just pushed.

#### 3b: Run DB migrations (if schema changed)
Only run this if `schema.sql`, `generate_insights.py` (the inline CREATE TABLE / ALTER TABLE), or any migration-relevant code was modified:
```bash
ssh docker-top "cd /home/sysadmin/stacks/nsight && .venv/bin/python -c \"
import psycopg2, os
from dotenv import load_dotenv
load_dotenv()
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute('''ALTER TABLE insights DROP CONSTRAINT IF EXISTS insights_type_check''')
cur.execute('''ALTER TABLE insights ADD CONSTRAINT insights_type_check CHECK (type IN (''daily'', ''weekly'', ''monthly'', ''correlation'', ''sleep'', ''recovery'', ''weekly_current'', ''monthly_current''))''')
conn.commit()
print('Migration OK')
conn.close()
\""
```

**Prefer this instead**: run the generate_insights.py script which includes the migration block in its startup:
```bash
ssh docker-top "cd /home/sysadmin/stacks/nsight && .venv/bin/python generate_insights.py --rolling"
```
This both migrates the schema AND generates rolling insights.

#### 3c: Generate rolling insights (if insight code changed)
If `generate_insights.py`, `athlete_context.txt`, or prompt-related code changed:
```bash
ssh docker-top "cd /home/sysadmin/stacks/nsight && .venv/bin/python generate_insights.py --rolling"
```

#### 3d: Restart web service (if CI didn't already, or to be safe)
```bash
ssh docker-top "systemctl --user restart nsight-web"
```

### Step 4: Verify

```bash
ssh docker-top "systemctl --user status nsight-web --no-pager"
```
Confirm: `active (running)`. Report the status to the user.

## Flags / Variations

- **`--skip-insights`**: Skip step 3c (rolling insight generation) — useful for pure UI changes
- **`--full-ingest`**: Also trigger a full data ingest after deploy:
  ```bash
  ssh docker-top "cd /home/sysadmin/stacks/nsight && bash nsight-ingest"
  ```
- **`--migrate-only`**: Skip commit/push, just run steps 3b-4 (for applying migrations to an already-deployed codebase)

## Decision Logic

Determine which post-deploy steps are needed based on what files changed:

| Files changed | Actions needed |
|---|---|
| `schema.sql`, `generate_insights.py` (schema section) | DB migration (3b) |
| `generate_insights.py`, `athlete_context.txt` | Rolling insights (3c) |
| `app.py`, `templates/*`, `static/*` | Restart only (3d) — CI handles this |
| `scoring.py`, `materialize_scores.py` | Full ingest recommended |
| `ingest_*.py` | Full ingest recommended |
| Only docs/config | Push only, no post-deploy |

## Error Handling

- If SSH fails: suggest the user check their network/Tailscale connection
- If migration fails: show the error, do NOT retry blindly — the user needs to review
- If insight generation fails: check if it's an API key issue or a data issue, report clearly
- If service won't start: show `journalctl --user -u nsight-web -n 20 --no-pager` output
