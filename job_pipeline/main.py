"""main.py — single entry point for the job search pipeline.

Commands
--------
    python main.py run              Full pipeline pass (discover → process → apply)
    python main.py discover         Pull new postings from ATS APIs + LinkedIn Gmail
    python main.py process          Extract, score, tailor, sync to Notion
    python main.py apply-approved   Submit applications for Notion-approved jobs only
    python main.py learn            Regenerate calibration notes from decision history
    python main.py status           Read-only DB summary (no API key required)

Legacy entry points (preserved, not yet removed):
    python seed_discoveries.py candidates.json   — hand off web-search results to the DB
    python learning_run.py                       — deprecated; use `python main.py learn`
"""
import argparse
import shutil
import sys
from pathlib import Path

# Force UTF-8 stdout/stderr on Windows so Rich can render Unicode
# symbols (✓ ✗ etc.) without hitting the cp1252 codec wall.
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv()

CONFIG_DIR = Path(__file__).resolve().parent / "config"
DATA_DIR = Path(__file__).resolve().parent / "data"


# ── helpers ───────────────────────────────────────────────────────────────────

def _snapshot_db(run_id: str) -> None:
    from pipeline.db import DB_PATH
    if not DB_PATH.exists():
        return
    snap_dir = DATA_DIR / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DB_PATH, snap_dir / f"pipeline_{run_id}.db")


def _load_run_context():
    """Load candidate profile, calibration notes, and applicant notes.

    Kept as a helper so run / process / apply-approved all read from the
    same config directory rather than duplicating the path logic.
    """
    from pipeline import apply as apply_module, scoring
    profile = scoring.load_candidate_profile(CONFIG_DIR / "cvs")
    notes = scoring.load_calibration_notes(CONFIG_DIR / "calibration_notes.md")
    applicant_notes = apply_module.load_applicant_notes(CONFIG_DIR / "applicant_notes.md")
    return profile, notes, applicant_notes


# ── commands ──────────────────────────────────────────────────────────────────

def cmd_run(args):
    """Full pipeline: discover → extract → score → tailor → sync → apply.

    Exact behavior of the previous `python main.py` invocation — no sequencing
    changes in this step (Step 3 only restructures the CLI).
    """
    from pipeline import orchestrator
    from pipeline.progress import RunProgress

    profile, notes, applicant_notes = _load_run_context()

    with RunProgress(log_dir=DATA_DIR / "logs") as progress:
        orchestrator.run_all(profile, notes, applicant_notes, progress=progress)
        _snapshot_db(progress.run_id)
        progress.info(f"DB snapshot saved → data/snapshots/pipeline_{progress.run_id}.db")
        progress.info(f"Log saved → data/logs/run_{progress.run_id}.log")


def cmd_discover(args):
    """Pull new job postings from ATS APIs + LinkedIn Gmail intake."""
    import os
    from pipeline import db, orchestrator
    from pipeline.progress import RunProgress

    with RunProgress(log_dir=DATA_DIR / "logs") as progress:
        conn = db.connect()
        try:
            progress.stage_start("discover")
            added = orchestrator.run_discovery(conn, progress)
            progress.stage_done("discover", count=added)
            _snapshot_db(progress.run_id)
        finally:
            conn.close()


def cmd_process(args):
    """Extract, score, tailor, and sync to Notion all pending jobs.

    Picks up jobs at 'discovered' (extract), 'extracted' (score), 'scored'
    (tailor), and 'tailored' (Notion sync) without re-processing anything
    that has already advanced past those states.
    """
    import os
    from pipeline import db, orchestrator
    from pipeline.progress import RunProgress

    profile, notes, _ = _load_run_context()
    cv_folder = Path(os.environ["CV_FOLDER_PATH"]) if os.environ.get("CV_FOLDER_PATH") else None
    output_dir = Path(os.environ.get("CV_OUTPUT_DIR", str(DATA_DIR / "tailored")))

    with RunProgress(log_dir=DATA_DIR / "logs") as progress:
        conn = db.connect()
        try:
            progress.stage_start("extract")
            orchestrator.run_extraction(conn, progress)
            progress.stage_done("extract")

            progress.stage_start("score")
            orchestrator.run_scoring(conn, profile, notes, progress)
            progress.stage_done("score")

            progress.stage_start("tailor")
            if cv_folder:
                orchestrator.run_tailoring(conn, cv_folder, output_dir, progress)
            else:
                progress.info("CV_FOLDER_PATH not set — skipping tailoring, jobs stay at 'scored'")
            progress.stage_done("tailor")

            progress.stage_start("sync")
            orchestrator.run_notion_sync(conn, progress)
            progress.stage_done("sync")

            _snapshot_db(progress.run_id)
        finally:
            conn.close()


