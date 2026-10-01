"""Evergreen application questions used by the tailor skill's prep and SKILL.md.

Answer drafting is now performed by the /job-pipeline:tailor skill, which embeds
the answers prompt directly. This module retains only the question list so prep.py
can include it in the skill's input JSON without duplicating it.
"""

EVERGREEN_QUESTIONS = [
    "Why are you interested in this role?",
    "Why this company?",
    "Briefly describe your relevant experience for this role.",
    "What's your greatest strength relevant to this position?",
]
