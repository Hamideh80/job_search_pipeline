# Discovery Stage

Pull new job postings from all configured ATS sources (Greenhouse, Ashby, Lever, LinkedIn), then apply the relevance filter and availability check.

Run:
```
python job-pipeline/skills/discover/run.py
```

After it completes, read the output and report:
- New jobs found per source
- How many passed the relevance filter vs were skipped as irrelevant
- How many were skipped as closed (no longer accepting applications)
- Any errors encountered

If there are errors, read the relevant error message and suggest a fix.
