"""Candidate profile and calibration note loaders used by the score skill's prep script.

Scoring itself is now performed by the /job-pipeline:score skill, which embeds the
scoring prompt directly. This module retains only the file-reader helpers so prep.py
can load the candidate profile and calibration notes without duplicating the path logic.
"""
from pathlib import Path

COMBINED_CV_FILE = "Hamideh_Ahooei_Master_CV_Combined.md"

_SUMMARY_PATH = Path(__file__).resolve().parent.parent / "config" / "candidate_summary.md"


def load_candidate_profile(master_cv_dir: Path) -> str:
    """Return the candidate profile used for scoring.

    Uses config/candidate_summary.md (concise, token-efficient) when present.
    Falls back to the full combined CV from master_cv_dir if the summary is missing.
    """
    if _SUMMARY_PATH.exists():
        return _SUMMARY_PATH.read_text(encoding="utf-8")
    path = master_cv_dir / COMBINED_CV_FILE
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"[candidate profile missing: tried {_SUMMARY_PATH} and {path}]"


def load_calibration_notes(notes_path: Path) -> str:
    return notes_path.read_text() if notes_path.exists() else ""
