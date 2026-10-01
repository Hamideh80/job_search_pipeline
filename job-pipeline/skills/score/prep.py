"""Export 'extracted' jobs + candidate profile for the score skill.

Output: job_pipeline/data/pending/score_input.json
  {candidate_profile, calibration_notes, jobs: [{id, title, company, jd_extracted}]}
"""
import json
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_PROJECT_ROOT))
_PENDING_DIR = _PROJECT_ROOT / "job_pipeline" / "data" / "pending"
_CONFIG_DIR  = _PROJECT_ROOT / "job_pipeline" / "config"

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / "job_pipeline" / ".env", override=False)
load_dotenv(_PROJECT_ROOT / ".env", override=False)

from job_pipeline.pipeline import db, scoring

_MAX_JOBS = int(os.environ.get("MAX_JOBS_PER_RUN", "20")) or 9999

cv_folder = os.environ.get("CV_FOLDER_PATH", "")
master_cv_dir = (Path(cv_folder) / "Master CVs") if cv_folder else (_CONFIG_DIR / "cvs")
candidate_profile  = scoring.load_candidate_profile(master_cv_dir)
calibration_notes  = scoring.load_calibration_notes(_CONFIG_DIR / "calibration_notes.md")

conn = db.connect()
try:
    rows = db.jobs_by_status(conn, "extracted")[:_MAX_JOBS]
    jobs = [
        {
            "id": row["id"],
            "title": row["title"],
            "company": row["company"],
            "jd_extracted": json.loads(row["jd_extracted"]),
        }
        for row in rows
    ]
finally:
    conn.close()

_PENDING_DIR.mkdir(parents=True, exist_ok=True)
out_path = _PENDING_DIR / "score_input.json"
out_path.write_text(json.dumps({
    "candidate_profile": candidate_profile,
    "calibration_notes": calibration_notes or "",
    "jobs": jobs,
}, indent=2), encoding="utf-8")

print(f"[score-prep] {len(jobs)} job(s) ready → {out_path}")
