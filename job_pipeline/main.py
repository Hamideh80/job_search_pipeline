"""Entry point.

    python main.py

Loads your candidate profile (the three base CVs) and calibration notes
(empty until phase 6 exists), then runs one full pipeline pass: discover ->
extract -> score -> sync to Notion. Safe to run on a schedule (cron /
GitHub Actions) -- dedup means a re-run only picks up what's new.
"""
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from pipeline import orchestrator, scoring  # noqa: E402  (import after load_dotenv)

CONFIG_DIR = Path(__file__).resolve().parent / "config"

if __name__ == "__main__":
    profile = scoring.load_candidate_profile(CONFIG_DIR / "cvs")
    notes = scoring.load_calibration_notes(CONFIG_DIR / "calibration_notes.md")
    orchestrator.run_all(profile, notes)
