"""Notion sync -- Job Tracker 2026 is a DASHBOARD, not the pipeline's source
of truth. The pipeline DB (pipeline/db.py, SQLite) owns every piece of
processing state: dedup, job IDs, file paths, pipeline_status, timestamps.
Notion only ever gets written to for reporting, with exactly one field read
back in the other direction.

Pipeline -> Notion (one-way): create a page for every tailored job, with
just what you actually want to see there -- Company, Job Title, Link, Fit
Score, a short fit summary, which resume was used, Status, and the
relevant dates. Nothing about HOW the pipeline works internally (source
detection, tailoring flags, raw JD text, etc) needs to live in Notion, so
it doesn't.

Notion -> Pipeline (the one exception): your Status field is the approval
gate for phase 5 auto-apply. Setting Status to "Approved" is the only
thing that counts as approved -- leaving it at "To Apply" untouched is
undecided, not approved, since phase 5 can act on an approval by filling
and (optionally) submitting a real application. "Skip" / "No longer
available" records a skip, and Interview/Rejected/Offer feed the phase 6
learning loop. That one field is the entire read-back; nothing else in
Notion is ever read by the pipeline.

NOTION_DATABASE_ID should be the database id from the Job Tracker 2026 URL
(https://www.notion.so/<workspace>/<DATABASE_ID>?v=...), not the
collection:// data-source id -- the public Notion API's pages.create takes
a database_id.

Importing this module is safe without NOTION_TOKEN or NOTION_DATABASE_ID
set -- the Notion client is created lazily on first use, so tests and
read-only commands (status, discover) can import pipeline.orchestrator
without needing Notion credentials.
"""
import os
from datetime import date

OUTCOME_STATUSES = {"Interview", "Rejected", "Offer"}
SKIP_STATUSES = {"Skip", "No longer available"}
APPROVE_STATUSES = {"Approved"}

# ── lazy Notion client ────────────────────────────────────────────────────────

_notion_client = None


def _notion():
    """Return the Notion client, creating it on first call.

    Raises a clear RuntimeError (not a KeyError) if NOTION_TOKEN is absent,
    so the error message points directly at what needs to be set.
    """
    global _notion_client
    if _notion_client is None:
        token = os.environ.get("NOTION_TOKEN")
        if not token:
            raise RuntimeError(
                "NOTION_TOKEN is not set. Add it to your .env file. "
                "Required for Notion sync operations."
            )
        from notion_client import Client
        _notion_client = Client(auth=token)
    return _notion_client


def _db_id() -> str:
    """Return the Notion database ID, with a clear error if absent."""
    db_id = os.environ.get("NOTION_DATABASE_ID")
    if not db_id:
        raise RuntimeError(
            "NOTION_DATABASE_ID is not set. Add it to your .env file. "
            "Required for Notion sync operations."
        )
    return db_id


# ── public API ────────────────────────────────────────────────────────────────

def create_job_page(job: dict) -> str:
    """job: a pipeline DB row (as a dict). Returns the new Notion page id.
    Only sets the fields worth seeing on the dashboard -- Company, Job
    Title, Link, Fit Score, a short fit summary, which resume was used,
    Source, and Status."""
    props = {
        "Job Title": {"title": [{"text": {"content": job["title"]}}]},
        "Company": {"rich_text": [{"text": {"content": job["company"]}}]},
        "Status": {"multi_select": [{"name": "To Apply"}]},
        "Source": {"select": {"name": job["source"]}},
        "Applied Via": {"select": {"name": "N/A"}},
        "Date Added": {"date": {"start": date.today().isoformat()}},
    }
    if job.get("link"):
        props["Link"] = {"url": job["link"]}
    if job.get("fit_score") is not None:
        props["Fit Score"] = {"number": job["fit_score"]}
    if job.get("score_reason"):
        props["Score Reason"] = {"rich_text": [{"text": {"content": job["score_reason"][:2000]}}]}
    if job.get("cv_category"):
        props["CV to Use"] = {"select": {"name": job["cv_category"]}}

    notes = _build_notes(job)
    if notes:
        props["Notes"] = {"rich_text": [{"text": {"content": notes[:2000]}}]}

    page = _notion().pages.create(parent={"database_id": _db_id()}, properties=props)
    return page["id"]


def _build_notes(job: dict) -> str:
    """Combines tailoring notes, any honesty-rule flags, and which resume
    file was used into one short blurb for the dashboard -- flags are
    things the JD wanted that the CV genuinely couldn't support, worth
    seeing before you approve."""
    import json

    parts = []
    if job.get("tailoring_notes"):
        parts.append(job["tailoring_notes"])
    flags = job.get("tailoring_flags")
    if flags:
        if isinstance(flags, str):
            flags = json.loads(flags) if flags.strip().startswith("[") else [flags]
        if flags:
            parts.append("Gaps not addressed: " + "; ".join(flags))
    if job.get("tailored_resume_docx"):
        parts.append(f"Resume used: {job['tailored_resume_docx'].rsplit('/', 1)[-1]}")
    return " ".join(parts)


def mark_applied(page_id: str, applied_via: str = "Auto") -> None:
    _notion().pages.update(
        page_id=page_id,
        properties={
            "Status": {"multi_select": [{"name": "Applied"}]},
            "Applied Via": {"select": {"name": applied_via}},
            "Apply date": {"date": {"start": date.today().isoformat()}},
        },
    )


def fetch_status(page_id: str) -> list[str]:
    page = _notion().pages.retrieve(page_id=page_id)
    return [opt["name"] for opt in page["properties"]["Status"]["multi_select"]]


def poll_decisions(conn) -> None:
    """For every shortlisted job without a recorded decision yet, check its
    Notion Status and record approve/skip back into the pipeline DB.

    Approved   → decision='approved', pipeline_status='approved'
                 (makes the job eligible for tailoring on the next run)
    Skipped    → decision='skipped',  pipeline_status='skipped_human'
    Outcome    → also recorded (Interview/Rejected/Offer) for the learning loop.

    Only an explicit "Approved" status counts as approved; "To Apply" (the
    default) stays undecided. This is the ONLY field ever read back from Notion.
    """
    from . import db

    rows = conn.execute(
        "SELECT id, notion_page_id FROM jobs "
        "WHERE notion_page_id IS NOT NULL AND decision IS NULL"
    ).fetchall()
    for row in rows:
        statuses = set(fetch_status(row["notion_page_id"]))
        if statuses & SKIP_STATUSES:
            db.update_job(conn, row["id"],
                          decision="skipped", pipeline_status="skipped_human")
        elif statuses & APPROVE_STATUSES:
            db.update_job(conn, row["id"],
                          decision="approved", pipeline_status="approved")
        outcome = statuses & OUTCOME_STATUSES
        if outcome:
            db.update_job(conn, row["id"], outcome=sorted(outcome)[0])
