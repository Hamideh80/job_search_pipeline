"""Write extraction results from the skill back to the pipeline DB.

Reads: job_pipeline/data/pending/extract_results.json
  [{id, extracted: {must_haves, nice_to_haves, years_required, ...}}, ...]

Updates each job: jd_extracted = JSON, pipeline_status = 'extracted'
"""
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_PROJECT_ROOT))
_PENDING_DIR = _PROJECT_ROOT / "job_pipeline" / "data" / "pending"

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / "job_pipeline" / ".env", override=False)
load_dotenv(_PROJECT_ROOT / ".env", override=False)

from job_pipeline.pipeline import db

results_path = _PENDING_DIR / "extract_results.json"
if not results_path.exists():
    print("[extract-commit] extract_results.json not found — nothing to commit")
    sys.exit(1)

results = json.loads(results_path.read_text(encoding="utf-8"))
conn = db.connect()
n_ok = n_err = 0
try:
    for item in results:
        job_id = item.get("id")
        extracted = item.get("extracted")
        if not extracted or not isinstance(extracted, dict):
            print(f"[extract-commit] job {job_id}: missing/invalid extracted field — skipping")
            n_err += 1
            continue
        db.update_job(conn, job_id, jd_extracted=extracted, pipeline_status="extracted")
        n_ok += 1
finally:
    conn.close()

results_path.unlink()
print(f"[extract-commit] {n_ok} committed, {n_err} error(s)")
