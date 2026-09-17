"""Lever job-board discovery.

Uses Lever's public postings API:
https://api.lever.co/v0/postings/{company}?mode=json

`company` is the slug in the company's Lever job board URL
(jobs.lever.co/<company>).
"""
import requests

BASE_URL = "https://api.lever.co/v0/postings/{company}"


def fetch_postings(company: str) -> list[dict]:
    resp = requests.get(BASE_URL.format(company=company), params={"mode": "json"}, timeout=30)
    resp.raise_for_status()
    postings = []
    for job in resp.json():
        desc = job.get("descriptionPlain") or job.get("description", "")
        postings.append({
            "company": company,
            "title": job["text"],
            "link": job.get("hostedUrl"),
            "jd_raw": desc,
            "source": "Lever",
        })
    return postings
