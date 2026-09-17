"""LinkedIn job-alert intake via Gmail.

LinkedIn has no public jobs API, and scraping it while logged in is risky
(bot detection, ToS) and brittle. Instead this reads the job-alert emails
you already receive into a Gmail label on your own Gmail account, and
pulls the job links out of them.

Setup (one-time, see README):
  1. Make sure LinkedIn's job-alert emails land in a Gmail label -- either
     an existing filter, or a new one you create for this.
  2. Enable the Gmail API in a Google Cloud project and download OAuth
     credentials as config/credentials.json.
  3. Run `python -m pipeline.discovery.gmail_linkedin --authorize` once to
     complete the OAuth flow and cache a token at config/gmail_token.json.

Indeed has no intake wired up yet -- you weren't sure an account/folder
exists for it. Add a twin of this module once you confirm one, or route
Indeed job links in some other way (e.g. a shared note you paste into).

Best-effort JD fetch: LinkedIn often blocks unauthenticated fetches of the
full job page. `fetch_jd_text` tries, and returns None on failure -- in
that case the orchestrator should skip the job rather than score it on an
empty JD (extraction/scoring both need real requirement text to be
trustworthy).
"""
import base64
import re
from pathlib import Path

import requests

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
CREDENTIALS_PATH = CONFIG_DIR / "credentials.json"
TOKEN_PATH = CONFIG_DIR / "gmail_token.json"

LINKEDIN_LINK_RE = re.compile(r"https://www\.linkedin\.com/comm/jobs/view/\d+[^\s\"<>]*")
JD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def _get_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def fetch_job_links(label_name: str = "LinkedIn Job Alerts", max_messages: int = 50) -> list[str]:
    """Returns job-posting links pulled out of alert emails in the given
    Gmail label. Titles/companies aren't reliably parseable from the alert
    email itself -- fetch_jd_text (or the extraction step) reads the job
    page for those."""
    service = _get_service()
    label_id = _find_label_id(service, label_name)
    if not label_id:
        raise RuntimeError(f"No Gmail label named {label_name!r} found")

    results = service.users().messages().list(
        userId="me", labelIds=[label_id], maxResults=max_messages
    ).execute()
    links: set[str] = set()
    for msg_meta in results.get("messages", []):
        msg = service.users().messages().get(
            userId="me", id=msg_meta["id"], format="full"
        ).execute()
        body = _extract_body(msg)
        links.update(LINKEDIN_LINK_RE.findall(body))
    return sorted(links)


def fetch_jd_text(job_link: str) -> str | None:
    """Best-effort fetch of the job page text. Returns None if LinkedIn
    blocks the request -- caller should skip rather than score a blank JD."""
    try:
        resp = requests.get(job_link, headers=JD_HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    text = re.sub(r"<[^>]+>", " ", resp.text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) > 200 else None


def _find_label_id(service, label_name: str) -> str | None:
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label["name"].lower() == label_name.lower():
            return label["id"]
    return None


def _extract_body(message: dict) -> str:
    def walk(part) -> str:
        data = part.get("body", {}).get("data")
        text = base64.urlsafe_b64decode(data).decode("utf-8", "ignore") if data else ""
        for sub in part.get("parts", []) or []:
            text += walk(sub)
        return text
    return walk(message.get("payload", {}))


if __name__ == "__main__":
    import sys
    if "--authorize" in sys.argv:
        _get_service()
        print("Gmail authorized, token cached at", TOKEN_PATH)
