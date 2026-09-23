"""DEPRECATED — use `python main.py learn` instead.

This script is preserved so that any external cron job or scheduled task
that calls `python learning_run.py` continues to work during the migration
to the unified CLI. Once you have verified `python main.py learn`, you may
remove this file.

----

Phase 6 entry point -- the learning loop.

    python learning_run.py   # deprecated; prefer: python main.py learn

Run this on its own, slower cadence (e.g. weekly, once you've accumulated a
batch of new decisions/outcomes in Notion) -- separate from main.py, which
you'd run much more often (e.g. daily) for discovery/scoring/tailoring/apply.

What it does:
1. Polls Notion for any Status changes since the last run (same poll
   main.py already does, so this also catches decisions if you haven't run
   main.py in a while).
2. Reads every job you've explicitly approved or skipped, plus any
   recorded outcome (Interview / Rejected / Offer).
3. Asks Claude to look for real patterns in that history and rewrites
   config/calibration_notes.md accordingly -- which pipeline/scoring.py
   already reads on every score call, so nothing else needs to change for
   the scorer to start using what's learned here.

Safe to run repeatedly: with fewer than 5 decided jobs, it prints a notice
and leaves your calibration notes untouched rather than guessing from too
little data.
"""
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from pipeline import db, learning, notion_sync  # noqa: E402  (import after load_dotenv)

CONFIG_DIR = Path(__file__).resolve().parent / "config"

if __name__ == "__main__":
    conn = db.connect()
    try:
        notion_sync.poll_decisions(conn)
        learning.run_learning(conn, CONFIG_DIR / "calibration_notes.md")
    finally:
        conn.close()
