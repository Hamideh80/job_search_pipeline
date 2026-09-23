"""Fit scoring against the three CV categories.

Phase 1-3: no automatic hard-gate reject (parked per your comment on the
build-plan doc). Must-have gaps extracted upstream are weighted heavily
into the score itself and called out in the reasoning, instead of silently
rejecting a job before you ever see it. The gate comes back later, learned
from your actual approve/skip pattern -- see the "Fit Scoring (Hard Gate
Deferred)" section of the doc.

The rubric mirrors the fit-analysis framework already defined in the Job
Search 2026 project instructions (career paths, strong/transferable
matches, gaps, interview risk) -- keep the two in sync if either changes.
"""
import json
from pathlib import Path

from .ai import get_ai_client

CV_CATEGORIES = [
    "AI Transformation Consultant",
    "Technical Business Analyst",
    "Implementation / FDE",
]

SCORING_PROMPT = """You are scoring a job description against a candidate's real,
documented experience. Be strict: do not let a keyword match (e.g. the word
"leadership" appearing somewhere) stand in for a specific stated requirement
(e.g. "8 years of Engineering Management experience"). If a must-have isn't
actually supported by the candidate profile, say so plainly in the
reasoning and score it down accordingly -- do not gate the job out
entirely; a low score is enough for the human reviewer to skip it. Weigh
the calibration notes below if any are given -- they reflect the
candidate's own past decisions and should shift how strictly or leniently
you weigh similar gaps in similar roles.

CANDIDATE PROFILE:
{candidate_profile}

CALIBRATION NOTES (from past approve/skip decisions and interview outcomes -- may be empty):
{calibration_notes}

STRUCTURED JOB REQUIREMENTS:
{jd_extracted}

Score the fit for EACH of these three CV categories: {categories}.
Return ONLY valid JSON (no prose, no markdown fences):

{{
  "scores": {{
    "AI Transformation Consultant": <0-100>,
    "Technical Business Analyst": <0-100>,
    "Implementation / FDE": <0-100>
  }},
  "best_category": "<the highest-scoring category>",
  "best_score": <that category's score>,
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
        categories=", ".join(CV_CATEGORIES),
    )
    raw = get_ai_client().complete(prompt, max_tokens=1024, purpose="scoring")
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


def load_candidate_profile(profile_dir: Path) -> str:
    """Concatenates the three base CVs (plain text/markdown files you place
    in config/cvs/) into one profile block for the scorer. See README for
    the expected filenames."""
    parts = []
    for filename, category in [
        ("ai_transformation_consultant.md", "AI Transformation Consultant"),
        ("technical_business_analyst.md", "Technical Business Analyst"),
        ("implementation_fde.md", "Implementation / FDE"),
    ]:
        path = profile_dir / filename
        if path.exists():
            parts.append(f"--- {category} base CV ---\n{path.read_text()}")
        else:
            parts.append(f"--- {category} base CV ---\n[missing: add {path}]")
    return "\n\n".join(parts)


def load_calibration_notes(notes_path: Path) -> str:
    return notes_path.read_text() if notes_path.exists() else ""
