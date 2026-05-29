"""Convert the Lift Progression artifact HTML into a print-friendly PDF.

Reads the current HTML produced by lift_bridge.py at /tmp/lift_progression.html
(the bridge's canonical output), forces all <details> elements open for print,
appends a small print stylesheet, then runs headless Chromium to render PDF.

Output lands at ~/code/nsight/reports/Lift-Progression_Report_<YYYYMMDD>.pdf.

Run after the bridge has updated /tmp/lift_progression.html.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

SRC_HTML = Path("/tmp/lift_progression.html")
PRINT_HTML = Path("/tmp/lift_progression_print.html")
OUT_DIR = Path("/home/blake/code/nsight/reports")
OUT_PDF = OUT_DIR / f"Lift-Progression_Report_{date.today():%Y%m%d}.pdf"

if not SRC_HTML.exists():
    sys.exit(f"Source HTML not found: {SRC_HTML}. Run lift_bridge.py first.")

OUT_DIR.mkdir(parents=True, exist_ok=True)

src = SRC_HTML.read_text()

# Force all <details> elements open so every exercise card and section
# appears in the print output.
src = re.sub(r"<details(?![^>]*\bopen\b)", "<details open", src)

# Page setup + print-friendly tweaks.
PRINT_CSS = """
<style>
@page { size: letter; margin: 0.55in 0.55in 0.65in 0.55in; }
@media print {
  body { padding: 0; }
  details > summary::before,
  details.section > summary.section-summary::before { content: ""; }
  details, details.section { break-inside: avoid; }
  table { break-inside: avoid; }
  h2, h3 { break-after: avoid; }
  .meta-bar { background: #f6f7f9 !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  tr:nth-child(even) td { background: #fafbfc !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  th { background: #f6f7f9 !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
}
</style>
"""
src = src.replace("</head>", PRINT_CSS + "</head>")

PRINT_HTML.write_text(src)
print(f"Wrote print HTML: {PRINT_HTML} ({len(src):,} chars)", file=sys.stderr)

cmd = [
    "chromium",
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--no-pdf-header-footer",
    f"--print-to-pdf={OUT_PDF}",
    f"file://{PRINT_HTML}",
]
print(f"Running headless chromium…", file=sys.stderr)
cp = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
if cp.returncode != 0 and not OUT_PDF.exists():
    print(f"chromium stderr:\n{cp.stderr}", file=sys.stderr)
    sys.exit(cp.returncode)

print(f"\nWrote PDF: {OUT_PDF} ({OUT_PDF.stat().st_size:,} bytes)", file=sys.stderr)
