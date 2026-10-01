"""Discovery, relevance filtering, availability checking, and Notion sync.

AI stages (extract, score, tailor, apply, learn) have moved to the
job-pipeline Claude Code plugin. Run /job-pipeline:pipeline to execute
the full pipeline, or individual stage skills for each step.

Non-AI stages kept here:
  run_discovery()           — fetch new postings from ATS APIs + LinkedIn
  run_relevance_filter()    — cheap title/JD signal filter (no AI)
  run_availability_check()  — HTTP check for closed postings (no AI)
  run_notion_sync()         — push shortlisted jobs to Notion, poll decisions
"""
import os
from pathlib import Path

import yaml

from . import db, job_status_check, notion_sync, relevance
from .discovery import ashby, gmail_linkedin, greenhouse, lever, linkedin_search
from .progress import RunProgress

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# ── discovery ─────────────────────────────────────────────────────────────────

def load_watchlist() -> dict:
    path = CONFIG_DIR / "companies.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found -- copy config/companies.example.yaml to "
            "config/companies.yaml and fill in your target companies."
        )
    return yaml.safe_load(path.read_text()) or {}


def run_discovery(conn, progress: RunProgress) -> int:
    watchlist = load_watchlist()
    total_fetched = 0
    total_new = 0

    fetchers = {
        "greenhouse": greenhouse.fetch_postings,
        "ashby": ashby.fetch_postings,
        "lever": lever.fetch_postings,
    }
    for ats, fetch_fn in fetchers.items():
        for token in watchlist.get(ats, []):
            try:
                postings = fetch_fn(token)
            except Exception as exc:  # noqa: BLE001
                progress.error(f"[discovery] {ats}/{token} failed: {exc}")
                continue
            fetched = len(postings)
            added = sum(insert_discovered_job(conn, p) for p in postings)
            total_fetched += fetched
            total_new += added
            progress.info(
                f"[{ats}] {token} → {fetched} found, {added} new, {fetched - added} duplicate(s)"
            )

    if watchlist.get("linkedin_gmail_label"):
        found, added = _run_linkedin_intake(conn, watchlist["linkedin_gmail_label"], progress)
        total_fetched += found
        total_new += added

    for cfg in watchlist.get("linkedin_searches", []):
        found, added = _run_linkedin_search(conn, cfg, progress)
        total_fetched += found
        total_new += added

    total_dup = total_fetched - total_new
    progress.info(
        f"Discovery total: {total_fetched} fetched across all sources "
        f"→ {total_new} new, {total_dup} duplicate(s)"
    )
    return total_new


def _run_linkedin_intake(conn, label_name: str, progress: RunProgress) -> tuple[int, int]:
    """Ingest jobs from LinkedIn alert emails. Returns (found, added)."""
    try:
        links = gmail_linkedin.fetch_job_links(label_name)
    except Exception as exc:  # noqa: BLE001
        progress.error(f"[discovery] LinkedIn Gmail intake failed: {exc}")
        return 0, 0
    found = len(links)
    added = 0
    failed = 0
    for link in links:
        if db.job_link_exists(conn, link):
            continue
        job_data = gmail_linkedin.fetch_job_data(link)
        if not job_data["jd_raw"]:
            failed += 1
            progress.error(f"[discovery] could not fetch JD for {link}, skipping")
            continue
        posting = {
            "company": job_data["company"] or "Unknown (LinkedIn)",
            "title":   job_data["title"]   or "Unknown (see JD)",
            "link":    link,
            "jd_raw":  job_data["jd_raw"],
            "source":  "LinkedIn Gmail",
        }
        added += insert_discovered_job(conn, posting)
    dup = found - added - failed
    progress.info(
        f"LinkedIn Gmail → {found} link(s), {added} new, {dup} duplicate(s)"
        + (f", {failed} fetch error(s)" if failed else "")
    )
    return found, added


def _run_linkedin_search(conn, cfg: dict, progress: RunProgress) -> tuple[int, int]:
    """Run one keyword+location LinkedIn guest search. Returns (found, added)."""
    keywords = cfg.get("keywords", "")
    location = cfg.get("location", "Canada")
    max_results = int(cfg.get("max_results", 25))
    if not keywords:
        return 0, 0
    try:
        postings = linkedin_search.search_jobs(keywords, location, max_results)
    except Exception as exc:  # noqa: BLE001
        progress.error(f"[discovery] LinkedIn search '{keywords}' failed: {exc}")
        return 0, 0
    found = len(postings)
    added = 0
    for p in postings:
        if db.job_link_exists(conn, p.get("link", "")):
            continue
        added += insert_discovered_job(conn, p)
    progress.info(
        f"LinkedIn Search \"{keywords}\" → {found} found, {added} new, {found - added} duplicate(s)"
    )
    return found, added


def insert_discovered_job(conn, posting: dict) -> int:
    """Dedup-checked insert used by every discovery source. Returns 1 if new, 0 if duplicate."""
    jd_hash = db.hash_jd(posting["company"], posting["title"], posting["jd_raw"])
    if db.job_exists(conn, jd_hash):
        return 0
    db.insert_job(conn, **posting)
    return 1


# ── relevance filter (cheap, no AI) ──────────────────────────────────────────

