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

CI completes in ~30 seconds (git pull + pip install + restart nsight-web). Rather than sleeping, **let CI handle code sync and restart**, then SSH only for what CI doesn't do.

Use this SSH pattern for all commands:
```bash
ssh docker-top "cd /home/sysadmin/stacks/nsight && <command>"
```

If `ssh docker-top` fails (not on local network), retry with `ssh docker-top-ts`.

#### 3a: Wait for CI, then verify code is current
Poll until the remote HEAD matches the pushed commit (CI typically finishes within 30s):
```bash
EXPECTED=$(git rev-parse --short HEAD)
for i in 1 2 3 4 5; do
  REMOTE=$(ssh docker-top "cd /home/sysadmin/stacks/nsight && git rev-parse --short HEAD" 2>/dev/null)
  [ "$REMOTE" = "$EXPECTED" ] && break
  sleep 8
done
```
If it doesn't match after ~40 seconds, fall back to manual pull:
```bash
ssh docker-top "cd /home/sysadmin/stacks/nsight && git pull origin main"
```

#### 3b: Run DB migrations (if schema changed)
Only needed if `schema.sql` or `generate_insights.py` (inline CREATE TABLE / ALTER TABLE) changed.

Preferred: run `generate_insights.py` which includes the migration block at startup:
```bash
ssh docker-top "cd /home/sysadmin/stacks/nsight && .venv/bin/python generate_insights.py --rolling"
```
This both migrates the schema AND generates rolling insights (combines 3b + 3c).

#### 3c: Generate rolling insights (if insight code changed)
If `generate_insights.py`, `athlete_context.txt`, or prompt-related code changed:
```bash
ssh docker-top "cd /home/sysadmin/stacks/nsight && .venv/bin/python generate_insights.py --rolling"
```

#### 3d: Restart web service
CI already restarts nsight-web. Only do this manually if CI didn't run or you need a second restart after migrations:
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
