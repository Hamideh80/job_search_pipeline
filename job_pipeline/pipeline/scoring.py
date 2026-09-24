"""Fit scoring against the three canonical Master CV role families.

Each job is classified into one of three families based on the actual work
described in the JD — not title-matching alone. The AI returns a score for
all three families and picks the best fit.

Score >= SCORE_THRESHOLD (70) → shortlisted in Notion for human review.
Score < 70 → skipped_low_score (never shown to the human).

Tailoring only happens AFTER the human approves in Notion.
"""
import json
from pathlib import Path

from .ai import get_ai_client

CV_CATEGORIES = [
    "FDE / Solutions",
    "Agentic AI",
    "Technical Leadership",
]

SCORING_PROMPT = """You are scoring a job description against a candidate's documented \
experience and classifying it into the best-fit role family.

ROLE FAMILY DEFINITIONS
Use these definitions to classify the role based on its actual work requirements,
not just the job title.

FDE / Solutions:
  Core signal: hands-on technical delivery, implementation, configuration, integration,
  requirements discovery, troubleshooting, and deployment/adoption with customers or
  stakeholders. Quota-carrying pre-sales roles or roles requiring extensive years of
  external consulting without engineering depth do NOT belong here.
  Examples: Forward Deployed Engineer, Solutions Engineer, AI Solutions Engineer,
  Implementation Engineer, Implementation Consultant, Customer Engineer, Integration
  Engineer, Technical Solutions Engineer.

Agentic AI:
  Core signal: building applied AI/agent systems — orchestration, agents, tool use,
  retrieval/grounding, MCP integration, evaluation, workflow automation.
  Do NOT classify here if the core work is ML research, model training, data science,
  MLOps, senior Python backend platform engineering, or production AI infrastructure.
  Examples: Agentic AI Engineer, Applied AI Engineer, AI Engineer, AI Agent Engineer,
  AI Workflow Engineer, AI Automation Engineer, AI Integration Engineer, applied LLM roles.

Technical Leadership:
  Core signal: technical initiative ownership, cross-functional delivery, stakeholder
  alignment, AI adoption/enablement, technical program or delivery leadership.
  Engineering Manager roles qualify ONLY when prior formal people-management experience
  is NOT a hard requirement in the JD.
  Examples: Technical Program Manager – AI, AI Delivery Manager, AI Enablement Manager,
  Technical Delivery Manager, Digital Transformation Manager, AI Transformation Manager,
  Engineering Lead.

IMPORTANT: classify by the actual responsibilities and requirements in the JD — not by
keyword or title matching alone. A title with "AI" may still be Technical Leadership if
the core work is program management, not engineering.

CANDIDATE PROFILE:
{candidate_profile}

CALIBRATION NOTES (from past approve/skip decisions and interview outcomes — may be empty):
{calibration_notes}

STRUCTURED JOB REQUIREMENTS:
{jd_extracted}

Score the fit for EACH of these three role families: {categories}.
Be strict: do not let a keyword match stand in for a specific stated requirement.
If a must-have is not supported by the candidate profile, call it out in reasoning
and score it down accordingly. Do not reject the job outright — a low score is
sufficient for the human reviewer to skip it.

Return ONLY valid JSON (no prose, no markdown fences):

{{
  "scores": {{
    "FDE / Solutions": <0-100>,
    "Agentic AI": <0-100>,
    "Technical Leadership": <0-100>
  }},
  "best_category": "<the highest-scoring family — exactly one of: FDE / Solutions, Agentic AI, Technical Leadership>",
  "best_score": <that family's score>,
  "strong_matches": ["..."],
  "transferable_matches": ["..."],
  "gaps": ["..."],
  "interview_risk": ["claims that would be hard to defend in an interview"],
  "reasoning": "<2-4 sentences a human can read in the Notion Score Reason field>"
}}
"""

MASTER_CV_FILES = [
    ("Hamideh_Ahooei_Master_FDE_Solutions.md", "FDE / Solutions"),
    ("Hamideh_Ahooei_Master_Agentic_AI.md", "Agentic AI"),
    ("Hamideh_Ahooei_Master_Technical_Leadership.md", "Technical Leadership"),
]


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


def load_candidate_profile(master_cv_dir: Path) -> str:
    """Concatenates the three Master CVs into one profile block for the scorer.

    master_cv_dir should be the folder containing the three canonical
    Hamideh_Ahooei_Master_*.md files (typically CV_FOLDER_PATH/Master CVs).
    """
    parts = []
    for filename, family in MASTER_CV_FILES:
        path = master_cv_dir / filename
        if path.exists():
            parts.append(f"--- {family} Master CV ---\n{path.read_text()}")
        else:
            parts.append(f"--- {family} Master CV ---\n[missing: {path}]")
    return "\n\n".join(parts)


def load_calibration_notes(notes_path: Path) -> str:
    return notes_path.read_text() if notes_path.exists() else ""
