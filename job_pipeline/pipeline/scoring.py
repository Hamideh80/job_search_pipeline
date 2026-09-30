"""Fit scoring against the candidate's combined Master CV.

Each job is scored on a 0-100 scale. Score >= SCORE_THRESHOLD (70) →
shortlisted in Notion for human review. Score < 70 → skipped_low_score.

Tailoring only happens AFTER the human approves in Notion.
"""
import json
from pathlib import Path

from .ai import get_ai_client

COMBINED_CV_FILE = "Hamideh_Ahooei_Master_CV_Combined.md"

SCORING_PROMPT = """You are evaluating a job description against a candidate's CV to determine fit.

CANDIDATE CV:
{candidate_profile}

CALIBRATION NOTES (from past approve/skip decisions — may be empty):
{calibration_notes}

STRUCTURED JOB REQUIREMENTS:
{jd_extracted}

Score the candidate's overall fit for this role on a scale of 0-100.
Be strict: if a must-have requirement is not clearly supported by the CV, lower the score
accordingly. Do not hard-reject — a low score is sufficient for the human reviewer to skip.

SECURITY CLEARANCE: "Eligible for [clearance]" or "eligibility for [clearance]" means
the candidate must be able to obtain the clearance (e.g. Canadian citizen or permanent
resident with a clean background) — NOT that they must already hold it. Do NOT penalize
for lacking an active clearance when the JD uses "eligible for" or "eligibility for".
Only treat clearance as a gap if the JD explicitly states it must already be active,
in-progress, or currently held.

Return ONLY valid JSON (no prose, no markdown fences):

{{
  "score": <0-100>,
  "strong_matches": ["..."],
  "transferable_matches": ["..."],
  "gaps": ["..."],
  "interview_risk": ["claims that would be hard to defend in an interview"],
  "reasoning": "<2-4 sentences a human can read in the Notion Score Reason field>"
}}
"""


def score(jd_extracted: dict, candidate_profile: str, calibration_notes: str = "") -> dict:
    prompt = SCORING_PROMPT.format(
        candidate_profile=candidate_profile,
        calibration_notes=calibration_notes or "(none yet)",
        jd_extracted=json.dumps(jd_extracted, indent=2),
    )
    raw = get_ai_client().complete(prompt, max_tokens=1024, purpose="scoring")
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    result = json.loads(raw)
    return {
        "best_score":          result["score"],
        "best_category":       None,
        "reasoning":           result.get("reasoning", ""),
        "strong_matches":      result.get("strong_matches", []),
        "transferable_matches": result.get("transferable_matches", []),
        "gaps":                result.get("gaps", []),
        "interview_risk":      result.get("interview_risk", []),
    }


def load_candidate_profile(master_cv_dir: Path) -> str:
    """Read the single combined Master CV as the candidate profile."""
    path = master_cv_dir / COMBINED_CV_FILE
    if path.exists():
        return path.read_text()
    return f"[missing: {path}]"


def load_calibration_notes(notes_path: Path) -> str:
    return notes_path.read_text() if notes_path.exists() else ""
