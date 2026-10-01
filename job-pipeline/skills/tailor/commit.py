"""Apply tailoring edits from the skill to the base .docx and write to DB.

Reads: job_pipeline/data/pending/tailor_results.json
  [{id, edits: {"<para_index>": "<new_text>"}, notes, flags, answers}]

For each job: loads base CV docx → applies edits → saves docx + pdf → writes DB.
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

from job_pipeline.pipeline import db, tailoring

cv_folder  = os.environ.get("CV_FOLDER_PATH", "")
output_dir = Path(os.environ.get("CV_OUTPUT_DIR",
                  str(_PROJECT_ROOT / "job_pipeline" / "data" / "tailored")))

results_path = _PENDING_DIR / "tailor_results.json"
if not results_path.exists():
    print("[tailor-commit] tailor_results.json not found — nothing to commit")
    sys.exit(1)

results    = json.loads(results_path.read_text(encoding="utf-8"))
base_cv    = Path(cv_folder) / tailoring.COMBINED_CV_FILE if cv_folder else None

import docx as _docx

conn = db.connect()
n_ok = n_err = 0
try:
    for item in results:
        job_id = item.get("id")
        row    = db.get_job(conn, job_id)
        if row is None:
            print(f"[tailor-commit] job {job_id} not found in DB — skipping")
            n_err += 1
            continue
        if not base_cv or not base_cv.exists():
            print(f"[tailor-commit] job {job_id}: base CV not found at {base_cv}")
            n_err += 1
            continue
        try:
            doc = _docx.Document(str(base_cv))
            tailoring.apply_edits(doc, item.get("edits", {}))

            output_dir.mkdir(parents=True, exist_ok=True)
            stem      = f"Hamideh_Ahooei_{tailoring.sanitize(row['company'])}_{tailoring.sanitize(row['title'])}"
            docx_path = output_dir / f"{stem}.docx"
            doc.save(str(docx_path))
            pdf_path  = tailoring.convert_to_pdf(docx_path, output_dir)

            db.update_job(conn, job_id,
                tailored_resume_docx=str(docx_path),
                tailored_resume_pdf=str(pdf_path) if pdf_path else None,
                tailoring_notes=item.get("notes", ""),
                tailoring_flags=item.get("flags", []),
                answers=item.get("answers", {}),
                pipeline_status="tailored",
            )
            n_ok += 1
        except Exception as exc:
            print(f"[tailor-commit] job {job_id} failed: {exc}")
            n_err += 1
finally:
    conn.close()

results_path.unlink()
print(f"[tailor-commit] {n_ok} tailored, {n_err} error(s)")
