"""Scrape application form questions for each tailored+approved ATS job.

Opens a Playwright browser session per job, navigates to the form, reads
the visible question labels, then closes the browser. The skill then drafts
answers; apply/commit.py opens a second session to fill and submit.

Output: job_pipeline/data/pending/apply_input.json
  [{id, company, role, source, job_link, resume_pdf, questions[], cv_text,
    jd_extracted, applicant_notes}]
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

from job_pipeline.pipeline import db, apply as apply_mod

_APPLY_SOURCES = ("Greenhouse", "Ashby", "Lever")

cv_folder = os.environ.get("CV_FOLDER_PATH", "")
cv_text_path = Path(cv_folder) / "Master CVs" / "Hamideh_Ahooei_Master_CV_Combined.md" if cv_folder else None
cv_text = cv_text_path.read_text(encoding="utf-8") if cv_text_path and cv_text_path.exists() else ""
applicant_notes = apply_mod.load_applicant_notes(_CONFIG_DIR / "applicant_notes.md")

conn = db.connect()
try:
    rows = conn.execute(
        "SELECT * FROM jobs WHERE pipeline_status = 'tailored' AND decision = 'approved' "
        f"AND source IN ({','.join('?' * len(_APPLY_SOURCES))})",
        _APPLY_SOURCES,
    ).fetchall()

    jobs = []
    for row in rows:
        questions = apply_mod.scrape_form_questions(
            source=row["source"],
            job_link=row["link"] or "",
        )
        jobs.append({
            "id": row["id"],
            "company": row["company"],
            "role": row["title"],
            "source": row["source"],
            "job_link": row["link"] or "",
            "resume_pdf": row["tailored_resume_pdf"] or "",
            "questions": questions,
            "cv_text": cv_text,
            "jd_extracted": json.loads(row["jd_extracted"]) if row["jd_extracted"] else {},
            "applicant_notes": applicant_notes,
        })
finally:
    conn.close()

_PENDING_DIR.mkdir(parents=True, exist_ok=True)
out_path = _PENDING_DIR / "apply_input.json"
out_path.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
print(f"[apply-prep] {len(jobs)} job(s) ready → {out_path}")
