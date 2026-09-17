"""Ashby job-board discovery.

Uses Ashby's public job board API:
https://api.ashbyhq.com/posting-api/job-board/{org_name}

`org_name` is the slug in the company's Ashby job board URL
(jobs.ashbyhq.com/<org_name>).
"""
import requests

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board/{org}"


def fetch_postings(org_name: str) -> list[dict]:
    resp = requests.get(
        BASE_URL.format(org=org_name), params={"includeCompensation": "false"}, timeout=30
    )
    resp.raise_for_status()
    data = resp.json()
    postings = []
    for job in data.get("jobs", []):
        postings.append({
            "company": org_name,
            "title": job["title"],
            "link": job.get("jobUrl") or job.get("applyUrl"),
            "jd_raw": job.get("descriptionPlain") or job.get("description", ""),
            "source": "Ashby",
        })
    return postings
