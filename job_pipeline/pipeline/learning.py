"""Decision history helpers for the learn skill.

Pattern analysis is now performed by the /job-pipeline:learn skill, which embeds
the learning prompt directly. This module retains the DB query helper and the
decision summariser so learn/prep.py can build the input JSON without duplicating logic.
"""
import json
from pathlib import Path

MIN_DECISIONS_FOR_PATTERN = 5


def _summarize_decision(row: dict) -> dict:
    """Compact view of one decided job -- just what the pattern-finder needs."""
    jd_extracted = row.get("jd_extracted")
    if isinstance(jd_extracted, str):
        try:
            jd_extracted = json.loads(jd_extracted) if jd_extracted else {}
        except json.JSONDecodeError:
            jd_extracted = {}
    jd_extracted = jd_extracted or {}
    return {
        "company": row.get("company"),
        "title": row.get("title"),
        "source": row.get("source"),
        "cv_category": row.get("cv_category"),
        "fit_score": row.get("fit_score"),
        "seniority": jd_extracted.get("seniority"),
        "years_required": jd_extracted.get("years_required"),
        "years_required_context": jd_extracted.get("years_required_context"),
        "decision": row.get("decision"),
        "outcome": row.get("outcome"),
    }


def run_learning(conn, notes_path: Path) -> None:
    """Legacy entry point kept for main.py compatibility. Directs the user to the skill."""
    from . import db
    rows = [dict(row) for row in db.jobs_with_decisions(conn)]
    print(f"[learning] {len(rows)} decided job(s) found.")
    print("[learning] Pattern analysis has moved to the /job-pipeline:learn skill.")
    print("[learning] Run that skill from Claude Code to regenerate calibration notes.")
