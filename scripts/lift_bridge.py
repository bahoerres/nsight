"""Lift-progression bridge.

Drives the canonical scripts/lift_progression_report.py on docker-top via SSH,
captures its markdown output, renders a self-contained light-mode HTML doc,
and publishes that HTML to the ts-proxy Caddy host.

  Inputs:
    /home/blake/code/nsight/scripts/lift_progression_report.py  (source of truth)
  Outputs:
    /tmp/lift_progression.md         — fresh markdown from docker-top stdout
    /tmp/lift_progression.html       — artifact-ready HTML
    ts-proxy:/var/www/nsight/index.html  — live web copy (Caddy serves it)

Idempotent: re-running re-syncs the script (cheap), re-runs it (writes a fresh
dated report on docker-top under /home/sysadmin/stacks/nsight/reports/), and
overwrites the local md/html plus the published copy.

Env vars:
    NSIGHT_SKIP_PUBLISH=1   — skip the ts-proxy scp step (useful for local-only
                              testing or when ts-proxy is unreachable).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import markdown as md_lib  # python-markdown


# --- Config -------------------------------------------------------------

LOCAL_SCRIPT = Path("/home/blake/code/nsight/scripts/lift_progression_report.py")
REMOTE_DIR = "/home/sysadmin/stacks/nsight"
REMOTE_SCRIPT = f"{REMOTE_DIR}/scripts/lift_progression_report.py"

OUT_MD = Path("/tmp/lift_progression.md")
OUT_HTML = Path("/tmp/lift_progression.html")

# Publish target — ts-proxy is the Caddy host. /var/www/nsight is group-
# writable by the caddy group (sysadmin is a member), with the sgid bit set
# so new files inherit group=caddy and Caddy can read them.
# Caddy's site stanza for progress.blakehoerres.com rewrites all requests
# to `lift_progression_artifact.html`, so that's the filename we must write
# even though a more conventional name would be `index.html`.
PUBLISH_HOST = "ts-proxy"
PUBLISH_REMOTE_PATH = "/var/www/nsight/lift_progression_artifact.html"

# DC program window — used only for the meta-bar header. The report script
# uses these as its own defaults.
START_DATE = date(2026, 2, 26)
ROUTINE_SHIFT = date(2026, 5, 7)


# --- Remote run ---------------------------------------------------------


def sync_script() -> None:
    subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "docker-top", f"mkdir -p {REMOTE_DIR}/scripts"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["scp", "-q", "-o", "BatchMode=yes",
         str(LOCAL_SCRIPT), f"docker-top:{REMOTE_SCRIPT}"],
        check=True,
    )


def run_remote_report() -> str:
    cp = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "docker-top",
         f"cd {REMOTE_DIR} && exec ./.venv/bin/python scripts/lift_progression_report.py"],
        check=True, capture_output=True, text=True, timeout=60,
    )
    return cp.stdout


def publish_html(local_html: Path) -> None:
    """scp the rendered HTML to the Caddy host. Overwrites — idempotent."""
    cp = subprocess.run(
        ["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
         str(local_html), f"{PUBLISH_HOST}:{PUBLISH_REMOTE_PATH}"],
        capture_output=True, text=True, timeout=30,
    )
    if cp.returncode != 0:
        raise RuntimeError(
            f"scp to {PUBLISH_HOST}:{PUBLISH_REMOTE_PATH} failed "
            f"(exit {cp.returncode}): {cp.stderr.strip() or 'no stderr'}"
        )


# --- HTML rendering -----------------------------------------------------


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Lift Progression</title>
<style>
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 24px 28px 40px;
  font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  color: #111;
  background: transparent;
}
.meta-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 18px;
  align-items: baseline;
  padding: 10px 14px;
  margin: 0 0 22px;
  background: #f6f7f9;
  border: 1px solid #e3e5ea;
  border-radius: 6px;
  font-size: 13px;
  color: #444;
}
.meta-bar .label { color: #6b7280; }
.meta-bar .refreshed { margin-left: auto; font-variant-numeric: tabular-nums; }
h1 {
  font-size: 22px;
  font-weight: 600;
  margin: 0 0 4px;
  letter-spacing: -0.01em;
}
h2 {
  font-size: 17px;
  font-weight: 600;
  margin: 28px 0 10px;
  padding-bottom: 6px;
  border-bottom: 1px solid #e3e5ea;
}
h3 {
  font-size: 15px;
  font-weight: 600;
  margin: 20px 0 8px;
  color: #1f2937;
}
p { margin: 6px 0; }
strong { font-weight: 600; }
em { color: #6b7280; }
ul { margin: 4px 0 10px; padding-left: 22px; }
li { margin: 1px 0; }
table {
  border-collapse: collapse;
  margin: 6px 0 14px;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  width: 100%;
}
th, td {
  border: 1px solid #e3e5ea;
  padding: 6px 10px;
  text-align: left;
  vertical-align: top;
}
th {
  background: #f6f7f9;
  font-weight: 600;
  color: #374151;
}
tr:nth-child(even) td { background: #fafbfc; }
.session-log {
  font-variant-numeric: tabular-nums;
  color: #4b5563;
  font-size: 13px;
}
details {
  margin: 14px 0 18px;
  border: 1px solid #e3e5ea;
  border-radius: 6px;
  background: #fff;
  padding: 0;
}
details > summary {
  cursor: pointer;
  padding: 10px 14px;
  font-weight: 600;
  font-size: 14px;
  list-style: none;
  user-select: none;
}
details > summary::-webkit-details-marker { display: none; }
details > summary::before {
  content: "▸ ";
  display: inline-block;
  width: 14px;
  color: #6b7280;
  transition: transform 0.15s;
}
details[open] > summary::before { content: "▾ "; }
details > .inner { padding: 0 14px 8px; }

/* Section-level collapsibles (New / Retired) look like section headers,
   not exercise cards. Sit flush with the page rather than inside a card. */
details.section {
  margin: 28px 0 18px;
  border: none;
  background: transparent;
  padding: 0;
}
details.section > summary.section-summary {
  padding: 0 0 6px;
  border-bottom: 1px solid #e3e5ea;
  font-size: 17px;
  font-weight: 600;
}
details.section > summary.section-summary::before {
  content: "▸ ";
  color: #6b7280;
  font-size: 14px;
}
details.section[open] > summary.section-summary::before { content: "▾ "; }
details.section > .section-inner { padding: 10px 0 0; }

/* Carryover prominence + block-delta lines */
.share-line {
  font-size: 13px;
  color: #4b5563;
  margin: 0 0 8px;
}
.share-line .share-label { color: #6b7280; }
.delta-line {
  font-size: 13px;
  color: #1f2937;
  margin: 4px 0 10px;
  font-variant-numeric: tabular-nums;
}

/* Headline list — punchy and well-spaced */
h2:first-of-type + p + ol > li { margin: 6px 0; line-height: 1.55; }

@media (max-width: 600px) {
  body { padding: 16px 14px 30px; }
  table { font-size: 12px; }
}
</style>
</head>
<body>
<div class="meta-bar">
  <span><span class="label">Window:</span> __WINDOW__</span>
  <span><span class="label">Block split:</span> __SHIFT__</span>
  <span><span class="label">Source:</span> nsight · canonical report script</span>
  <span class="refreshed"><span class="label">Refreshed:</span> __REFRESHED__</span>
</div>
__BODY__
</body>
</html>
"""


