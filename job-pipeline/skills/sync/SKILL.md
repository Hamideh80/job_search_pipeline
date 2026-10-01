# Notion Sync Stage

Push shortlisted jobs to the Notion dashboard and poll for human approve/skip decisions.

Run:
```
python job-pipeline/skills/sync/run.py
```

After it completes, report:
- How many new Notion pages were created
- How many jobs are now approved (ready for tailoring)
- How many are still pending human review
- How many were skipped by the human
- Any sync errors

No AI is used in this stage — it is a direct Python operation.
