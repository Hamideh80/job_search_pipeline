"""Ties discovery -> dedup -> extraction -> scoring -> Notion sync into one run.

Pipeline flow (post Step-5):
  discovered
  → extracted          (AI extraction; French-mandatory jobs hard-filtered here)
  → shortlisted        (score >= 70; Notion card created for human review)
    OR skipped_low_score  (score < 70; never shown to human)
  → [human approves in Notion]
  → approved           (poll_decisions advances shortlisted → approved)
  → tailored           (Master CV tailored to specific JD; only after approval)
  → ready_to_apply     (form filled + screenshot, dry-run when AUTO_SUBMIT_CONFIRMED=false)
  → applied            (real submission when AUTO_SUBMIT_CONFIRMED=true)

Safe to run repeatedly: dedup on JD hash means a re-run only processes
what's new, and every stage only picks up jobs still sitting at the
previous pipeline_status.

The pipeline DB is the only source of truth for processing state —
Notion is a one-way dashboard (see notion_sync.py), never read from
for discovery.
"""
import json
import os
import re
from pathlib import Path
from typing import Optional

import yaml

from . import answers, apply as apply_module, db, extraction, job_status_check, notion_sync, relevance, scoring, tailoring
from .discovery import ashby, gmail_linkedin, greenhouse, lever, linkedin_search
from .progress import RunProgress

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
SCORE_THRESHOLD = 70

# Cap how many jobs each AI stage processes per run to avoid rate-limit bursts.
# Override with MAX_JOBS_PER_RUN=50 in .env (set to 0 for unlimited).
_MAX_JOBS_PER_RUN = int(os.environ.get("MAX_JOBS_PER_RUN", "20"))

# ── French mandatory-language hard filter ─────────────────────────────────────
# Applied before any AI call in run_extraction. Hard-reject only when French
# is clearly stated as required. "Preferred", "asset", "nice to have", or
# ambiguous phrasing → allow through for normal scoring and human review.

_FRENCH_MANDATORY_PATTERNS = [
    r"french\s+(?:required|mandatory|essential|obligatoire)",
    r"must\s+(?:speak|be\s+bilingual\s+in|have\s+(?:proficiency|fluency)\s+in)\s+french",
    r"must\s+be\s+(?:proficient|fluent)\s+in\s+french",
    r"bilingual\s+(?:french[/-]english|english[/-]french)\s+(?:required|mandatory|essential)",
    r"(?:english\s+and\s+french|french\s+and\s+english)\s+(?:required|mandatory|essential|obligatoire)",
    r"professional\s+(?:proficiency|fluency)\s+in\s+french.{0,40}?(?:required|mandatory|essential)",
    r"fluent\s+(?:in\s+)?french.{0,40}?(?:required|mandatory|essential|is\s+required|is\s+mandatory)",
    r"fran[cç]ais\s+(?:obligatoire|requis|exig[ée]e?)",
]

_FRENCH_MANDATORY_RE = re.compile(
    "|".join(r"(?:" + p + r")" for p in _FRENCH_MANDATORY_PATTERNS),
    re.IGNORECASE,
)


def _is_french_mandatory(jd_raw: str) -> bool:
    return bool(_FRENCH_MANDATORY_RE.search(jd_raw or ""))


# ── CV text loading (for answers + apply custom questions) ────────────────────

_COMBINED_CV_FILE = "Hamideh_Ahooei_Master_CV_Combined.md"


def _load_cv_text() -> str:
    cv_folder = os.environ.get("CV_FOLDER_PATH", "")
    if not cv_folder:
        return ""
    path = Path(cv_folder) / "Master CVs" / _COMBINED_CV_FILE
    return path.read_text() if path.exists() else ""


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
    added = sum(insert_discovered_job(conn, p) for p in postings)
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


# ── extraction (with French hard filter) ─────────────────────────────────────

def run_extraction(conn, progress: RunProgress) -> None:
    rows = db.jobs_by_status(conn, "discovered")
    if _MAX_JOBS_PER_RUN:
        rows = rows[:_MAX_JOBS_PER_RUN]
    n_input = len(rows)
    n_french = 0
    n_extracted = 0
    n_failed = 0
    progress.info(f"Extraction: {n_input} job(s) queued")
    for row in rows:
        # French mandatory-language hard filter — no AI call, no cost.
        if _is_french_mandatory(row["jd_raw"] or ""):
            db.update_job(conn, row["id"], pipeline_status="skipped_language_requirement")
            progress.job_update(row["id"], row["title"], row["company"],
                                "filtered", ok=False, extra="French mandatory — skipped")
            n_french += 1
            continue
        try:
            extracted = extraction.extract(row["jd_raw"])
        except Exception as exc:  # noqa: BLE001
            progress.error(f"[extraction] job {row['id']} failed: {exc}")
            progress.job_update(row["id"], row["title"], row["company"],
                                "extracted", ok=False, extra=str(exc))
            n_failed += 1
            continue
        db.update_job(conn, row["id"], jd_extracted=extracted, pipeline_status="extracted")
        progress.job_update(row["id"], row["title"], row["company"], "extracted", ok=True)
        n_extracted += 1
    parts = [f"{n_extracted} extracted"]
    if n_french:
        parts.append(f"{n_french} French-filtered")
    if n_failed:
        parts.append(f"{n_failed} error(s)")
    progress.info(f"Extraction done: {n_input} in → {', '.join(parts)}")


