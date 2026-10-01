# Learning Stage

Analyse all past approve/skip decisions and rewrite the calibration notes that the scoring stage reads on every run. Requires at least 5 decided jobs.

---

## Step 1 — Prep

Run:
```
python job-pipeline/skills/learn/prep.py
```

If fewer than 5 jobs have been decided, the script prints a notice and exits cleanly — stop here.

Otherwise it writes `job_pipeline/data/pending/learn_input.json` containing:
- `current_notes`: what the scorer is using right now
- `decision_history`: every job with a recorded approve/skip + optional interview outcome
- `min_decisions`: minimum sample size for reliable patterns (5)

---

## Step 2 — Analyse decisions (you do this)

Apply the prompt below:

**Prompt:**
```
You maintain a calibration-notes file that a job-fit scorer reads before every score, to adjust
how strictly it weighs gaps for this specific candidate based on their real decisions over time.

CURRENT CALIBRATION NOTES (what the scorer is using right now — may be empty):
{current_notes}

DECISION HISTORY (every job the candidate has explicitly approved or skipped, plus any recorded
outcome — Interview / Rejected / Offer — if the application went that far):
{decision_history}

Rewrite the calibration notes from scratch based on the full decision history. Rules:

1. Only state a pattern when the evidence is a real repeated signal (several jobs pointing the
   same way), not a conclusion from one or two data points. With fewer than {min_decisions} total
   decisions or too few examples of a given category/pattern, say so plainly instead of inventing
   a rule ("not enough data yet on X").
2. Carry forward any pattern from the current notes that the new evidence still supports.
   Drop or soften one that the new evidence contradicts. Never contradict yourself.
3. Look specifically for: categories/titles that get approved vs skipped even at similar fit
   scores; specific gap types the candidate has shown they'll tolerate or won't; which
   category/company/seniority combinations are actually converting to interviews (a stronger
   signal than an approve/skip alone) or getting rejected after applying.
4. If a pattern is strong and consistent enough that it looks like a near-automatic skip, say so
   as a clear named recommendation ("near-automatic skip: ...") — but only when the evidence
   genuinely supports it, and always as guidance the scorer weighs, not an absolute rule.
5. Keep it under 400 words, plain language, organised as short bullet-style lines. This is read
   directly by another prompt on every scoring call, so keep it dense and free of meta-commentary.
6. Do not invent facts about the candidate's CV or skills — only reason about observed
   decision/outcome patterns given to you above.

Return ONLY valid JSON — no prose, no markdown fences:
{
  "calibration_notes": "<the full replacement text for the calibration notes file>",
  "summary": "<2-4 sentences for the candidate: what changed this run and why, in plain language>"
}
```

---

## Step 3 — Write results

Write to `job_pipeline/data/pending/learn_results.json`:
```json
{"calibration_notes": "...", "summary": "..."}
```

---

## Step 4 — Commit

Run:
```
python job-pipeline/skills/learn/commit.py
```

Report the summary line so the candidate can see what patterns were found.