def cmd_apply_approved(args):
    """Submit applications for jobs you have explicitly approved in Notion.

    Safety guarantees (unchanged from original apply.py):
    - Only acts on jobs with pipeline_status='synced' AND decision='approved'.
    - Real submission only fires when AUTO_SUBMIT_CONFIRMED=true in .env;
      otherwise fills the form and takes a screenshot for review.
    - Only acts on Greenhouse/Ashby/Lever jobs — LinkedIn/Indeed stay manual.
    """
    import os
    from pipeline import db, orchestrator
    from pipeline.progress import RunProgress

    _, _, applicant_notes = _load_run_context()
    screenshot_dir = Path(os.environ.get("APPLY_SCREENSHOT_DIR", str(DATA_DIR / "screenshots")))

    with RunProgress(log_dir=DATA_DIR / "logs") as progress:
        conn = db.connect()
        try:
            progress.stage_start("apply")
            orchestrator.run_apply(conn, screenshot_dir, progress, applicant_notes)
            progress.stage_done("apply")
            _snapshot_db(progress.run_id)
        finally:
            conn.close()


def cmd_learn(args):
    """Regenerate calibration notes from the full decision history (phase 6).

    Polls Notion for any new Status changes first, then reads every job you
    have approved or skipped (and any recorded outcomes) and rewrites
    config/calibration_notes.md. Requires at least 5 decided jobs; prints a
    notice and exits cleanly if fewer are available.
    """
    from pipeline import db, learning, notion_sync

    conn = db.connect()
    try:
        notion_sync.poll_decisions(conn)
        result = learning.run_learning(conn, CONFIG_DIR / "calibration_notes.md")
        if result:
            print(f"[learn] {result['summary']}")
    finally:
        conn.close()


# ── status ────────────────────────────────────────────────────────────────────

# Ordered list used for display — non-zero counts from unknown statuses are
# appended at the end so future/legacy values are never silently dropped.
_DISPLAY_STATUSES = [
    "discovered",
    "needs_jd",
    "extracted",
    "scored",
    "skipped_low_score",
    "shortlisted",
    "approved",
    "tailored",
    "ready_to_apply",
    "applying",
    "applied",
    "apply_failed",
    "failed",
    # legacy values written by pre-Step-2 code
    "synced",
    "ready_to_submit",
]


def cmd_status(args):
    """Read-only summary of the current pipeline DB state.

    Deliberately imports only pipeline.db so it never needs ANTHROPIC_API_KEY
    and makes zero external network calls.
    """
    from pipeline import db  # only db — no Anthropic-dependent modules

    conn = db.connect()
    try:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        counts = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT pipeline_status, COUNT(*) FROM jobs GROUP BY pipeline_status"
            )
        }

        print(f"\nJobs total: {total}")
        print("\nBy status:")
        for status in _DISPLAY_STATUSES:
            n = counts.get(status, 0)
            if n:
                print(f"  {status:<24} {n:>5}")

        # Print any status not in our known list (future extension / old data)
        known = set(_DISPLAY_STATUSES)
        for status, n in sorted(counts.items()):
            if status not in known and n:
                print(f"  {status:<24} {n:>5}  [unknown status]")

        # ── summary ──────────────────────────────────────────────────────────
        # "Pending human review" = shortlisted (new name) + synced (legacy name)
        pending_review = counts.get("shortlisted", 0) + counts.get("synced", 0)

        # Approved but not yet in a terminal or submission state
        approved_pending = conn.execute(
            "SELECT COUNT(*) FROM jobs "
            "WHERE decision = 'approved' AND pipeline_status != 'applied'"
        ).fetchone()[0]

        applied_count = counts.get("applied", 0)
        failed_retryable = counts.get("failed", 0) + counts.get("apply_failed", 0)

        print("\nSummary:")
        print(f"  Pending human review      {pending_review:>5}")
        print(f"  Approved, not yet applied {approved_pending:>5}")
        print(f"  Applied                   {applied_count:>5}")
        print(f"  Failed / retryable        {failed_retryable:>5}")
        print()
    finally:
        conn.close()


# ── argument parser ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Job search pipeline — automated discovery, scoring, and application.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
commands:
  run             Full pipeline pass (discover + process + apply-approved)
  discover        Pull new postings from ATS APIs and LinkedIn Gmail
  process         Extract, score, tailor, sync to Notion
  apply-approved  Submit applications for Notion-approved jobs (safety-gated)
  learn           Regenerate calibration notes from decision history
  status          Read-only DB summary — no API key required
""",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    sub.add_parser("run",            help="Full pipeline pass")
    sub.add_parser("discover",       help="Discover new job postings")
    sub.add_parser("process",        help="Extract, score, tailor, sync to Notion")
    sub.add_parser("apply-approved", help="Apply to Notion-approved jobs only (safety-gated)")
    sub.add_parser("learn",          help="Regenerate calibration notes from decision history")
    sub.add_parser("status",         help="Read-only DB summary (no API key required)")

    return parser


_HANDLERS: dict = {
    "run":            cmd_run,
    "discover":       cmd_discover,
    "process":        cmd_process,
    "apply-approved": cmd_apply_approved,
    "learn":          cmd_learn,
    "status":         cmd_status,
}


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    _HANDLERS[args.command](args)
