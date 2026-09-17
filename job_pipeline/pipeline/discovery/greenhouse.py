"""Greenhouse job-board discovery.

Uses the public, unauthenticated job-board API:
https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true

`board_token` is the slug Greenhouse assigns a company's job board, usually
visible in the URL of their careers page (boards.greenhouse.io/<token>).
"""
import re

import requests

BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def fetch_postings(board_token: str) -> list[dict]:
    resp = requests.get(BASE_URL.format(token=board_token), params={"content": "true"}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    postings = []
    for job in data.get("jobs", []):
        postings.append({
            "company": board_token,
            "title": job["title"],
            "link": job["absolute_url"],
            "jd_raw": _strip_html(job.get("content", "")),
            "source": "Greenhouse",
        })
    return postings


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()