# ── scoring ───────────────────────────────────────────────────────────────────

def run_scoring(conn, candidate_profile: str, calibration_notes: str,
                progress: RunProgress) -> None:
    rows = db.jobs_by_status(conn, "extracted")
    if _MAX_JOBS_PER_RUN:
        rows = rows[:_MAX_JOBS_PER_RUN]
    n_input = len(rows)
    n_shortlisted = 0
    n_below = 0
    n_failed = 0
    scores: list[int] = []
    progress.info(f"Scoring: {n_input} job(s) queued")
    for row in rows:
        jd_extracted = json.loads(row["jd_extracted"])
        try:
            result = scoring.score(jd_extracted, candidate_profile, calibration_notes)
        except Exception as exc:  # noqa: BLE001
            progress.error(f"[scoring] job {row['id']} failed: {exc}")
            progress.job_update(row["id"], row["title"], row["company"],
                                "scored", ok=False, extra=str(exc))
            n_failed += 1
            continue
        best_score = result["best_score"]
        scores.append(best_score)
        # score >= threshold → shortlisted for human review in Notion
        # score < threshold  → skipped_low_score, never shown
        new_status = "shortlisted" if best_score >= SCORE_THRESHOLD else "skipped_low_score"
        db.update_job(
            conn, row["id"],
            fit_score=best_score,
            score_reason=result["reasoning"],
            pipeline_status=new_status,
        )
        ok = best_score >= SCORE_THRESHOLD
        if ok:
            n_shortlisted += 1
        else:
            n_below += 1
        stage = "shortlisted" if ok else "skipped"
        extra = f"score={best_score} → {result['best_category']}" if ok \
            else f"score={best_score} (below threshold)"
        progress.job_update(row["id"], row["title"], row["company"], stage, ok=ok, extra=extra)
    progress.info(
        f"Scoring done: {n_input} in → {n_shortlisted} shortlisted, "
        f"{n_below} below threshold" + (f", {n_failed} error(s)" if n_failed else "")
    )
    if scores:
        progress.info(
            f"  Scores: min={min(scores)}, max={max(scores)}, "
            f"avg={sum(scores) / len(scores):.0f}"
        )


# ── tailoring (only for approved jobs) ────────────────────────────────────────

