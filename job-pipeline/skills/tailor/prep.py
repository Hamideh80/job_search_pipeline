"""Export 'approved' jobs with CV paragraph data for the tailor skill.

Opens the base .docx to extract editable paragraph indices and text so the
skill can propose targeted edits without re-reading the full file.

Output: job_pipeline/data/pending/tailor_input.json
  [{id, company, role, full_text, editable_paragraphs, jd_extracted, jd_raw,
    cv_text, evergreen_questions}]
"""
import json
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_PROJECT_ROOT))
_PENDING_DIR = _PROJECT_ROOT / "job_pipeline" / "data" / "pending"

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / "job_pipeline" / ".env", override=False)
load_dotenv(_PROJECT_ROOT / ".env", override=False)

from job_pipeline.pipeline import db, tailoring, answers

cv_folder = os.environ.get("CV_FOLDER_PATH", "")
if not cv_folder:
    print("[tailor-prep] CV_FOLDER_PATH not set — cannot load base CV")
    sys.exit(1)

base_cv_path = Path(cv_folder) / tailoring.COMBINED_CV_FILE
if not base_cv_path.exists():
    print(f"[tailor-prep] base CV not found: {base_cv_path}")
    sys.exit(1)

cv_text_path = Path(cv_folder) / "Master CVs" / "Hamideh_Ahooei_Master_CV_Combined.md"
cv_text = cv_text_path.read_text(encoding="utf-8") if cv_text_path.exists() else ""

import docx as _docx

conn = db.connect()
try:
    rows = db.jobs_by_status(conn, "approved")
    jobs = []
    for row in rows:
        doc = _docx.Document(str(base_cv_path))
        jobs.append({
            "id": row["id"],
            "company": row["company"],
            "role": row["title"],
            "full_text": tailoring.extract_full_text(doc),
            "editable_paragraphs": tailoring.extract_editable_paragraphs(doc),
            "jd_extracted": json.loads(row["jd_extracted"]),
            "jd_raw": (row["jd_raw"] or "")[:6000],
            "cv_text": cv_text,
            "evergreen_questions": answers.EVERGREEN_QUESTIONS,
        })
finally:
    conn.close()

_PENDING_DIR.mkdir(parents=True, exist_ok=True)
out_path = _PENDING_DIR / "tailor_input.json"
out_path.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
print(f"[tailor-prep] {len(jobs)} job(s) ready → {out_path}")
