"""Draft evergreen application answers, grounded in the real CV content.

Scope note: this drafts answers to the common questions that show up on
almost every application form (why this role, relevant experience, salary
expectations, work authorization, availability). It does NOT scrape a
specific posting's custom question set -- reading an actual Greenhouse/
Ashby/Lever application form's fields needs the same browser-automation
work as auto-submit, so per-posting custom questions land in phase 5
alongside it. These evergreen answers are still useful as a starting draft
you paste from and edit per question.
"""
import json

from .ai import get_ai_client

EVERGREEN_QUESTIONS = [
    "Why are you interested in this role?",
    "Why this company?",
    "Briefly describe your relevant experience for this role.",
    "What's your greatest strength relevant to this position?",
]

ANSWERS_PROMPT = """Draft short, specific answers (3-5 sentences each) to the questions
below, as the candidate, using ONLY facts from the CV text provided. Never
invent a technology, employer, title, or metric that isn't in the CV. Keep
OMID Foundation framed as part-time volunteer work if it comes up. Do not
answer the salary-expectation or availability/start-date questions with a
made-up number or date -- return null for those so the candidate fills them
in themselves.

Return ONLY valid JSON (no prose, no markdown fences):
{{
  "answers": {{"<question>": "<answer or null>", ...}},
  "salary_expectation": null,
  "availability": null
}}

CANDIDATE CV:
{cv_text}

JOB REQUIREMENTS (structured):
{jd_extracted}

QUESTIONS:
{questions}
"""


def draft_answers(cv_text: str, jd_extracted: dict) -> dict:
    prompt = ANSWERS_PROMPT.format(
        cv_text=cv_text,
        jd_extracted=json.dumps(jd_extracted, indent=2),
        questions="\n".join(f"- {q}" for q in EVERGREEN_QUESTIONS),
    )
    raw = get_ai_client().complete(prompt, max_tokens=1024, purpose="answers")
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)
