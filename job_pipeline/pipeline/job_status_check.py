"""Lightweight check: is a job still accepting applications?

Makes one HTTP request per job — no AI call. Called before extraction so
closed jobs never consume scoring quota.

LinkedIn-specific: the guest job-detail API returns an HTML fragment that
includes "No longer accepting applications" or "Not currently accepting
applications" in a visible element when the posting is closed. The page
still returns HTTP 200, so a status-code check alone is not enough.

For ATS links (Greenhouse/Ashby/Lever) we check the same closed phrases
in addition to HTTP 404/410.

On any network error we assume open (True) — better to waste one scoring
call than to miss a real opportunity.
"""
import re
import time

import requests

# How long to wait for a response before giving up (seconds).
_TIMEOUT = 10

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Phrases that indicate a closed posting, case-insensitive.
_CLOSED_PHRASES = re.compile(
    r"no longer accepting applications"
    r"|not currently accepting applications"
    r"|not accepting applications"
    r"|this job is no longer"
    r"|job is no longer available"
    r"|position has been filled"
    r"|posting.*(?:closed|expired|no longer active)"
    r"|application.*(?:closed|no longer accepted)",
    re.IGNORECASE,
)

# HTTP status codes that definitively indicate the posting is gone.
_CLOSED_STATUS_CODES = {404, 410}

# Extract LinkedIn job ID from standard and tracking URLs.
_LINKEDIN_JOB_ID_RE = re.compile(r"/jobs/(?:view|search)/(\d+)")
_LINKEDIN_GUEST_API = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"


def _linkedin_job_id(url: str) -> str | None:
    m = _LINKEDIN_JOB_ID_RE.search(url or "")
    return m.group(1) if m else None


def _fetch(url: str) -> tuple[int, str]:
    """Return (status_code, body_text). Returns (0, '') on network error."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT, allow_redirects=True)
        return resp.status_code, resp.text
    except Exception:  # noqa: BLE001
        return 0, ""


def is_job_open(url: str) -> bool:
    """Return True if the posting appears to still be accepting applications.

    Returns True (open) on any network failure so we never silently skip
    a job we couldn't reach.
    """
    if not url:
        return True

    job_id = _linkedin_job_id(url)
    if job_id:
        # Use the lightweight guest API fragment — much faster than the full page.
        api_url = _LINKEDIN_GUEST_API.format(job_id)
        status, body = _fetch(api_url)
    else:
        status, body = _fetch(url)

    if status == 0:
        # Network error — assume open.
        return True
    if status in _CLOSED_STATUS_CODES:
        return False
    if body and _CLOSED_PHRASES.search(body):
        return False
    return True
