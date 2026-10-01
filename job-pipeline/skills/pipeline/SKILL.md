# Job Search Pipeline — Full Run

Run the complete automated job search pipeline from discovery through application.

Work through each stage below **in order**. After each stage report what happened (counts + any errors) before moving on. If a stage produces zero actionable results (e.g. nothing to extract), skip forward to the next relevant stage rather than stopping.

---

**Stage 1 — Discover**
Use /job-pipeline:discover

**Stage 2 — Extract**
Use /job-pipeline:extract

**Stage 3 — Score**
Use /job-pipeline:score

**Stage 4 — Sync to Notion**
Use /job-pipeline:sync
Note: shortlisted jobs land here for human review. The pipeline continues automatically — tailoring and application only act on jobs already marked Approved in Notion from a previous run.

**Stage 5 — Tailor**
Use /job-pipeline:tailor

**Stage 6 — Apply**
Use /job-pipeline:apply

---

End with a summary table:

| Stage    | Result                        |
|----------|-------------------------------|
| Discover | N new jobs found              |
| Extract  | N extracted, N skipped        |
| Score    | N shortlisted, N below cutoff |
| Sync     | N pages created, N pending    |
| Tailor   | N tailored                    |
| Apply    | N submitted / N dry-run       |
