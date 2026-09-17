"""Two-way sync between the pipeline DB and the Notion "Job Tracker 2026" database.

Pipeline -> Notion: create a page for every scored job (Fit Score, Score
Reason, Source, CV to Use, Status = "To Apply").
Notion -> Pipeline: poll synced pages for Status changes to pick up your
approve/skip decision, and outcomes (Interview / Rejected / Offer).

NOTION_DATABASE_ID should be the database id from the Job Tracker 2026 URL
(https://www.notion.so/<workspace>/<DATABASE_ID>?v=...), not the
collection:// data-source id -- the public Notion API's pages.create takes
a database_id.
"""
import os
from datetime import date

from notion_client import Client

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]

client = Client(auth=NOTION_TOKEN)

OUTCOME_STATUSES = {"Interview", "Rejected", "Offer"}
SKIP_STATUSES = {"Skip", "No longer available"}


def create_job_page(job: dict) -> str:
    """job: a pipeline DB row (as a dict). Returns the new Notion page id."""
    props = {
        "Job Title": {"title": [{"text": {"content": job["title"]}}]},
        "Company": {"rich_text": [{"text": {"content": job["company"]}}]},
        "Status": {"multi_select": [{"name": "To Apply"}]},
        "Source": {"select": {"name": job["source"]}},
        "Applied Via": {"select": {"name": "N/A"}},
    }
    if job.get("link"):
        props["Link"] = {"url": job["link"]}
    if job.get("fit_score") is not None:
        props["Fit Score"] = {"number": job["fit_score"]}
    if job.get("score_reason"):
        props["Score Reason"] = {"rich_text": [{"text": {"content": job["score_reason"][:2000]}}]}
    if job.get("cv_category"):
        props["CV to Use"] = {"select": {"name": job["cv_category"]}}

    page = client.pages.create(parent={"database_id": NOTION_DATABASE_ID}, properties=props)
    return page["id"]


def mark_applied(page_id: str, applied_via: str = "Auto") -> None:
    client.pages.update(
        page_id=page_id,
        properties={
            "Status": {"multi_select": [{"name": "Applied"}]},
            "Applied Via": {"select": {"name": applied_via}},
            "Apply date": {"date": {"start": date.today().isoformat()}},
        },
    )


def fetch_status(page_id: str) -> list[str]:
    page = client.pages.retrieve(page_id=page_id)
    return [opt["name"] for opt in page["properties"]["Status"]["multi_select"]]


def poll_decisions(conn) -> None:
    """For every synced job without a recorded decision yet, check its
    Notion Status and record approve/skip (and any outcome) back into the
    pipeline DB. Leaving Status at "To Apply" counts as approved; setting
    it to "Skip" or "No longer available" counts as skipped."""
    from . import db

    rows = conn.execute(
        "SELECT id, notion_page_id FROM jobs "
        "WHERE notion_page_id IS NOT NULL AND decision IS NULL"
    ).fetchall()
    for row in rows:
        statuses = set(fetch_status(row["notion_page_id"]))
        if statuses & SKIP_STATUSES:
            db.update_job(conn, row["id"], decision="skipped")
        elif "To Apply" not in statuses:
            db.update_job(conn, row["id"], decision="approved")
        outcome = statuses & OUTCOME_STATUSES
        if outcome:
            db.update_job(conn, row["id"], outcome=sorted(outcome)[0])
