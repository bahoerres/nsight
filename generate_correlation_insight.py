#!/usr/bin/env python3
"""
Generate a plain-English correlation insight and store it in the insights table.
Runs nsight's correlation analysis, sends findings to Claude for interpretation.

Usage:
  python generate_correlation_insight.py          # generate if not already done today
  python generate_correlation_insight.py --force  # regenerate even if exists
"""

import argparse
import os
import sys
from datetime import date

import anthropic
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-sonnet-4-5-20241022"
PROMPT_VERSION = "correlation-v1"
MAX_TOKENS = 1024

# Load athlete context if available
_CONTEXT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "athlete_context.txt"
)
ATHLETE_CONTEXT = ""
if os.path.exists(_CONTEXT_PATH):
    with open(_CONTEXT_PATH) as f:
        ATHLETE_CONTEXT = f.read().strip()


def get_conn():
    return psycopg2.connect(
        os.environ["DATABASE_URL"],
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def insight_exists(conn, target_date, insight_type="correlation"):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM insights WHERE date = %s AND type = %s LIMIT 1",
            (target_date, insight_type),
        )
        return cur.fetchone() is not None


def build_correlation_prompt(results):
    """Build a prompt from correlation findings for Claude to interpret."""
    findings = results.get("findings", [])
    exploratory = results.get("exploratory", [])

    lines = []
    if ATHLETE_CONTEXT:
        lines.append(f"Athlete context:\n{ATHLETE_CONTEXT}\n")

    lines.append("Here are the significant correlation findings from my health data analysis:")
    lines.append(f"Total tests run: {results.get('total_tests', 0)}")
    lines.append(f"Significant (FDR-corrected): {results.get('significant_count', 0)}")
    lines.append("")

    if findings:
        lines.append("## Significant Correlations (FDR-corrected)")
        for f in findings:
            lines.append(f"- {f['interpretation']} (r={f['r']:.3f}, p={f.get('p_corrected', f.get('p', 0)):.4f}, lag={f['lag']}d, n={f['n']})")
        lines.append("")

    if exploratory:
        lines.append("## Exploratory Findings (uncorrected p < 0.05)")
        for f in exploratory:
            lines.append(f"- {f['interpretation']} (r={f['r']:.3f}, p={f.get('p', 0):.4f}, lag={f['lag']}d, n={f['n']})")
        lines.append("")

    if not findings and not exploratory:
        lines.append("No significant or exploratory correlations found in the current dataset.")
        lines.append("")

    lines.append("""Write a 3-5 sentence plain-English summary of what these correlations mean for my training and recovery. Focus on:
1. The most actionable finding — what should I do differently or keep doing?
2. Any surprising relationships and what might explain them
3. Limitations or caveats (small sample, confounders, correlation ≠ causation)

Be specific, reference the actual numbers, and keep it practical. No headers or bullet points — just flowing prose.""")

    return "\n".join(lines)


def generate_correlation_insight(conn, target_date, force=False):
    if not force and insight_exists(conn, target_date):
        print(f"  Correlation insight already exists for {target_date}, skipping.")
        return False

    # Import and run the correlation analysis
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from app import run_correlations_for_display
    results = run_correlations_for_display()

    if results.get("error"):
        print(f"  Correlation analysis error: {results['error']}")
        return False

    prompt = build_correlation_prompt(results)

    # Call API
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        insight_text = message.content[0].text
        tokens_used = message.usage.input_tokens + message.usage.output_tokens
    except Exception as e:
        print(f"  API error: {e}")
        return False

    # Ensure 'correlation' type is allowed
    with conn.cursor() as cur:
        cur.execute("""
            DO $$
            BEGIN
                ALTER TABLE insights DROP CONSTRAINT IF EXISTS insights_type_check;
                ALTER TABLE insights ADD CONSTRAINT insights_type_check
                    CHECK (type IN ('daily', 'weekly', 'monthly', 'correlation', 'sleep', 'recovery'));
            EXCEPTION WHEN others THEN NULL;
            END $$;
        """)
    conn.commit()

    # Store
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO insights (date, type, content, model, prompt_version, tokens_used)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (target_date, "correlation", insight_text, MODEL, PROMPT_VERSION, tokens_used),
        )
    conn.commit()
    print(f"  Generated correlation insight for {target_date} ({tokens_used} tokens)")
    return True


def main():
    parser = argparse.ArgumentParser(description="Generate correlation insight")
    parser.add_argument("--force", action="store_true", help="Regenerate even if exists")
    args = parser.parse_args()

    conn = get_conn()
    try:
        today = date.today()
        generate_correlation_insight(conn, today, force=args.force)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
