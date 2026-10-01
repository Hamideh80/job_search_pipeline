# Scoring Stage

Score each extracted job against the candidate profile. Jobs scoring ≥ 50 are shortlisted for human review in Notion; jobs below 50 are silently skipped.

---

## Step 1 — Prep

Run:
```
python job-pipeline/skills/score/prep.py
```

The script writes `job_pipeline/data/pending/score_input.json` containing:
- `candidate_profile`: the candidate summary used for all scoring calls
- `calibration_notes`: patterns derived from past approve/skip decisions (may be empty)
- `jobs`: array of `{id, title, company, jd_extracted}`

If `jobs` is empty, report "nothing to score" and stop.

---

## Step 2 — Score (you do this)

Read `job_pipeline/data/pending/score_input.json`.

For **each job** in `jobs`, apply the prompt below using the shared `candidate_profile` and `calibration_notes` from the top of the file.

**Prompt:**
```
Evaluate this candidate's fit for the job below. Score 0–100.

CANDIDATE PROFILE:
{candidate_profile}

CALIBRATION NOTES (patterns from past decisions — may be empty):
{calibration_notes}

STRUCTURED JOB REQUIREMENTS:
{jd_extracted}

Scoring rules:
- Be strict: if a must-have requirement is not clearly supported by the profile, lower the score.
  Do not hard-reject — a low score is sufficient for the human reviewer to skip.
- SECURITY CLEARANCE: "Eligible for [clearance]" or "eligibility for [clearance]" means the
  candidate must be able to obtain it (Canadian citizen / PR with clean background) — NOT that
  they must already hold it. Do NOT penalize for lacking an active clearance when the JD uses
  "eligible for" or "eligibility for". Only flag clearance as a gap if the JD says the clearance
  must already be active, in-progress, or currently held.
- CANADIAN LOCATION: Do NOT treat location as a gap for hybrid or remote roles anywhere in Canada.
  A Canadian candidate can relocate or commute to any Canadian city, and hybrid roles only require
  part-time on-site presence. Only flag location if the role is fully on-site AND relocation is
  not indicated as acceptable.

Return ONLY valid JSON — no prose, no markdown fences:
{
  "score": <0-100>,
  "strong_matches": ["<match>", ...],
  "transferable_matches": ["<match>", ...],
  "gaps": ["<gap>", ...],
  "interview_risk": ["<claim that would be hard to defend in an interview>", ...],
  "reasoning": "<2-4 sentences a human can read in the Notion Score Reason field>"
}

JOB: {title} at {company}
REQUIREMENTS: {jd_extracted}
```

---

## Step 3 — Write results

Collect all results as:
```json
[{"id": <job_id>, "score": <int>, "reasoning": "...", "strong_matches": [...],
  "transferable_matches": [...], "gaps": [...], "interview_risk": [...]}, ...]
```

Write to `job_pipeline/data/pending/score_results.json`.

---

## Step 4 — Commit

Run:
```
python job-pipeline/skills/score/commit.py
```

Report: N shortlisted (score ≥ 50), N below threshold, score range (min/max/avg), N errors.
