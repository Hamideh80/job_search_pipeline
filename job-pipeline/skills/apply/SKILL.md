# Application Stage

Scrape form questions, draft answers, then fill and submit applications for tailored+approved jobs.

---

## Step 1 — Prep (scrape form questions)

Run:
```
python job-pipeline/skills/apply/prep.py
```

The script opens a Playwright browser session per job, navigates to the application form,
scrapes the custom question labels, closes the browser, and writes
`job_pipeline/data/pending/apply_input.json`.

Each item contains: `id, company, role, source, job_link, resume_pdf, questions[], cv_text, jd_extracted, applicant_notes`

If the array is empty, report "no jobs ready to apply" and stop.

---

## Step 2 — Draft answers (you do this)

For **each job**, apply the prompt below to draft answers for the scraped form questions:

**Prompt:**
```
Answer these application form questions as the candidate, using ONLY facts from the CV text
below — never invent a technology, employer, title, or metric that is not there.

Rules:
- For yes/no eligibility questions (work authorisation, willingness to relocate, sponsorship),
  answer only if the CV or the applicant notes clearly settle it; otherwise return null.
- Keep OMID Foundation framed as part-time volunteer work if it comes up.
- For salary expectation or notice period: use the applicant notes if they contain guidance;
  otherwise return null so the candidate fills it in.

Return ONLY valid JSON — no prose, no markdown fences:
{"answers": {"<question text>": "<answer, or null if not confident>", ...}}

CANDIDATE CV:
{cv_text}

APPLICANT NOTES (work authorisation, notice period, etc.):
{applicant_notes}

JOB REQUIREMENTS (structured):
{jd_extracted}

FORM QUESTIONS (exact text scraped from the form):
{questions}
```

---

## Step 3 — Write results

Collect per-job answers:
```json
[{"id": <job_id>, "answers": {"<question>": "<answer or null>", ...}}, ...]
```

Write to `job_pipeline/data/pending/apply_results.json`.

---

## Step 4 — Commit (fill + submit)

Run:
```
python job-pipeline/skills/apply/commit.py
```

Report: N submitted, N dry-run (screenshot only, AUTO_SUBMIT_CONFIRMED not set), N errors.
List any flagged fields that needed human input but received null.
