"""Write scoring results from the skill back to the pipeline DB.

Reads: job_pipeline/data/pending/score_results.json
  [{id, score, reasoning, strong_matches, transferable_matches, gaps, interview_risk}, ...]

score >= 50 → pipeline_status = 'shortlisted'
score <  50 → pipeline_status = 'skipped_low_score'
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

SCORE_THRESHOLD = 50

results_path = _PENDING_DIR / "score_results.json"
if not results_path.exists():
    print("[score-commit] score_results.json not found — nothing to commit")
    sys.exit(1)

results = json.loads(results_path.read_text(encoding="utf-8"))
conn = db.connect()
n_shortlisted = n_below = n_err = 0
scores = []
try:
    for item in results:
        job_id = item.get("id")
        score  = item.get("score")
        if score is None:
            print(f"[score-commit] job {job_id}: missing score — skipping")
            n_err += 1
            continue
        score = int(score)
        scores.append(score)
        status = "shortlisted" if score >= SCORE_THRESHOLD else "skipped_low_score"
        db.update_job(conn, job_id,
            fit_score=score,
            score_reason=item.get("reasoning", ""),
            pipeline_status=status,
        )
        if score >= SCORE_THRESHOLD:
            n_shortlisted += 1
        else:
            n_below += 1
finally:
    conn.close()

results_path.unlink()
print(f"[score-commit] {n_shortlisted} shortlisted, {n_below} below threshold, {n_err} error(s)")
if scores:
    print(f"[score-commit] scores: min={min(scores)}, max={max(scores)}, "
          f"avg={sum(scores)/len(scores):.0f}")
