"""Write the skill-generated calibration notes to disk.

Reads: job_pipeline/data/pending/learn_results.json
  {calibration_notes: "<full text>", summary: "<2-4 sentence summary>"}

Overwrites config/calibration_notes.md (gitignored).
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

results_path = _PENDING_DIR / "learn_results.json"
if not results_path.exists():
    print("[learn-commit] learn_results.json not found — nothing to commit")
    sys.exit(1)

result = json.loads(results_path.read_text(encoding="utf-8"))
notes  = result.get("calibration_notes", "").strip()
if not notes:
    print("[learn-commit] empty calibration_notes in results — skipping overwrite")
    sys.exit(1)

notes_path = _CONFIG_DIR / "calibration_notes.md"
notes_path.parent.mkdir(parents=True, exist_ok=True)
notes_path.write_text(notes, encoding="utf-8")

results_path.unlink()
print(f"[learn-commit] calibration notes updated → {notes_path}")
print(f"[learn-commit] {result.get('summary', '')}")
