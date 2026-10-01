"""Fill and submit application forms using the skill-drafted answers.

Reads: job_pipeline/data/pending/apply_results.json
  [{id, answers: {"<question label>": "<answer or null>", ...}}]

For each job: opens a Playwright session, fills the form using pre-drafted
answers, takes a screenshot, and (if AUTO_SUBMIT_CONFIRMED=true) submits.
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

from job_pipeline.pipeline import db, apply as apply_mod, notion_sync

screenshot_dir = Path(os.environ.get(
    "APPLY_SCREENSHOT_DIR",
    str(_PROJECT_ROOT / "job_pipeline" / "data" / "screenshots"),
))

results_path = _PENDING_DIR / "apply_results.json"
if not results_path.exists():
    print("[apply-commit] apply_results.json not found — nothing to commit")
    sys.exit(1)

results     = json.loads(results_path.read_text(encoding="utf-8"))
answers_map = {item["id"]: item.get("answers", {}) for item in results}

conn = db.connect()
n_submitted = n_dry = n_err = 0
try:
    for item in results:
        job_id  = item["id"]
        row     = db.get_job(conn, job_id)
        if row is None:
            print(f"[apply-commit] job {job_id} not found in DB")
            n_err += 1
            continue
        jd_extracted = json.loads(row["jd_extracted"]) if row["jd_extracted"] else {}
        cv_text_path = None
        cv_folder    = os.environ.get("CV_FOLDER_PATH", "")
        if cv_folder:
            cv_text_path = Path(cv_folder) / "Master CVs" / "Hamideh_Ahooei_Master_CV_Combined.md"
        cv_text = cv_text_path.read_text(encoding="utf-8") if cv_text_path and cv_text_path.exists() else ""

        try:
            result = apply_mod.apply_to_job(
                source=row["source"],
                job_link=row["link"] or "",
                resume_pdf_path=row["tailored_resume_pdf"] or "",
                cv_text=cv_text,
                jd_extracted=jd_extracted,
                output_dir=screenshot_dir,
                company=row["company"],
                role=row["title"],
                pre_answered=answers_map.get(job_id, {}),
            )
        except Exception as exc:
            print(f"[apply-commit] job {job_id} failed: {exc}")
            n_err += 1
            continue

        new_status = "applied" if result["submitted"] else "ready_to_apply"
        db.update_job(conn, job_id,
            custom_answers=result["custom_answers"],
            apply_flags=result["flags"],
            application_screenshot=result["screenshot_path"],
            pipeline_status=new_status,
            submitted_at=db.now_iso() if result["submitted"] else None,
        )
        if result["submitted"]:
            if row["notion_page_id"]:
                notion_sync.mark_applied(row["notion_page_id"], applied_via="Auto")
            n_submitted += 1
        else:
            n_dry += 1
finally:
    conn.close()

results_path.unlink()
print(f"[apply-commit] {n_submitted} submitted, {n_dry} dry-run, {n_err} error(s)")
