"""Ties discovery -> dedup -> extraction -> scoring -> Notion sync into one run.

Run via `python main.py`. Safe to run repeatedly: dedup on JD hash means a
re-run only processes what's new, and every stage only picks up jobs still
sitting at the previous pipeline_status.

The pipeline DB (db.py, SQLite) is the only source of truth for processing
state -- Notion is a one-way dashboard (see notion_sync.py's docstring),
never read from for discovery. Broad discovery beyond companies.yaml and
LinkedIn Gmail intake is expected to come from seed_discoveries.py (a
separate local entry point a Claude scheduled task's web search hands
results to) rather than from anything in this module -- see its docstring
and the README section on the scheduled-task architecture.
"""
import json
import os
from pathlib import Path
from typing import Optional

import yaml

from . import answers, apply as apply_module, db, extraction, notion_sync, scoring, tailoring
from .discovery import ashby, gmail_linkedin, greenhouse, lever
from .progress import RunProgress

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
SCORE_THRESHOLD = 70


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
    new_count = 0

    fetchers = {
        "greenhouse": greenhouse.fetch_postings,
        "ashby": ashby.fetch_postings,
        "lever": lever.fetch_postings,
    }
    for ats, fetch_fn in fetchers.items():
        for token in watchlist.get(ats, []):
            try:
                postings = fetch_fn(token)
            except Exception as exc:  # noqa: BLE001 -- one bad company shouldn't kill the run
                progress.error(f"[discovery] {ats}/{token} failed: {exc}")
                continue
            for posting in postings:
                new_count += insert_discovered_job(conn, posting)

    if watchlist.get("linkedin_gmail_label"):
        new_count += _run_linkedin_intake(conn, watchlist["linkedin_gmail_label"], progress)

    progress.info(f"{new_count} new posting(s) found")
    return new_count


def _run_linkedin_intake(conn, label_name: str, progress: RunProgress) -> int:
    try:
        links = gmail_linkedin.fetch_job_links(label_name)
    except Exception as exc:  # noqa: BLE001
        progress.error(f"[discovery] LinkedIn Gmail intake failed: {exc}")
        return 0
    added = 0
    for link in links:
        if db.job_link_exists(conn, link):
            continue
        jd_text = gmail_linkedin.fetch_jd_text(link)
        if not jd_text:
            progress.error(f"[discovery] could not fetch JD text for {link}, skipping")
            continue
        posting = {
            "company": "Unknown (LinkedIn)",
            "title": "Unknown (see JD)",
            "link": link,
            "jd_raw": jd_text,
            "source": "LinkedIn",
        }
        added += insert_discovered_job(conn, posting)
    return added


def insert_discovered_job(conn, posting: dict) -> int:
    """Dedup-checked insert used by every discovery source (companies.yaml,
    LinkedIn Gmail intake, and seed_discoveries.py for web-search results
    handed off by a scheduled task). Returns 1 if it was new, 0 if this
    exact company+title+JD text is already tracked."""
    jd_hash = db.hash_jd(posting["company"], posting["title"], posting["jd_raw"])
    if db.job_exists(conn, jd_hash):
        return 0
    db.insert_job(conn, **posting)
    return 1


def run_extraction(conn, progress: RunProgress) -> None:
    for row in db.jobs_by_status(conn, "discovered"):
        try:
            extracted = extraction.extract(row["jd_raw"])
        except Exception as exc:  # noqa: BLE001
            progress.error(f"[extraction] job {row['id']} failed: {exc}")
            progress.job_update(row["id"], row["title"], row["company"], "extracted", ok=False, extra=str(exc))
            continue
        db.update_job(conn, row["id"], jd_extracted=extracted, pipeline_status="extracted")
        progress.job_update(row["id"], row["title"], row["company"], "extracted", ok=True)


def run_scoring(conn, candidate_profile: str, calibration_notes: str,
                progress: RunProgress) -> None:
    for row in db.jobs_by_status(conn, "extracted"):
        jd_extracted = json.loads(row["jd_extracted"])
        try:
            result = scoring.score(jd_extracted, candidate_profile, calibration_notes)
        except Exception as exc:  # noqa: BLE001
            progress.error(f"[scoring] job {row['id']} failed: {exc}")
            progress.job_update(row["id"], row["title"], row["company"], "scored", ok=False, extra=str(exc))
            continue
        best_score = result["best_score"]
        new_status = "scored" if best_score >= SCORE_THRESHOLD else "skipped_low_score"
        db.update_job(
            conn, row["id"],
            fit_score=best_score,
            cv_category=result["best_category"],
            score_reason=result["reasoning"],
            pipeline_status=new_status,
        )
        ok = best_score >= SCORE_THRESHOLD
        extra = f"score = {best_score}" if ok else f"score = {best_score} (below threshold)"
        progress.job_update(row["id"], row["title"], row["company"], "scored", ok=ok, extra=extra)


_CATEGORY_PROFILE_FILES = {
    "AI Transformation Consultant": "ai_transformation_consultant.md",
    "Technical Business Analyst": "technical_business_analyst.md",
    "Implementation / FDE": "implementation_fde.md",
}


def _load_category_cv_text(category: str) -> str:
    filename = _CATEGORY_PROFILE_FILES.get(category)
    if not filename:
        return ""
    path = CONFIG_DIR / "cvs" / filename
    return path.read_text() if path.exists() else ""


