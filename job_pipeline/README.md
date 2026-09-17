# Job Search Pipeline — Phases 1–6 (complete)

Implements all six phases from the [build-plan doc](https://claude.ai/code/artifact/aa39646b-7ab7-45d5-b2f4-1ef9267de06c):
data model + Notion sync, discovery + extraction, fit scoring (hard gate
deferred, then reintroduced as a learned pattern in phase 6), resume
tailoring + drafted answers, approval-gated auto-apply, and a weekly
learning loop that recalibrates scoring from your own decisions.

**What this does today:** pulls new postings from your Greenhouse/Ashby/Lever
watchlist and LinkedIn job-alert emails, extracts structured requirements,
scores each job 0–100 against your three CV categories, builds a tailored
.docx/.pdf resume from the matching base CV, drafts evergreen application
answers, and writes the result to your Notion Job Tracker 2026 database for
you to review. Once you set a job's Notion Status to **Approved**, it will
(for Greenhouse/Ashby/Lever only) fill out the real application form,
answer its specific custom questions, and — only once you've explicitly
turned on real submission — click Submit. Separately, on whatever cadence
you run it, the learning loop looks at your approve/skip decisions and any
interview outcomes and rewrites its own calibration notes so future scoring
gets closer to your actual judgment over time.

**What this does NOT do:** touch LinkedIn/Indeed applications (those stay
fully manual — you apply yourself using the tailored resume and drafted
answers), or submit anything for real until you've reviewed dry-run
screenshots and explicitly opted in. See **Phase 5 safety design** below
before turning that on.

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

### Auto-apply (phase 5, optional)
Only relevant once you're ready to have jobs auto-filled/submitted. Read
**Phase 5 safety design** below first, then:
1. Fill `APPLICANT_FIRST_NAME` / `APPLICANT_LAST_NAME` / `APPLICANT_EMAIL` /
   `APPLICANT_PHONE` in `.env`.
2. `cp config/applicant_notes.example.md config/applicant_notes.md` and fill
   in the free-text context (work authorization, notice period, salary
   expectation, etc) used to answer each posting's custom questions. Leave
   a line blank to have that field flagged for your manual input instead of
   guessed.
3. Install a browser for Playwright: `python -m playwright install chromium`.
4. Leave `AUTO_SUBMIT_CONFIRMED=false` (the default) until you've reviewed
   several dry-run screenshots.

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

**To have a job auto-applied to** (Greenhouse/Ashby/Lever only — see
**Phase 5** below): change its Status in Notion to **Approved**. The next
`python main.py` run will fill out the real application form and take a
screenshot for you to review at `data/screenshots/` (or
`APPLY_SCREENSHOT_DIR`) — nothing is submitted unless you've turned on
`AUTO_SUBMIT_CONFIRMED`. Setting Status to **Skip** or **No longer
available** instead records that as a skip, which the learning loop later
reads as a signal. Leaving Status at "To Apply" untouched is treated as
"haven't decided yet," not as approval.

### Learning loop (phase 6)
Run this separately, on a slower cadence (weekly is reasonable — it needs a
batch of new decisions to say anything useful):
```
python learning_run.py
```
It polls Notion for Status changes, reads every job you've approved or
skipped (plus any recorded Interview/Rejected/Offer outcome), and asks
Claude to look for real patterns — then rewrites
`config/calibration_notes.md`, which `pipeline/scoring.py` already reads on
every score call. With fewer than 5 decided jobs it prints a notice and
leaves your notes untouched rather than guessing from too little data. See
**Phase 6** below for what "patterns" means here.

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
      - run: python -m playwright install --with-deps chromium
      - run: python main.py
        env:
          NOTION_TOKEN: ${{ secrets.NOTION_TOKEN }}
          NOTION_DATABASE_ID: ${{ secrets.NOTION_DATABASE_ID }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          APPLICANT_FIRST_NAME: ${{ secrets.APPLICANT_FIRST_NAME }}
          APPLICANT_LAST_NAME: ${{ secrets.APPLICANT_LAST_NAME }}
          APPLICANT_EMAIL: ${{ secrets.APPLICANT_EMAIL }}
          APPLICANT_PHONE: ${{ secrets.APPLICANT_PHONE }}
          # Leave unset (defaults to false) until you're ready for real submissions.
          # AUTO_SUBMIT_CONFIRMED: "true"
```
Note: the SQLite pipeline DB (`data/pipeline.db`) needs to persist between
runs for dedup, apply-flag tracking, and the learning loop to work — on
GitHub Actions you'd need to commit it back or use a cache/artifact step.
Cron on a machine that stays around is simpler for that reason. Add a
second, weekly-cadence workflow that runs `python learning_run.py` instead
of `python main.py` for the learning loop.

## 4. Project layout

```
main.py                          # entry point: discover -> ... -> apply
learning_run.py                  # phase 6 entry point, run on its own (weekly) cadence
pipeline/
  db.py                          # SQLite pipeline DB (audit trail, dedup, decisions, outcomes)
  notion_sync.py                 # write tailored jobs to Notion, read Status back (explicit "Approved")
  extraction.py                  # raw JD -> structured requirements (Claude)
  scoring.py                     # structured JD -> fit score per CV category (Claude, reads calibration notes)
  tailoring.py                   # structured JD -> tailored .docx/.pdf (Claude + python-docx + LibreOffice)
  answers.py                     # structured JD -> drafted evergreen application answers (Claude)
  apply.py                       # fills/submits real applications (Claude + Playwright) -- see safety design below
  learning.py                    # decision history -> recalibrated config/calibration_notes.md (Claude)
  orchestrator.py                # discover -> extract -> score -> tailor -> sync -> apply
  discovery/
    greenhouse.py                # Greenhouse job-board API
    ashby.py                     # Ashby job-board API
    lever.py                     # Lever job-board API
    gmail_linkedin.py            # LinkedIn job-alert emails via Gmail API
config/
  companies.example.yaml         # copy to companies.yaml
  cvs/                           # your three base CVs, pre-filled as text for scoring/answers
  calibration_notes.md           # rewritten by learning_run.py; empty/starter text until you have 5+ decisions
  applicant_notes.example.md     # copy to applicant_notes.md -- work auth, notice period, salary expectation, etc
data/
  pipeline.db                    # created on first run -- system of record for everything, including the learning loop
  tailored/                      # default tailored-resume output if CV_OUTPUT_DIR isn't set
  screenshots/                   # filled-application screenshots for you to review before/instead of real submission
```

## 5. Phase 5 — auto-apply, and its safety design

This is the part of the pipeline that can act on the real world (filling
and potentially submitting a job application), so it's built to fail safe:

- **Scope is narrow on purpose.** Only Greenhouse, Ashby, and Lever
  postings are touched — these are the three ATS platforms with public,
  stable enough form structures to automate reasonably. LinkedIn and Indeed
  jobs are scored and tailored like everything else, but you always apply
  to those yourself.
- **Nothing happens without an explicit "Approved."** `notion_sync.py`
  only ever treats an actual **Approved** Status as approval — leaving a
  job at "To Apply" (i.e., you haven't looked at it yet) is a different
  state from "go ahead," on purpose, since this step can take a real,
  hard-to-undo action.
- **Dry-run by default.** `AUTO_SUBMIT_CONFIRMED` defaults to `false`. With
  it off, the pipeline fills out the real form (name/email/phone, resume
  upload, and every custom question it can confidently answer from your CV
  and `applicant_notes.md`) and takes a full-page screenshot — but never
  clicks Submit. The job moves to `ready_to_submit` in the pipeline DB, not
  `applied`, so it's obvious nothing went out yet.
- **A field it can't confidently answer is flagged, not guessed.** Custom
  questions the CV/notes don't clearly settle (most often salary
  expectation, if you didn't fill that into `applicant_notes.md`) come back
  as `None` and get recorded in `apply_flags` — and, importantly, even with
  `AUTO_SUBMIT_CONFIRMED=true`, a job with any unanswered flag is **not**
  submitted; it stays at `ready_to_submit` for you to fill that field
  yourself and re-run.
- **Recommended rollout:** leave `AUTO_SUBMIT_CONFIRMED=false`, run the
  pipeline, open a few screenshots in `data/screenshots/` and check the
  fill quality and drafted answers against the real form. Only once you
  trust it, set `AUTO_SUBMIT_CONFIRMED=true` in `.env`.
- **Selector coverage differs by platform.** The Greenhouse form fields
  (`#first_name`, `#resume`, `question_<id>` custom fields, etc.) were
  verified against a real, live Greenhouse posting. Ashby and Lever
  selectors are implemented the same way but were **not** verified against
  a live posting (no open postings were available to test against at build
  time) — treat their dry-run screenshots with extra scrutiny the first few
  times, and expect to possibly need to adjust selectors in
  `pipeline/apply.py` if a form layout doesn't match.
- **Runs where it can actually reach the ATS sites.** This step needs
  normal internet access to the job board's domain — it won't work from a
  restricted/sandboxed environment. Run it from your own machine, a normal
  CI runner, or a server with unrestricted outbound HTTPS.

## 6. Phase 6 — the learning loop

`python learning_run.py`, run on its own cadence (weekly is a reasonable
starting point — it needs a real batch of new decisions to say anything
useful, and re-running immediately after the last run just reproduces the
same notes).

What it actually does: reads every job you've explicitly approved or
skipped in Notion, plus any recorded Interview/Rejected/Offer outcome, and
asks Claude to find *repeated* patterns — not each job in isolation — then
rewrites `config/calibration_notes.md` from scratch (not appended-to, so
stale guidance doesn't pile up). `pipeline/scoring.py` already reads that
file on every score call, so no code changes are needed for newly-learned
calibration to take effect on the next `python main.py` run.

Examples of what it looks for: categories or titles that get approved vs.
skipped even at similar fit scores; specific gap types (a particular
years-of-experience shortfall, a seniority mismatch) you've shown you will
or won't tolerate; which category/company/seniority combinations are
actually converting to interviews, which is a stronger signal than an
approve/skip alone.

This is also where the hard reject gate you asked to defer comes back —
but **learned, not hand-specified**. If a pattern is strong and consistent
(not one or two data points — `MIN_DECISIONS_FOR_PATTERN` in
`pipeline/learning.py` requires at least 5 total decisions before it will
state any pattern at all), the notes can say so explicitly as a
"near-automatic skip," which `scoring.py`'s prompt is told to weigh
heavily. It stays advisory text the scorer reasons about, not a code-level
reject that can never be overridden by an unusually strong JD — if you
later want a literal hard-coded reject instead, that's a small, deliberate
follow-up change to `scoring.py`/`orchestrator.py`, not something this loop
does on its own.
