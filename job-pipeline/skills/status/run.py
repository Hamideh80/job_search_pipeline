"""Read-only DB summary — no API key required."""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_PROJECT_ROOT))

from job_pipeline.pipeline import db

_DISPLAY_STATUSES = [
    "discovered", "extracted",
    "skipped_irrelevant", "skipped_low_score", "skipped_language_requirement", "skipped_closed",
    "shortlisted", "skipped_human", "approved", "tailored",
    "ready_to_apply", "applying", "applied", "apply_failed", "failed",
    "synced", "ready_to_submit",  # legacy
]

conn = db.connect()
try:
    total  = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT pipeline_status, COUNT(*) FROM jobs GROUP BY pipeline_status"
    )}

    print(f"\nJobs total: {total}\n\nBy status:")
    for status in _DISPLAY_STATUSES:
        n = counts.get(status, 0)
        if n:
            print(f"  {status:<30} {n:>5}")
    known = set(_DISPLAY_STATUSES)
    for status, n in sorted(counts.items()):
        if status not in known and n:
            print(f"  {status:<30} {n:>5}  [unknown]")

    pending_review  = counts.get("shortlisted", 0) + counts.get("synced", 0)
    approved_pending = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE decision='approved' AND pipeline_status != 'applied'"
    ).fetchone()[0]

    print(f"\nSummary:")
    print(f"  Pending human review        {pending_review:>5}")
    print(f"  Approved, not yet applied   {approved_pending:>5}")
    print(f"  Applied                     {counts.get('applied', 0):>5}")
    print(f"  Failed / retryable          {counts.get('failed', 0) + counts.get('apply_failed', 0):>5}")
    print()
finally:
    conn.close()
