"""Phase 6: the learning loop.

This is what turns the pipeline from a scorer with static rules into
something that adapts to your actual decisions over time.

It reads every job you've made a decision on (approved or skipped, in
Notion) plus any recorded outcome (Interview / Rejected / Offer), and asks
Claude to look for patterns your explicit scoring rubric doesn't already
capture -- e.g. "consistently skips Senior Android Developer even at a
score >= 80" or "Implementation/FDE-categorized roles are converting to
interviews far more than Technical Business Analyst ones." Those patterns
get written into config/calibration_notes.md as plain-language guidance,
which pipeline/scoring.py already reads and weighs on every future score
call (see SCORING_PROMPT in scoring.py) -- no code change needed for the
scorer to start using what's learned here.

This is also where the hard gate you asked to park comes back, but learned
rather than hand-specified: if your own decisions show a consistent,
confident pattern (not a one-off), the notes can say so explicitly ("you
have skipped every pure People-Management IC role scored above 70 -- treat
that combination as a near-automatic skip unless the JD says otherwise").
It stays advisory text the scorer weighs, not a code-level reject, until
you've told Claude to make a specific pattern a hard rule.

Design choices worth knowing:
- Calibration notes are REGENERATED each run from the full decision
  history, not appended to forever -- this keeps them bounded and prevents
  stale/contradictory guidance from piling up. The prompt is explicitly
  told to preserve any pattern that's still holding and drop ones that
  no longer are.
- A minimum sample size (MIN_DECISIONS_FOR_PATTERN) gates against
  overfitting a "rule" to one or two data points -- the prompt is told to
  only state a pattern when the evidence is a real repeated signal, and to
  say so explicitly when the sample is still too thin to conclude much.
- This never touches fit_score, jd_extracted, or anything already written
  for a specific job -- it only ever rewrites the calibration notes file
  that future scoring runs read.
"""
import json
from pathlib import Path

from .ai import get_ai_client

MIN_DECISIONS_FOR_PATTERN = 5

LEARNING_PROMPT = """You maintain a calibration-notes file that a job-fit scorer reads before
every score, to adjust how strictly it weighs gaps for THIS specific candidate based on
their real approve/skip decisions and interview outcomes over time.

CURRENT CALIBRATION NOTES (what the scorer is using right now -- may be empty):
{current_notes}

DECISION HISTORY (every job the candidate has explicitly approved or skipped so far,
plus any recorded outcome -- Interview / Rejected / Offer -- if the application went that far):
{decision_history}

Rewrite the calibration notes from scratch based on the full decision history above. Rules:

1. Only state a pattern when the evidence is a real, repeated signal (several jobs pointing
   the same way) -- not a conclusion from one or two data points. With fewer than
   {min_decisions} total decisions, or too few examples of a given category/pattern, say so
   plainly instead of inventing a rule ("not enough data yet on X").
2. Carry forward any pattern from the current notes that the new evidence still supports.
   Drop or soften one that the new evidence contradicts. Never contradict yourself.
3. Look specifically for: categories/titles that get approved vs skipped even at similar
   fit scores; specific gap types (e.g. years of a specific skill, a seniority mismatch,
   a certification) the candidate has shown they'll tolerate or won't; which
   category/company/seniority combinations are actually converting to interviews (a
   stronger signal than an approve/skip alone) or getting rejected after applying.
4. If a pattern is strong and consistent enough that it looks like a near-automatic
   skip (the human gate the candidate deferred adding explicitly -- see project notes),
   say so as a clear, named recommendation in the notes ("near-automatic skip:
   ...") rather than a soft hint -- but only when the evidence genuinely supports it,
   and always as guidance the scorer weighs, not a rule you're asserting cannot be
   overridden by a strong JD.
5. Keep it under 400 words, plain language, organized as short bullet-style lines (no
   markdown headers needed). This is read directly by another prompt on every scoring
   call, so keep it dense and free of commentary about the process itself.
6. Do not invent facts about the candidate's CV or skills -- only reason about their
   observed decision/outcome PATTERNS, which are given to you above.

Return ONLY valid JSON (no prose, no markdown fences):

{{
  "calibration_notes": "<the full replacement text for the calibration notes file>",
  "summary": "<2-4 sentences for the candidate: what changed this run and why, in plain language>"
}}
"""


def _summarize_decision(row: dict) -> dict:
    """Compact view of one decided job -- just what the pattern-finder needs."""
    jd_extracted = row.get("jd_extracted")
    if isinstance(jd_extracted, str):
        try:
            jd_extracted = json.loads(jd_extracted) if jd_extracted else {}
        except json.JSONDecodeError:
            jd_extracted = {}
    jd_extracted = jd_extracted or {}
    return {
        "company": row.get("company"),
        "title": row.get("title"),
        "source": row.get("source"),
        "cv_category": row.get("cv_category"),
        "fit_score": row.get("fit_score"),
        "seniority": jd_extracted.get("seniority"),
        "years_required": jd_extracted.get("years_required"),
        "years_required_context": jd_extracted.get("years_required_context"),
        "decision": row.get("decision"),
        "outcome": row.get("outcome"),
    }


def analyze_decisions(decided_jobs: list[dict], current_notes: str) -> dict:
    """decided_jobs: pipeline DB rows (as dicts) with a non-null `decision`.
    Returns {"calibration_notes": str, "summary": str}."""
    history = [_summarize_decision(row) for row in decided_jobs]
    prompt = LEARNING_PROMPT.format(
        current_notes=current_notes or "(none yet -- this is the first run)",
        decision_history=json.dumps(history, indent=2),
        min_decisions=MIN_DECISIONS_FOR_PATTERN,
    )
    raw = get_ai_client().complete(prompt, max_tokens=1536, purpose="learning")
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


def run_learning(conn, notes_path: Path) -> dict | None:
    """Reads every decided job from the DB, re-derives calibration notes,
    and overwrites notes_path. Returns the analysis result dict, or None if
    skipped (not enough decisions yet to say anything useful)."""
    from . import db

    rows = [dict(row) for row in db.jobs_with_decisions(conn)]
    if len(rows) < MIN_DECISIONS_FOR_PATTERN:
        print(f"[learning] only {len(rows)} decided job(s) so far -- "
              f"need at least {MIN_DECISIONS_FOR_PATTERN} before patterns are "
              "worth deriving. Calibration notes left unchanged.")
        return None

    current_notes = notes_path.read_text() if notes_path.exists() else ""
    result = analyze_decisions(rows, current_notes)

    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(result["calibration_notes"])
    print(f"[learning] recalibrated from {len(rows)} decided job(s) -> {notes_path}")
    print(f"[learning] {result['summary']}")
    return result
