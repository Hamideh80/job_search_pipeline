"""Discovery stage: pull new postings from all ATS sources, then apply
relevance + availability filters. Updates the DB directly — no JSON handoff needed."""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / "job_pipeline" / ".env", override=False)
load_dotenv(_PROJECT_ROOT / ".env", override=False)

from job_pipeline.pipeline import db, orchestrator
from job_pipeline.pipeline.progress import RunProgress

_LOG_DIR = _PROJECT_ROOT / "job_pipeline" / "data" / "logs"

with RunProgress(log_dir=_LOG_DIR) as progress:
    conn = db.connect()
    try:
        added = orchestrator.run_discovery(conn, progress)
        orchestrator.run_relevance_filter(conn, progress)
        orchestrator.run_availability_check(conn, progress)
        remaining = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE pipeline_status = 'discovered'"
        ).fetchone()[0]
        progress.info(f"Discovery done: {added} new job(s), {remaining} ready for extraction")
    finally:
        conn.close()
