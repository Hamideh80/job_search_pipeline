# Extraction Stage

Parse raw job descriptions into structured fields.

---

## Step 1 — Prep

Run:
```
python job-pipeline/skills/extract/prep.py
```

The script prints the path to `extract_input.json` and the count of jobs to process.
If count is 0, report "nothing to extract" and stop.

---

## Step 2 — Extract (you do this)

Read `job_pipeline/data/pending/extract_input.json`.

For **each job** in the array, apply the prompt below and collect the JSON result.
Process all jobs before writing results.

**Prompt:**
```
Extract structured requirements from the job description below.
Return ONLY valid JSON — no prose, no markdown fences — matching exactly this shape:

{
  "must_haves": ["<requirement>", ...],
  "nice_to_haves": ["<requirement>", ...],
  "years_required": <integer or null>,
  "years_required_context": "<what those years must be in, e.g. 'engineering management', or null>",
  "seniority": "<Junior|Mid|Senior|Staff|Manager|Director|Unclear>",
  "remote_policy": "<Remote|Hybrid|On-site|Unclear>",
  "location": "<city/region as stated in JD, or null>",
  "certifications_required": ["<cert>", ...],
  "work_authorization_required": "<e.g. 'Must be authorized to work in Canada', or null>"
}

Rules:
- Only mark something as a must-have if the JD explicitly says required / must have / minimum.
  Never promote a "nice to have", "preferred", or general skill mention into a must-have.
- If a field has no clear answer, use null (years_required, location) or [] (lists) or "Unclear".

JOB: {title} at {company}
JOB DESCRIPTION:
{jd_raw}
```

---

## Step 3 — Write results

Collect all results as a JSON array:
```json
[{"id": <job_id>, "extracted": { <the JSON object above> }}, ...]
```

Write this array to `job_pipeline/data/pending/extract_results.json`.
Create the `pending/` directory if it does not exist.

---

## Step 4 — Commit

Run:
```
python job-pipeline/skills/extract/commit.py
```

Report: N extracted, N errors. If there are errors, show the job IDs and messages.
