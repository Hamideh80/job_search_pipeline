"""Export 'discovered' jobs to JSON for the extract skill.

Also applies the French-mandatory hard filter (pure regex, no AI) and marks
those jobs as skipped_language_requirement before they reach the skill.

Output: job_pipeline/data/pending/extract_input.json
"""
import json
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_PROJECT_ROOT))
_PENDING_DIR = _PROJECT_ROOT / "job_pipeline" / "data" / "pending"

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / "job_pipeline" / ".env", override=False)
load_dotenv(_PROJECT_ROOT / ".env", override=False)

from job_pipeline.pipeline import db

_FRENCH_PATTERNS = [
    r"french\s+(?:required|mandatory|essential|obligatoire)",
    r"must\s+(?:speak|be\s+bilingual\s+in|have\s+(?:proficiency|fluency)\s+in)\s+french",
    r"must\s+be\s+(?:proficient|fluent)\s+in\s+french",
    r"bilingual\s+(?:french[/-]english|english[/-]french)\s+(?:required|mandatory|essential)",
    r"(?:english\s+and\s+french|french\s+and\s+english)\s+(?:required|mandatory|essential|obligatoire)",
    r"professional\s+(?:proficiency|fluency)\s+in\s+french.{0,40}?(?:required|mandatory|essential)",
    r"fluent\s+(?:in\s+)?french.{0,40}?(?:required|mandatory|essential|is\s+required|is\s+mandatory)",
    r"fran[cç]ais\s+(?:obligatoire|requis|exig[ée]e?)",
]
_FRENCH_RE = re.compile(
    "|".join(r"(?:" + p + r")" for p in _FRENCH_PATTERNS),
    re.IGNORECASE,
)

_MAX_JOBS = int(__import__("os").environ.get("MAX_JOBS_PER_RUN", "20")) or 9999

conn = db.connect()
try:
    rows = db.jobs_by_status(conn, "discovered")[:_MAX_JOBS]
    jobs = []
    n_french = 0
    for row in rows:
        if _FRENCH_RE.search(row["jd_raw"] or ""):
            db.update_job(conn, row["id"], pipeline_status="skipped_language_requirement")
            n_french += 1
            continue
        jobs.append({
            "id": row["id"],
            "title": row["title"],
            "company": row["company"],
            "jd_raw": (row["jd_raw"] or "")[:15000],
        })
finally:
    conn.close()

_PENDING_DIR.mkdir(parents=True, exist_ok=True)
out_path = _PENDING_DIR / "extract_input.json"
out_path.write_text(json.dumps(jobs, indent=2), encoding="utf-8")

print(f"[extract-prep] {len(jobs)} job(s) ready → {out_path}")
if n_french:
    print(f"[extract-prep] {n_french} French-mandatory job(s) marked skipped_language_requirement")
