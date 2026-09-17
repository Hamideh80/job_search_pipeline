# Job Search Pipeline — Phases 1–3

Implements the first milestone from the [build-plan doc](https://claude.ai/code/artifact/aa39646b-7ab7-45d5-b2f4-1ef9267de06c):
data model + Notion sync, discovery + extraction, and fit scoring (hard
gate deferred to phase 6).

**What this does today:** pulls new postings from your Greenhouse/Ashby/Lever
watchlist and LinkedIn job-alert emails, extracts structured requirements,
scores each job 0–100 against your three CV categories, and writes the
result to your Notion Job Tracker 2026 database for you to review.

**What this does NOT do yet:** tailor a resume, draft application answers,
auto-submit anywhere, or learn from your decisions. Those are phases 4–6.

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
Drop your three base CVs into `config/cvs/` as described in
`config/cvs/README.md`. The scorer reads these as your real, documented
experience — this is what keeps "leadership" on your resume from
satisfying "8 years of Engineering Management."

### LinkedIn Gmail intake (optional but recommended)
LinkedIn has no public jobs API, so this reads the job-alert emails you
already get into a Gmail label on `hamidehaahooei@gmail.com`.
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

Check the Notion database afterward — every job scoring 70+ shows up there
with its Fit Score, Score Reason, and Source, Status = "To Apply", ready
for you to review like normal.

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
  notion_sync.py                 # write scored jobs to Notion, read Status back
  extraction.py                  # raw JD -> structured requirements (Claude)
  scoring.py                     # structured JD -> fit score per CV category (Claude)
  orchestrator.py                # discover -> extract -> score -> sync
  discovery/
    greenhouse.py                # Greenhouse job-board API
    ashby.py                     # Ashby job-board API
    lever.py                     # Lever job-board API
    gmail_linkedin.py            # LinkedIn job-alert emails via Gmail API
config/
  companies.example.yaml         # copy to companies.yaml
  cvs/                           # your three base CVs go here
  calibration_notes.md           # empty until phase 6 exists
data/
  pipeline.db                    # created on first run
```

## 5. What's next (phases 4–6)

- **Phase 4** — resume tailoring + drafted application answers, landing in
  the same Notion row.
- **Phase 5** — auto-submit on Greenhouse/Ashby/Lever only, behind your
  approval in Notion (Status left at "To Apply" / moved off "Skip").
- **Phase 6** — the learning loop: reads `jobs_with_decisions()` from the
  pipeline DB weekly, has Claude write calibration notes into
  `config/calibration_notes.md`, which every scoring run already reads.
  This is also when the hard gate gets reintroduced — learned from your
  actual approve/skip pattern instead of hand-specified.
