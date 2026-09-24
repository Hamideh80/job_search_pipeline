"""Broad job discovery via LinkedIn's public guest search API.

No authentication required. Searches LinkedIn's guest search endpoint for
job postings matching keyword + location pairs configured in companies.yaml
under the `linkedin_searches` key.

Each result's JD text and metadata (title, company) are fetched via the
existing gmail_linkedin helpers, so the returned posting format is identical
to other ATS fetchers.

Configuration in companies.yaml:

    linkedin_searches:
      - keywords: "forward deployed engineer"
        location: "Canada"
      - keywords: "solutions architect AI"
        location: "Canada"

Rate-limiting: 1 s sleep between JD fetches; 0.5 s between search pages.
Keep max_results <= 25 per query per run to stay below informal rate limits.
"""
import re
import time

import requests

from .gmail_linkedin import JD_HEADERS, fetch_job_data

_SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
_JOB_ID_RE  = re.compile(r'data-entity-urn="urn:li:jobPosting:(\d+)"')


def search_jobs(keywords: str, location: str, max_results: int = 25) -> list[dict]:
    """Return postings matching keywords + location from LinkedIn guest search.

    Returns list of {company, title, link, jd_raw, source} dicts — same
    format as greenhouse.fetch_postings() and other ATS discovery modules.
    """
    job_ids = _fetch_job_ids(keywords, location, max_results)
    postings = []
    for job_id in job_ids:
        link = f"https://www.linkedin.com/jobs/view/{job_id}/"
        try:
            time.sleep(1.0)
            data = fetch_job_data(link)
        except Exception:  # noqa: BLE001 — network errors must not abort the run
            continue
        if not data["jd_raw"]:
            continue
        postings.append({
            "company": data["company"] or "Unknown (LinkedIn Search)",
            "title":   data["title"]   or "Unknown (see JD)",
            "link":    link,
            "jd_raw":  data["jd_raw"],
            "source":  "LinkedIn",
        })
    return postings


def _fetch_job_ids(keywords: str, location: str, max_results: int) -> list[str]:
    """Collect job IDs from paginated LinkedIn guest search results."""
    seen: dict[str, None] = {}  # insertion-ordered dedup
    start = 0
    per_page = 25

    while len(seen) < max_results:
        try:
            resp = requests.get(
                _SEARCH_URL,
                params={"keywords": keywords, "location": location,
                        "start": start, "sortBy": "DD"},
                headers=JD_HEADERS,
                timeout=20,
            )
            resp.raise_for_status()
        except requests.RequestException:
            break

        found = _JOB_ID_RE.findall(resp.text)
        if not found:
            break
        for jid in found:
            seen[jid] = None
        if len(found) < per_page:
            break
        start += per_page
        time.sleep(0.5)

    return list(seen)[:max_results]
