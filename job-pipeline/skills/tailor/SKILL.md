# Tailoring Stage

Build a tailored resume and draft application answers for every Notion-approved job.

---

## Step 1 — Prep

Run:
```
python job-pipeline/skills/tailor/prep.py
```

The script writes `job_pipeline/data/pending/tailor_input.json`. Each item contains:
`id, company, role, full_text, editable_paragraphs, jd_extracted, jd_raw, cv_text, evergreen_questions`

If the array is empty, report "no approved jobs to tailor" and stop.

---

## Step 2 — Tailor resume (you do this)

For **each job**, apply the resume tailoring prompt:

**Prompt:**
```
Tailor this resume for the specific job below.

Rules:
- You may ONLY reword or re-prioritize content already in the FULL CV TEXT — never introduce
  a skill, technology, employer, title, certification, or metric that is not already there.
- If the job wants something genuinely missing from the CV, list it in "flags" instead of
  papering over the gap.
- OMID Foundation experience must always read as part-time volunteer work — never remove or
  soften "Part-time Volunteer" / "volunteer" language.
- Never reframe the Bell "Applied AI" initiative or any self-directed project as production
  machine-learning research, or claim it was an official production deployment if the source
  text describes it as self-directed/internal.
- Prefer re-ordering and re-emphasising existing bullet content over rewriting wholesale;
  keep the same rough length per line.
- Only include paragraph indices you are actually changing. An empty edits object is valid
  if the base CV is already a strong fit as-is.

Return ONLY valid JSON — no prose, no markdown fences:
{
  "edits": {"<paragraph_index>": "<new text for that paragraph>", ...},
  "notes": "<1-3 sentences: what you emphasised and why, for the candidate's reference>",
  "flags": ["<a requirement the JD wants that is genuinely not in the CV>", ...]
}

FULL CURRENT CV TEXT:
{full_text}

EDITABLE PARAGRAPHS (index: current text):
{editable_paragraphs}

JOB REQUIREMENTS (structured):
{jd_extracted}

JOB DESCRIPTION (raw, for tone/context):
{jd_raw}
```

---

## Step 3 — Draft application answers (you do this)

For the **same job**, also draft answers to the evergreen application questions in `evergreen_questions`:

**Prompt:**
```
Draft short, specific answers (3–5 sentences each) to the questions below, as the candidate,
using ONLY facts from the CV text provided.

Rules:
- Never invent a technology, employer, title, or metric not in the CV.
- Keep OMID Foundation framed as part-time volunteer work if it comes up.
- For salary expectation and availability/start date: return null so the candidate fills these in.

Return ONLY valid JSON — no prose, no markdown fences:
{
  "answers": {"<question>": "<answer, or null>", ...},
  "salary_expectation": null,
  "availability": null
}

CANDIDATE CV:
{cv_text}

JOB REQUIREMENTS (structured):
{jd_extracted}

QUESTIONS:
{evergreen_questions}
```

---

## Step 4 — Write results

Combine both outputs per job:
```json
[{
  "id": <job_id>,
  "edits": {"<para_index>": "<new text>", ...},
  "notes": "...",
  "flags": [...],
  "answers": {"<question>": "<answer or null>", ...}
}, ...]
```

Write to `job_pipeline/data/pending/tailor_results.json`.

---

## Step 5 — Commit

Run:
```
python job-pipeline/skills/tailor/commit.py
```

Report: N tailored (docx + pdf saved), N errors. For any flags, list them so the candidate knows what gaps remain.
