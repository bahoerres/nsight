# Sync Button + Insight Carousel Design

## Overview

Two features for nsight:
1. A sidebar sync button that triggers data ingest + score refresh
2. An insight carousel embedded in the homepage score row, with regenerate buttons on both the homepage and insights archive page

## API Endpoints

### POST /api/ingest

Spawns `nsight-ingest` (without insight generation) as a background subprocess. Returns `202 {"status": "started"}`.

- **Lockfile**: `/tmp/nsight-ingest.lock` — created on start, removed on completion
- **Cooldown**: Rejects with `409` if lockfile exists and is less than 5 minutes old
- **Concurrent guard**: If lockfile exists and process is still running, returns `409 {"error": "already_running"}`

### POST /api/generate-insight

Accepts JSON body `{"type": "daily"}` (or `"weekly"`, `"monthly"`).

Runs `generate_insights.py --{type} --force` as a background subprocess. Returns `202`.

- **Separate lockfile**: `/tmp/nsight-insight.lock`
- Same cooldown/concurrency pattern as ingest

### GET /api/ingest/status

Returns `{"running": true/false, "last_run": "2026-04-08T05:00:00"}` by checking lockfile existence and mtime. Frontend polls this every 2 seconds while a sync is active.

### GET /api/insight/{type}

Returns the latest insight for the given type: `{"date": "2026-04-08", "type": "daily", "content": "..."}`. Used by the carousel to swap content in-place after regeneration without a full page reload.

## Sidebar Sync Button

Located at the bottom of the sidebar nav in `base.html`, pushed down with `margin-top: auto`.

### States

- **Default**: Rotating-arrows sync icon, 20px, `var(--text-secondary)`, same visual weight as nav icons
- **Loading**: Icon spins via CSS `@keyframes` animation
- **Complete**: Spinner stops, toast appears

### Behavior

1. Click fires `POST /api/ingest`
2. Icon starts spinning
3. Frontend polls `GET /api/ingest/status` every 2 seconds
4. On completion: stop spinner, show toast
5. On `409` response: toast shows "Sync already running" or "Last sync was less than 5 minutes ago"

## Toast Notification

A shared, reusable toast component used by both sync and insight regeneration.

- Fixed position, bottom-right of viewport
- Gruvbox-themed: `var(--bg-elevated)` background, `var(--border)` border
- Auto-dismisses after 4 seconds
- Shows success ("Sync complete") or failure ("Sync failed") with appropriate color accent
- One toast visible at a time — new toasts replace the current one

## Insight Carousel (Homepage)

Embedded as a second row inside the existing `.scores-row-inner` card on the home page.

### Layout

- **Row 1**: Existing 4 score rings + weight sparkline (unchanged)
- **Divider**: `border-top: 1px solid var(--border-subtle)`
- **Row 2**: Insight carousel — full width of the card

### Content

- Backend queries the most recent insight for each type (daily, weekly, monthly)
- All three insights are rendered into the HTML (hidden), JS toggles visibility
- Navigation wraps around: daily → weekly → monthly → daily

### Navigation

- Small left/right arrow buttons on either side of the insight text
- Arrows semi-transparent, full opacity on hover (always visible on mobile)
- Footer line: type badge + date + regenerate icon, e.g., "Daily Insight — Apr 8, 2026"

### Regenerate

- Small refresh icon next to the date in the footer
- On click: `POST /api/generate-insight` with current type
- Spinner on the icon while running
- On completion: `GET /api/insight/{type}` to fetch new content, swap text in-place
- Toast confirms "Insight regenerated"

### Empty State

If no insights exist, show: "No insights yet — run a sync to get started."

## Insights Archive Page — Regenerate Button

- Refresh icon button inline with the tab headers (Daily | Weekly | Monthly)
- On click: `POST /api/generate-insight` with active tab type
- Spinner while running
- On completion: page reloads to show fresh insight at top of list

## Backend: nsight-ingest Modification

The sidebar sync runs ingest without insight generation. Either:
- Add a `--no-insights` flag to the `nsight-ingest` script that skips the "Daily insight" step (lines 98-104)
- Or create a separate lighter script

Recommendation: Add `--no-insights` flag to the existing script. Minimal change, single entry point.

## Systemd Timer Simplification

With the manual sync button available, simplify the timer from 4 runs to 1:

```ini
[Timer]
OnCalendar=*-*-* 10:00:00
Persistent=true
```

Single daily run at 10:00 UTC (5:00 AM CDT). Manual button covers any missed syncs.

## Files Modified

- `app.py` — New API routes, insight carousel data queries
- `templates/base.html` — Sidebar sync button
- `templates/home.html` — Carousel row inside scores card, JS for navigation/regeneration
- `templates/insights.html` — Regenerate button on tab header
- `static/style.css` — Toast styles, carousel styles, sync button styles
- `static/app.js` — Toast component, sync polling, carousel navigation, regenerate logic
- `nsight-ingest` — `--no-insights` flag
- `systemd/nsight-ingest.timer` — Simplify to single daily run