def run_tailoring(conn, cv_folder: Path, output_dir: Path, progress: RunProgress) -> None:
    """Builds a tailored resume + drafted evergreen answers for every scored
    job. Needs python-docx and (for the PDF) LibreOffice available wherever
    this runs -- see README. If either step fails for a job, it's left at
    'scored' so a later run retries it rather than silently dropping it."""
    for row in db.jobs_by_status(conn, "scored"):
        jd_extracted = json.loads(row["jd_extracted"])
        try:
            tailor_result = tailoring.build_tailored_resume(
                cv_folder=cv_folder,
                category=row["cv_category"],
                company=row["company"],
                role=row["title"],
                jd_extracted=jd_extracted,
                jd_raw=row["jd_raw"],
                output_dir=output_dir,
            )
        except Exception as exc:  # noqa: BLE001
            progress.error(f"[tailoring] job {row['id']} resume build failed: {exc}")
            progress.job_update(row["id"], row["title"], row["company"], "tailored", ok=False, extra=str(exc))
            continue

        cv_text = _load_category_cv_text(row["cv_category"])
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


def run_notion_sync(conn, progress: RunProgress) -> None:
    """Creates a Notion dashboard row for every tailored job, then polls for
    Status changes (see notion_sync.py -- this is one-way except that one
    field). Every job's Notion page is always brand-new here: with
    notion_intake removed, the pipeline is the only thing that ever creates
    a Job Tracker 2026 row, so there's nothing to update-in-place."""
    synced_ids: list[int] = []
    for row in db.jobs_by_status(conn, "tailored"):
        try:
            page_id = notion_sync.create_job_page(dict(row))
        except Exception as exc:  # noqa: BLE001
            progress.error(f"[sync] job {row['id']} Notion sync failed: {exc}")
            progress.job_update(row["id"], row["title"], row["company"], "sync", ok=False, extra=str(exc))
            continue
        db.update_job(conn, row["id"], notion_page_id=page_id, pipeline_status="synced")
        progress.job_update(row["id"], row["title"], row["company"], "sync", ok=True)
        synced_ids.append(row["id"])

    notion_sync.poll_decisions(conn)

    # Show approval status for jobs synced this run (read back after polling)
    if synced_ids:
        placeholders = ",".join("?" * len(synced_ids))
        rows = conn.execute(
            f"SELECT id, title, company, decision FROM jobs WHERE id IN ({placeholders})",
            synced_ids,
        ).fetchall()
        for r in rows:
            approved = r["decision"] == "approved"
            progress.job_update(r["id"], r["title"], r["company"], "approved", ok=approved)


_APPLY_SOURCES = ("Greenhouse", "Ashby", "Lever")


def run_apply(conn, screenshot_dir: Path, progress: RunProgress,
              applicant_notes: str = "") -> None:
    """Fills (and, only with AUTO_SUBMIT_CONFIRMED=true, submits) applications
    for jobs you've explicitly set to "Approved" in Notion. See the safety
    notes at the top of pipeline/apply.py before enabling real submission.
    Only acts on Greenhouse/Ashby/Lever jobs -- LinkedIn/Indeed stay manual."""
    rows = conn.execute(
        "SELECT * FROM jobs WHERE pipeline_status = 'synced' AND decision = 'approved' "
        f"AND source IN ({','.join('?' * len(_APPLY_SOURCES))})",
        _APPLY_SOURCES,
    ).fetchall()
    for row in rows:
        jd_extracted = json.loads(row["jd_extracted"]) if row["jd_extracted"] else {}
        cv_text = _load_category_cv_text(row["cv_category"])
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
            progress.job_update(row["id"], row["title"], row["company"], "applied", ok=False, extra=str(exc))
            continue

        new_status = "applied" if result["submitted"] else "ready_to_submit"
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
        else:
            flag_note = (f"{len(result['flags'])} field(s) need your input"
                         if result["flags"] else "dry-run screenshot saved")
            progress.job_update(row["id"], row["title"], row["company"], "applied",
                                ok=False, extra=flag_note)


def run_all(candidate_profile: str, calibration_notes: str = "",
            applicant_notes: str = "",
            progress: Optional[RunProgress] = None) -> None:
    conn = db.connect()
    cv_folder = Path(os.environ["CV_FOLDER_PATH"]) if os.environ.get("CV_FOLDER_PATH") else None
    output_dir = Path(os.environ.get("CV_OUTPUT_DIR", CONFIG_DIR.parent / "data" / "tailored"))
    screenshot_dir = Path(os.environ.get("APPLY_SCREENSHOT_DIR",
                                         CONFIG_DIR.parent / "data" / "screenshots"))

    _p = progress  # caller owns the context-manager lifecycle
    try:
        _p.stage_start("discover")
        added = run_discovery(conn, _p)
        _p.stage_done("discover", count=added)

        _p.stage_start("extract")
        run_extraction(conn, _p)
        _p.stage_done("extract")

        _p.stage_start("score")
        run_scoring(conn, candidate_profile, calibration_notes, _p)
        _p.stage_done("score")

        _p.stage_start("tailor")
        if cv_folder:
            run_tailoring(conn, cv_folder, output_dir, _p)
        else:
            _p.info("CV_FOLDER_PATH not set — skipping tailoring, jobs stay at 'scored'")
        _p.stage_done("tailor")

        _p.stage_start("sync")
        run_notion_sync(conn, _p)
        _p.stage_done("sync")

        _p.stage_start("apply")
        run_apply(conn, screenshot_dir, _p, applicant_notes)
        _p.stage_done("apply")
    finally:
        conn.close()
