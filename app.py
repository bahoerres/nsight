"""nsight — personal health intelligence dashboard."""

import os
from datetime import datetime

import markdown
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from flask import Flask, render_template, send_from_directory
from markupsafe import Markup
from zoneinfo import ZoneInfo

load_dotenv()

app = Flask(__name__)

LOCAL_TZ = ZoneInfo(os.environ.get("TZ", "America/Chicago"))

# ── Database ────────────────────────────────────────────────────────

def get_db():
    """Return a psycopg2 connection with RealDictCursor."""
    conn = psycopg2.connect(
        os.environ["DATABASE_URL"],
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    return conn


# ── Template helpers ────────────────────────────────────────────────

@app.template_filter("md")
def md_filter(text):
    """Render markdown string to safe HTML."""
    if not text:
        return ""
    return Markup(markdown.markdown(text, extensions=["extra", "nl2br"]))


@app.context_processor
def inject_globals():
    """Make active_page available in all templates."""
    return {}


# ── Static PWA routes ──────────────────────────────────────────────

@app.route("/sw.js")
def service_worker():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")


@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json", mimetype="application/json")


# ── Pages ───────────────────────────────────────────────────────────

@app.route("/")
def home():
    now = datetime.now(LOCAL_TZ)
    hour = now.hour
    if hour < 12:
        greeting = "Good morning, Blake"
    elif hour < 17:
        greeting = "Good afternoon, Blake"
    else:
        greeting = "Good evening, Blake"

    today_str = now.strftime("%A, %B %-d, %Y")

    return render_template(
        "home.html",
        active_page="home",
        greeting=greeting,
        today_str=today_str,
    )


# ── Run ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(port=5100, debug=True)
