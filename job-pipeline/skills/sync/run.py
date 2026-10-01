"""Notion sync stage: create dashboard cards for shortlisted jobs, then
poll for human approve/skip decisions. Updates the DB directly."""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / "job_pipeline" / ".env", override=False)
load_dotenv(_PROJECT_ROOT / ".env", override=False)

from job_pipeline.pipeline import db, notion_sync

conn = db.connect()
n_synced = n_failed = 0
try:
    rows = conn.execute(
        "SELECT * FROM jobs WHERE pipeline_status = 'shortlisted' AND notion_page_id IS NULL"
    ).fetchall()

    for row in rows:
        try:
            page_id = notion_sync.create_job_page(dict(row))
            db.update_job(conn, row["id"], notion_page_id=page_id)
            n_synced += 1
        except Exception as exc:
            print(f"[sync] job {row['id']} ({row['company']} — {row['title']}): {exc}")
            n_failed += 1

    notion_sync.poll_decisions(conn)

    n_approved = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE decision = 'approved'"
    ).fetchone()[0]
    n_pending = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE pipeline_status = 'shortlisted' AND decision IS NULL"
    ).fetchone()[0]
    n_human_skipped = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE decision = 'skipped'"
    ).fetchone()[0]

    print(f"[sync] {n_synced} page(s) created" + (f", {n_failed} failed" if n_failed else ""))
    print(f"[sync] decisions (all time): {n_approved} approved, "
          f"{n_human_skipped} skipped by human, {n_pending} pending review")
finally:
    conn.close()
