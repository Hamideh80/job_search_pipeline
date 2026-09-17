# Job Search Pipeline — Phases 1–4

Implements phases 1–4 from the [build-plan doc](https://claude.ai/code/artifact/aa39646b-7ab7-45d5-b2f4-1ef9267de06c):
data model + Notion sync, discovery + extraction, fit scoring (hard gate
deferred to phase 6), and resume tailoring + drafted answers.

**What this does today:** pulls new postings from your Greenhouse/Ashby/Lever
watchlist and LinkedIn job-alert emails, extracts structured requirements,
scores each job 0–100 against your three CV categories, builds a tailored
.docx/.pdf resume from the matching base CV, drafts evergreen application
answers, and writes the result to your Notion Job Tracker 2026 database for
you to review.

**What this does NOT do yet:** auto-submit anywhere, extract a specific
posting's custom application questions, or learn from your decisions. Those
are phases 5–6.

## 1. One-time setup

### Notion
1. Go to https://www.notion.so/my-integrations and create a new internal
   integration. Copy its secret.
2. Open the **Job Tracker 2026** database in Notion → `···` menu →
   **Connections** → add the integration you just created.
3. Copy the database id out of its URL: `notion.so/<workspace>/<DATABASE_ID>?v=...`.
   (The Notion schema already has the fields this pipeline needs — Fit
   Score, Score Reason, Source, Applied Via, and Status now includes
   Rejected/Offer — these were added directly.)

### Anthropic
Grab an API key from https://console.anthropic.com/settings/keys.

### Put both in `.env`
```
cp .env.example .env
# then fill in NOTION_TOKEN, NOTION_DATABASE_ID, ANTHROPIC_API_KEY
```

### Company watchlist
```
cp config/companies.example.yaml config/companies.yaml
```
Fill in the Greenhouse/Ashby/Lever slugs for companies you're actually
targeting (see comments in the file for how to find each slug).

### Your base CVs
Already done — `config/cvs/*.md` is pre-filled with the text of your three
existing category CVs (`Hamideh_Ahooei_CV_AI_Transformation_Consultant.docx`,
`..._Technical_Business_Analyst.docx`, `..._Implementation_FDE.docx`) from
your CV folder. The scorer reads these as your real, documented experience —
this is what keeps "leadership" on your resume from satisfying "8 years of
Engineering Management." If you update those CVs later, re-paste the text
into the matching `config/cvs/*.md` file so scoring stays current.

### LinkedIn Gmail intake (optional but recommended)
LinkedIn has no public jobs API, so this reads the job-alert emails you
already get into a Gmail label on your own Gmail account.
1. In [Google Cloud Console](https://console.cloud.google.com/), create a
   project, enable the **Gmail API**, and create OAuth 2.0 credentials
   (Desktop app type). Download the JSON as `config/credentials.json`.
2. Run once to authorize:
   ```
   python -m pipeline.discovery.gmail_linkedin --authorize
   ```
   This opens a browser for you to sign in and caches a token — no need to
   repeat it on later runs.
3. Set `linkedin_gmail_label` in `config/companies.yaml` to the exact name
   of the Gmail label your LinkedIn alerts land in.

Indeed has no intake wired up yet — you weren't sure which account/folder
to use. Leave it out for now; it's easy to add a twin module later.

### Resume tailoring (phase 4)
Tailoring opens the matching base CV (already in your CV folder), reworks
the summary/skills/bullet wording for the specific JD using only facts
already in that CV, and exports a new `.docx` + `.pdf` named
`Hamideh_Ahooei_<Company>_<Role>.*` — the same naming convention you already
use. It needs **LibreOffice** installed (for the PDF step) wherever it runs,
plus `CV_FOLDER_PATH` set in `.env` to your CV folder's path.

Two ways to run this step:
- **Standalone (`python main.py`)** — works if the machine running the
  pipeline has LibreOffice installed. Set `CV_FOLDER_PATH` and
  `CV_OUTPUT_DIR` in `.env` to real paths on that machine.
- **Interactively, in a Cowork session connected to your computer** — no
  setup needed; LibreOffice and python-docx are already available there
  (this is how phase 4 was built and tested). Just ask Claude to run the
  tailoring step and point it at a scored job; it reads/writes your CV
  folder directly through the device connection. This is the easiest path
  until `CV_FOLDER_PATH` is configured for unattended runs.

If `CV_FOLDER_PATH` isn't set, `python main.py` skips tailoring and jobs sit
at "scored" (not yet in Notion) until it's configured — nothing is lost, a
later run picks them up.

### Install dependencies
```
pip install -r requirements.txt
```

## 2. Run it

```
python main.py
```

This is safe to run repeatedly — every job is deduplicated by a hash of
company + title + JD text, so a re-run only processes what's new. Progress
prints to stdout: how many new postings were found, and each job's score
and outcome (scored vs. skipped for being under 70).

Check the Notion database afterward — every job scoring 70+ that made it
through tailoring shows up there with its Fit Score, Score Reason, Source,
CV to Use, and Notes (what was emphasized, plus any gaps the CV genuinely
couldn't cover), Status = "To Apply", ready for you to review like normal.
The tailored resume and drafted answers live in the pipeline DB and, for
the resume, in your CV output folder — the Notion row's Notes field names
the file.

## 3. Run it on a schedule

**Cron (simplest, if this runs on a machine that's usually on):**
```
# crontab -e
0 7 * * * cd /path/to/job_pipeline && /path/to/venv/bin/python main.py >> run.log 2>&1
```

**GitHub Actions (if you'd rather not depend on a machine being on):**
Add `.github/workflows/pipeline.yml`:
```yaml
name: job-pipeline
on:
  schedule:
    - cron: "0 12 * * *"
  workflow_dispatch: {}
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: python main.py
        env:
          NOTION_TOKEN: ${{ secrets.NOTION_TOKEN }}
          NOTION_DATABASE_ID: ${{ secrets.NOTION_DATABASE_ID }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```
Note: the SQLite pipeline DB (`data/pipeline.db`) needs to persist between
runs for dedup and the future learning loop to work — on GitHub Actions
you'd need to commit it back or use a cache/artifact step. Cron on a
machine that stays around is simpler for that reason.

## 4. Project layout

```
main.py                          # entry point
pipeline/
  db.py                          # SQLite pipeline DB (audit trail, dedup)
  notion_sync.py                 # write tailored jobs to Notion, read Status back
  extraction.py                  # raw JD -> structured requirements (Claude)
  scoring.py                     # structured JD -> fit score per CV category (Claude)
  tailoring.py                   # structured JD -> tailored .docx/.pdf (Claude + python-docx + LibreOffice)
  answers.py                     # structured JD -> drafted evergreen application answers (Claude)
  orchestrator.py                # discover -> extract -> score -> tailor -> sync
  discovery/
    greenhouse.py                # Greenhouse job-board API
    ashby.py                     # Ashby job-board API
    lever.py                     # Lever job-board API
    gmail_linkedin.py            # LinkedIn job-alert emails via Gmail API
config/
  companies.example.yaml         # copy to companies.yaml
  cvs/                           # your three base CVs, pre-filled as text for scoring/answers
  calibration_notes.md           # empty until phase 6 exists
data/
  pipeline.db                    # created on first run
  tailored/                      # default tailored-resume output if CV_OUTPUT_DIR isn't set
```

## 5. What's next (phases 5–6)

- **Phase 5** — auto-submit on Greenhouse/Ashby/Lever only, behind your
  approval in Notion (Status left at "To Apply" / moved off "Skip"). This is
  also when per-posting custom application questions get extracted (needs
  the same browser automation as submitting), replacing the evergreen
  answers from phase 4.
- **Phase 6** — the learning loop: reads `jobs_with_decisions()` from the
  pipeline DB weekly, has Claude write calibration notes into
  `config/calibration_notes.md`, which every scoring run already reads.
  This is also when the hard gate gets reintroduced — learned from your
  actual approve/skip pattern instead of hand-specified.
