"""Turn a raw job description into structured fields via Claude.

This structured JD is what the fit scorer reads from, never the raw text --
and it's kept separate from scoring so the same extraction can be reused
once the hard gate comes back (phase 6, see the build-plan doc).
"""
import json

from .ai import get_ai_client

EXTRACTION_PROMPT = """You are extracting structured requirements from a job description.
Read the JD below and return ONLY valid JSON (no prose, no markdown fences) matching this shape:

{{
  "must_haves": ["..."],
  "nice_to_haves": ["..."],
  "years_required": <int or null>,
  "years_required_context": "<what those years must be in, e.g. 'Engineering Management', or null>",
  "seniority": "<Junior|Mid|Senior|Staff|Manager|Director|Unclear>",
  "remote_policy": "<Remote|Hybrid|On-site|Unclear>",
  "location": "<city/region stated, or null>",
  "certifications_required": ["..."],
  "work_authorization_required": "<e.g. 'Must be authorized to work in Canada', or null>"
}}

Be conservative: only extract something as a must-have if the JD actually
says required / must have / minimum -- don't promote a "nice to have" or a
general skill mention into a must-have.

JOB DESCRIPTION:
{jd_text}
"""


def extract(jd_text: str) -> dict:
    prompt = EXTRACTION_PROMPT.format(jd_text=jd_text[:15000])
    raw = get_ai_client().complete(prompt, max_tokens=1024, purpose="extraction")
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)