def render_html(md_text: str, end_date: date) -> str:
    body = md_lib.markdown(md_text, extensions=["tables", "sane_lists"])

    # Hoist the H1 + window paragraphs into the meta-bar.
    body = re.sub(
        r"<h1>Lift Progression Report</h1>\s*"
        r"(?:<p>(?:<strong>(?:Window|Block 1|Block 2)[^<]*</strong>[^<]*)+</p>\s*){1,3}",
        "",
        body,
        count=1,
    )

    # Style session-log paragraphs as quieter text.
    body = re.sub(
        r"<p>(Session log(?: Block [12])?: [^<]+)</p>",
        r'<p class="session-log">\1</p>',
        body,
    )

    # Wrap each H3 (exercise heading) and its trailing content in a card,
    # up to the next H3 or H2. Use details/summary for collapse.
    # We need this only inside the H2 sections.
    def wrap_exercise_block(match: re.Match) -> str:
        h3 = match.group(1)
        inner = match.group(2).strip()
        return (
            f'<details open>\n'
            f'<summary>{strip_tags(h3)}</summary>\n'
            f'<div class="inner">{inner}</div>\n'
            f'</details>'
        )

    body = re.sub(
        r"<h3>(.*?)</h3>\s*(.*?)(?=<h[23]>|$)",
        wrap_exercise_block,
        body,
        flags=re.DOTALL,
    )

    # Wrap the New + Retired H2 sections in collapsed-by-default <details>
    # so the heavy inventory tables don't take over the page. The headline
    # and carryover sections stay open (they're the high-signal content).
    def wrap_section(label_match: str) -> None:
        nonlocal body
        body = re.sub(
            rf"<h2>({label_match}[^<]*)</h2>\s*(.*?)(?=<h2>|$)",
            lambda m: (
                f'<details class="section">\n'
                f'<summary class="section-summary"><span class="h2-text">{m.group(1)}</span></summary>\n'
                f'<div class="section-inner">{m.group(2)}</div>\n'
                f'</details>'
            ),
            body,
            count=1,
            flags=re.DOTALL,
        )

    wrap_section("New exercises")
    wrap_section("Retired exercises")

    # Style the "Share:" prominence line + Δ block-over-block paragraph quieter.
    body = re.sub(
        r"<p><em>Share: ([^<]+)</em></p>",
        r'<p class="share-line"><span class="share-label">Share:</span> \1</p>',
        body,
    )
    body = re.sub(
        r"<p>(<strong>Δ arc[^<]*</strong>.*?)</p>",
        r'<p class="delta-line">\1</p>',
        body,
        flags=re.DOTALL,
    )

    return (HTML_TEMPLATE
            .replace("__BODY__", body)
            .replace("__WINDOW__", f"{START_DATE} → {end_date}")
            .replace("__SHIFT__", f"{ROUTINE_SHIFT} (Old DC → New DC)")
            .replace("__REFRESHED__", datetime.now().strftime("%Y-%m-%d %H:%M")))


def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


# --- Main ---------------------------------------------------------------


def main() -> int:
    print("Syncing report script to docker-top…", file=sys.stderr)
    sync_script()
    print("Running lift_progression_report.py on docker-top…", file=sys.stderr)
    md = run_remote_report()
    if not md.strip():
        print("Empty report from remote. Aborting.", file=sys.stderr)
        return 1
    OUT_MD.write_text(md)
    print(f"Wrote {OUT_MD} ({len(md):,} chars, {md.count(chr(10))} lines).", file=sys.stderr)

    html = render_html(md, date.today())
    OUT_HTML.write_text(html)
    print(f"Wrote {OUT_HTML} ({len(html):,} chars).", file=sys.stderr)

    if os.environ.get("NSIGHT_SKIP_PUBLISH"):
        print(f"Skipping publish ({PUBLISH_HOST}) — NSIGHT_SKIP_PUBLISH set.", file=sys.stderr)
    else:
        print(f"Publishing to {PUBLISH_HOST}:{PUBLISH_REMOTE_PATH}…", file=sys.stderr)
        publish_html(OUT_HTML)
        print("  Published.", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
