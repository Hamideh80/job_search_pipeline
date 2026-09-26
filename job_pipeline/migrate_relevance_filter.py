"""One-time relevance filter migration — DO NOT COMMIT.

Applies Gate 1 (title blocklist) + Gate 2 (title + JD signals) to all
existing jobs at pipeline_status='discovered'.

  PASS  → status unchanged (stays 'discovered')
  FAIL  → pipeline_status='skipped_irrelevant', reason stored in
           relevance_skip_reason

Nothing is deleted. All changes are auditable and visible in the DB.
Safe to re-run: jobs already at a non-'discovered' status are skipped.

Review the printed table BEFORE the changes commit. The script writes
changes immediately as it goes, so review the output and stop (Ctrl-C)
if the filter behaviour looks wrong.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from pipeline import db
from pipeline.relevance import is_relevant


def _truncate(s: str, n: int = 55) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s[:n] + "…" if len(s) > n else s


def main():
    conn = db.connect()
    rows = conn.execute(
        "SELECT id, company, title, jd_raw FROM jobs WHERE pipeline_status = 'discovered'"
    ).fetchall()

    print(f"\n{'='*100}")
    print(f"RELEVANCE FILTER MIGRATION — {len(rows)} discovered jobs")
    print(f"{'='*100}")
    hdr = f"{'ID':>5}  {'Gate':<6}  {'Company':<14}  {'Title':<42}  Reason"
    print(hdr)
    print("-" * 100)

    kept = skipped = 0
    for row in rows:
        job_id = row["id"]
        title  = row["title"]  or ""
        jd_raw = row["jd_raw"] or ""
        co     = row["company"] or ""

        ok, reason = is_relevant(title, jd_raw)
        gate = reason.split(":")[0] if ":" in reason else "gate2"

        if ok:
            kept += 1
            print(f"{job_id:>5}  {'PASS':<6}  {co[:14]:<14}  {_truncate(title, 42):<42}")
        else:
            skipped += 1
            print(f"{job_id:>5}  {'SKIP':<6}  {co[:14]:<14}  {_truncate(title, 42):<42}  {_truncate(reason, 55)}")
            db.update_job(conn, job_id,
                          pipeline_status="skipped_irrelevant",
                          relevance_skip_reason=reason)

    print(f"\n{'='*100}")
    print(f"SUMMARY")
    print(f"{'='*100}")
    print(f"  Total discovered    : {len(rows)}")
    print(f"  Kept (discovered)   : {kept}")
    print(f"  Skipped (irrelevant): {skipped}  ({100*skipped//max(len(rows),1)}% filtered)")
    print()

    # Count by gate
    gate1 = sum(1 for r in rows
                if not is_relevant(r["title"] or "", r["jd_raw"] or "")[0]
                and is_relevant(r["title"] or "", r["jd_raw"] or "")[1].startswith("gate1"))
    gate2 = skipped - gate1
    print(f"  Gate 1 (title)   : {gate1} blocked")
    print(f"  Gate 2 (title+JD): {gate2} blocked")

    conn.close()
    print()


if __name__ == "__main__":
    main()