def run_tailoring(conn, cv_folder: Path, output_dir: Path, progress: RunProgress) -> None:
    """Builds a tailored resume for every job the human has approved in Notion.

    Tailoring runs ONLY on jobs in 'approved' status — meaning the human has
    explicitly set Status = Approved in Notion and poll_decisions() has recorded
    that decision. Shortlisted jobs that have not yet been reviewed are never
    touched here.
    """
    rows = db.jobs_by_status(conn, "approved")
    n_input = len(rows)
    n_tailored = 0
    n_failed = 0
    progress.info(f"Tailoring: {n_input} approved job(s) queued")
    for row in rows:
        jd_extracted = json.loads(row["jd_extracted"])
        try:
            tailor_result = tailoring.build_tailored_resume(
                cv_folder=cv_folder,
                company=row["company"],
                role=row["title"],
                jd_extracted=jd_extracted,
                jd_raw=row["jd_raw"],
                output_dir=output_dir,
            )
        except Exception as exc:  # noqa: BLE001
            progress.error(f"[tailoring] job {row['id']} resume build failed: {exc}")
            progress.job_update(row["id"], row["title"], row["company"],
                                "tailored", ok=False, extra=str(exc))
            n_failed += 1
            continue

        cv_text = _load_cv_text()
        try:
            answers_result = answers.draft_answers(cv_text, jd_extracted) if cv_text else {}
        except Exception as exc:  # noqa: BLE001
            progress.error(f"[tailoring] job {row['id']} answers draft failed: {exc}")
            answers_result = {}

        db.update_job(
            conn, row["id"],
            tailored_resume_docx=tailor_result["docx_path"],
            tailored_resume_pdf=tailor_result["pdf_path"],
            tailoring_notes=tailor_result["notes"],
            tailoring_flags=tailor_result["flags"],
            answers=answers_result,
            pipeline_status="tailored",
        )
        progress.job_update(row["id"], row["title"], row["company"], "tailored", ok=True,
                            extra=str(tailor_result["docx_path"]))
        n_tailored += 1
    progress.info(
        f"Tailoring done: {n_input} in → {n_tailored} tailored"
        + (f", {n_failed} error(s)" if n_failed else "")
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


_APPLY_SOURCES = ("Greenhouse", "Ashby", "Lever")


def run_apply(conn, screenshot_dir: Path, progress: RunProgress,
              applicant_notes: str = "") -> None:
    """Fills (and, only with AUTO_SUBMIT_CONFIRMED=true, submits) applications
    for jobs that are both tailored AND approved.

    Safety guarantees:
    - Only acts on jobs with pipeline_status='tailored' AND decision='approved'.
    - Real submission only fires when AUTO_SUBMIT_CONFIRMED=true in .env;
      otherwise fills the form and takes a screenshot for review.
    - Only acts on Greenhouse/Ashby/Lever jobs — LinkedIn/Indeed stay manual.
    - A job must have been approved by the human in Notion AND tailored before
      any application is attempted.
    """
    rows = conn.execute(
        "SELECT * FROM jobs WHERE pipeline_status = 'tailored' AND decision = 'approved' "
        f"AND source IN ({','.join('?' * len(_APPLY_SOURCES))})",
        _APPLY_SOURCES,
    ).fetchall()
    n_input = len(rows)
    n_submitted = 0
    n_dry_run = 0
    n_failed = 0
    progress.info(f"Apply: {n_input} tailored+approved job(s) queued")
    for row in rows:
        jd_extracted = json.loads(row["jd_extracted"]) if row["jd_extracted"] else {}
        cv_text = _load_cv_text()
        try:
            result = apply_module.apply_to_job(
                source=row["source"],
                job_link=row["link"],
                resume_pdf_path=row["tailored_resume_pdf"] or "",
                cv_text=cv_text,
                jd_extracted=jd_extracted,
                output_dir=screenshot_dir,
                company=row["company"],
                role=row["title"],
                applicant_notes=applicant_notes,
            )
        except Exception as exc:  # noqa: BLE001
            progress.error(f"[apply] job {row['id']} failed: {exc}")
            progress.job_update(row["id"], row["title"], row["company"],
                                "applied", ok=False, extra=str(exc))
            n_failed += 1
            continue

        new_status = "applied" if result["submitted"] else "ready_to_apply"
        db.update_job(
            conn, row["id"],
            custom_answers=result["custom_answers"],
            apply_flags=result["flags"],
            application_screenshot=result["screenshot_path"],
            pipeline_status=new_status,
            submitted_at=db.now_iso() if result["submitted"] else None,
        )
        if result["submitted"]:
            notion_sync.mark_applied(row["notion_page_id"], applied_via="Auto")
            progress.job_update(row["id"], row["title"], row["company"], "applied", ok=True)
            n_submitted += 1
        else:
            flag_note = (f"{len(result['flags'])} field(s) need your input"
                         if result["flags"] else "dry-run screenshot saved")
            progress.job_update(row["id"], row["title"], row["company"],
                                "applied", ok=False, extra=flag_note)
            n_dry_run += 1
    progress.info(
        f"Apply done: {n_input} in → {n_submitted} submitted, "
        f"{n_dry_run} dry-run (screenshot only)"
        + (f", {n_failed} error(s)" if n_failed else "")
    )


def run_all(candidate_profile: str, calibration_notes: str = "",
            applicant_notes: str = "",
            progress: Optional[RunProgress] = None) -> None:
    conn = db.connect()
    cv_folder = Path(os.environ["CV_FOLDER_PATH"]) if os.environ.get("CV_FOLDER_PATH") else None
    output_dir = Path(os.environ.get("CV_OUTPUT_DIR", CONFIG_DIR.parent / "data" / "tailored"))
    screenshot_dir = Path(os.environ.get("APPLY_SCREENSHOT_DIR",
                                         CONFIG_DIR.parent / "data" / "screenshots"))

    def _count(status: str) -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE pipeline_status = ?", (status,)
        ).fetchone()[0]

    _p = progress
    try:
        _p.stage_start("discover", pending=_count("discovered"))
        added = run_discovery(conn, _p)
        _p.stage_done("discover", count=added)

        _p.stage_start("filter", pending=_count("discovered"))
        run_relevance_filter(conn, _p)
        run_availability_check(conn, _p)
        _p.stage_done("filter", count=_count("discovered"),
                       note="passed relevance + availability")

        _p.stage_start("extract", pending=_count("discovered"))
        run_extraction(conn, _p)
        _p.stage_done("extract", count=_count("extracted"))

        _p.stage_start("score", pending=_count("extracted"))
        run_scoring(conn, candidate_profile, calibration_notes, _p)
        _p.stage_done("score", count=_count("shortlisted"), note="shortlisted")

        _p.stage_start("tailor", pending=_count("approved"))
        if cv_folder:
            run_tailoring(conn, cv_folder, output_dir, _p)
        else:
            _p.info("CV_FOLDER_PATH not set — skipping tailoring, approved jobs stay at 'approved'")
        _p.stage_done("tailor", count=_count("tailored"))

        _p.stage_start("sync", pending=conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE pipeline_status='shortlisted'"
            " AND notion_page_id IS NULL"
        ).fetchone()[0])
        run_notion_sync(conn, _p)
        _p.stage_done("sync", count=_count("shortlisted"))

        _p.stage_start("apply", pending=conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE pipeline_status='tailored'"
            " AND decision='approved'"
        ).fetchone()[0])
        run_apply(conn, screenshot_dir, _p, applicant_notes)
        _p.stage_done("apply", count=_count("applied"))
    finally:
        conn.close()