def run_relevance_filter(conn, progress: RunProgress) -> None:
    """Apply the cheap two-gate relevance filter to all 'discovered' jobs.

    Gate 1 — title blocklist: obvious non-target role functions.
    Gate 2 — weighted title + JD signals: positive family evidence vs.
              hard-negative function indicators.

    Jobs that fail either gate are moved to 'skipped_irrelevant' with the
    reason stored in relevance_skip_reason. Jobs that pass stay at
    'discovered' and proceed to run_extraction().

    Safe to re-run: only processes jobs still at 'discovered' status.
    """
    rows = db.jobs_by_status(conn, "discovered")
    checked = len(rows)
    rejected = 0
    reason_counts: dict[str, int] = {}
    for row in rows:
        title  = row["title"]  or ""
        jd_raw = row["jd_raw"] or ""
        ok, reason = relevance.is_relevant(title, jd_raw)
        if not ok:
            db.update_job(conn, row["id"],
                          pipeline_status="skipped_irrelevant",
                          relevance_skip_reason=reason)
            progress.job_update(row["id"], title, row["company"],
                                "relevance", ok=False, extra=reason[:80])
            rejected += 1
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    passed = checked - rejected
    progress.info(f"Relevance: {checked} checked → {passed} passed, {rejected} rejected")
    for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
        progress.info(f"  skipped ×{count}: {reason}")


# ── availability check (no AI, runs before extraction) ───────────────────────

def run_availability_check(conn, progress: RunProgress) -> None:
    """Skip jobs that are no longer accepting applications.

    Makes one HTTP request per discovered job. Jobs where the posting page
    contains a closed-application notice (e.g. LinkedIn's 'No longer accepting
    applications') are moved to 'skipped_closed' before any AI call is made.

    Safe to re-run: only processes jobs still at 'discovered' status.
    Network errors are treated as open so we never silently discard a real job.
    """
    checked = 0
    closed = 0
    for row in db.jobs_by_status(conn, "discovered"):
        url = row["link"] or ""
        if not url:
            continue
        checked += 1
        if not job_status_check.is_job_open(url):
            db.update_job(conn, row["id"], pipeline_status="skipped_closed")
            progress.job_update(row["id"], row["title"], row["company"],
                                "closed", ok=False,
                                extra="no longer accepting applications")
            closed += 1
    progress.info(
        f"Availability: {checked} checked → {checked - closed} open, {closed} closed"
    )




# ── Notion sync (shortlisted → Pending Review; poll for human decisions) ──────

def run_notion_sync(conn, progress: RunProgress) -> None:
    """Creates a Notion dashboard row for every newly shortlisted job, then
    polls for Status changes (Approved / Skip) and advances pipeline_status
    accordingly.

    Only shortlisted jobs without a Notion page yet are synced. No tailoring
    data is available at this point — the human sees Company, Title, Link,
    Fit Score, Score Reason, and which CV family was selected. That is
    enough for an approve/skip decision.
    """
    synced_ids: list[int] = []
    n_sync_failed = 0
    rows = conn.execute(
        "SELECT * FROM jobs WHERE pipeline_status = 'shortlisted' AND notion_page_id IS NULL"
    ).fetchall()
    n_to_sync = len(rows)
    progress.info(f"Notion sync: {n_to_sync} new shortlisted job(s) to sync")
    for row in rows:
        try:
            page_id = notion_sync.create_job_page(dict(row))
        except Exception as exc:  # noqa: BLE001
            progress.error(f"[sync] job {row['id']} Notion sync failed: {exc}")
            progress.job_update(row["id"], row["title"], row["company"],
                                "sync", ok=False, extra=str(exc))
            n_sync_failed += 1
            continue
        # Status stays 'shortlisted' — we only record the Notion page id.
        # poll_decisions() below will advance it to 'approved' or 'skipped_human'.
        db.update_job(conn, row["id"], notion_page_id=page_id)
        progress.job_update(row["id"], row["title"], row["company"], "sync", ok=True)
        synced_ids.append(row["id"])

    notion_sync.poll_decisions(conn)

    # Report decision status for jobs synced this run (read back after polling).
    if synced_ids:
        placeholders = ",".join("?" * len(synced_ids))
        rows = conn.execute(
            f"SELECT id, title, company, decision FROM jobs WHERE id IN ({placeholders})",
            synced_ids,
        ).fetchall()
        for r in rows:
            approved = r["decision"] == "approved"
            progress.job_update(r["id"], r["title"], r["company"], "approved", ok=approved)

    # Overall decision tally across all synced jobs (not just this run).
    n_approved = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE decision = 'approved'"
    ).fetchone()[0]
    n_human_skipped = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE decision = 'skipped'"
    ).fetchone()[0]
    n_pending = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE pipeline_status = 'shortlisted' AND decision IS NULL"
    ).fetchone()[0]
    synced_ok = len(synced_ids)
    progress.info(
        f"Notion sync done: {synced_ok} page(s) created"
        + (f", {n_sync_failed} failed" if n_sync_failed else "")
    )
    progress.info(
        f"  Decisions (all time): {n_approved} approved, "
        f"{n_human_skipped} skipped by human, {n_pending} pending review"
    )


