"""Export decision history for the learn skill to analyze.

Output: job_pipeline/data/pending/learn_input.json
  {current_notes, decision_history: [{company, title, fit_score, decision, ...}],
   min_decisions}
"""
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_PROJECT_ROOT))
_PENDING_DIR = _PROJECT_ROOT / "job_pipeline" / "data" / "pending"
_CONFIG_DIR  = _PROJECT_ROOT / "job_pipeline" / "config"

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / "job_pipeline" / ".env", override=False)
load_dotenv(_PROJECT_ROOT / ".env", override=False)

from job_pipeline.pipeline import db, learning

MIN_DECISIONS = learning.MIN_DECISIONS_FOR_PATTERN

notes_path    = _CONFIG_DIR / "calibration_notes.md"
current_notes = notes_path.read_text(encoding="utf-8") if notes_path.exists() else ""

conn = db.connect()
try:
    rows    = [dict(r) for r in db.jobs_with_decisions(conn)]
    history = [learning._summarize_decision(r) for r in rows]
finally:
    conn.close()

if len(rows) < MIN_DECISIONS:
    print(f"[learn-prep] only {len(rows)} decided job(s) — need {MIN_DECISIONS} before patterns "
          "are worth deriving. Calibration notes left unchanged.")
    sys.exit(0)

_PENDING_DIR.mkdir(parents=True, exist_ok=True)
out_path = _PENDING_DIR / "learn_input.json"
out_path.write_text(json.dumps({
    "current_notes": current_notes or "(none yet — this is the first run)",
    "decision_history": history,
    "min_decisions": MIN_DECISIONS,
}, indent=2), encoding="utf-8")
print(f"[learn-prep] {len(rows)} decided job(s) → {out_path}")
