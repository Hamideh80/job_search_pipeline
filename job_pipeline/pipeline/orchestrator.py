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

from . import answers, apply as apply_module, db, extraction, notion_sync, relevance, scoring, tailoring
from .discovery import ashby, gmail_linkedin, greenhouse, lever, linkedin_search
from .progress import RunProgress

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
SCORE_THRESHOLD = 70

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

_MASTER_CV_FILES = {
    "FDE / Solutions":      "Hamideh_Ahooei_Master_FDE_Solutions.md",
    "Agentic AI":           "Hamideh_Ahooei_Master_Agentic_AI.md",
    "Technical Leadership": "Hamideh_Ahooei_Master_Technical_Leadership.md",
}


def _load_category_cv_text(category: str) -> str:
    filename = _MASTER_CV_FILES.get(category)
    if not filename:
        return ""
    cv_folder = os.environ.get("CV_FOLDER_PATH", "")
    if not cv_folder:
        return ""
    path = Path(cv_folder) / "Master CVs" / filename
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
            except Exception as exc:  # noqa: BLE001
                progress.error(f"[discovery] {ats}/{token} failed: {exc}")
                continue
            for posting in postings:
                new_count += insert_discovered_job(conn, posting)

    if watchlist.get("linkedin_gmail_label"):
        new_count += _run_linkedin_intake(conn, watchlist["linkedin_gmail_label"], progress)

    for cfg in watchlist.get("linkedin_searches", []):
        new_count += _run_linkedin_search(conn, cfg, progress)

    progress.info(f"{new_count} new posting(s) found")
    return new_count


def _run_linkedin_intake(conn, label_name: str, progress: RunProgress) -> int:
    """Ingest jobs from LinkedIn alert emails; extracts title + company from JD page."""
    try:
        links = gmail_linkedin.fetch_job_links(label_name)
    except Exception as exc:  # noqa: BLE001
        progress.error(f"[discovery] LinkedIn Gmail intake failed: {exc}")
        return 0
    added = 0
    for link in links:
        if db.job_link_exists(conn, link):
            continue
        job_data = gmail_linkedin.fetch_job_data(link)
        if not job_data["jd_raw"]:
            progress.error(f"[discovery] could not fetch JD for {link}, skipping")
            continue
        posting = {
            "company": job_data["company"] or "Unknown (LinkedIn)",
            "title":   job_data["title"]   or "Unknown (see JD)",
            "link":    link,
            "jd_raw":  job_data["jd_raw"],
            "source":  "LinkedIn",
        }
        added += insert_discovered_job(conn, posting)
    return added


def _run_linkedin_search(conn, cfg: dict, progress: RunProgress) -> int:
    """Run one keyword+location LinkedIn guest search and ingest results."""
    keywords = cfg.get("keywords", "")
    location = cfg.get("location", "Canada")
    max_results = int(cfg.get("max_results", 25))
    if not keywords:
        return 0
    try:
        postings = linkedin_search.search_jobs(keywords, location, max_results)
    except Exception as exc:  # noqa: BLE001
        progress.error(f"[discovery] LinkedIn search '{keywords}' failed: {exc}")
        return 0
    added = sum(insert_discovered_job(conn, p) for p in postings)
    if added:
        progress.info(f"[discovery] LinkedIn search '{keywords}' → {added} new")
    return added


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
    for row in db.jobs_by_status(conn, "discovered"):
        title  = row["title"]  or ""
        jd_raw = row["jd_raw"] or ""
        ok, reason = relevance.is_relevant(title, jd_raw)
        if not ok:
            db.update_job(conn, row["id"],
                          pipeline_status="skipped_irrelevant",
                          relevance_skip_reason=reason)
            progress.job_update(row["id"], title, row["company"],
                                "relevance", ok=False, extra=reason[:80])


# ── extraction (with French hard filter) ─────────────────────────────────────

def run_extraction(conn, progress: RunProgress) -> None:
    for row in db.jobs_by_status(conn, "discovered"):
        # French mandatory-language hard filter — no AI call, no cost.
        if _is_french_mandatory(row["jd_raw"] or ""):
            db.update_job(conn, row["id"], pipeline_status="skipped_language_requirement")
            progress.job_update(row["id"], row["title"], row["company"],
                                "filtered", ok=False, extra="French mandatory — skipped")
            continue
        try:
            extracted = extraction.extract(row["jd_raw"])
        except Exception as exc:  # noqa: BLE001
            progress.error(f"[extraction] job {row['id']} failed: {exc}")
            progress.job_update(row["id"], row["title"], row["company"],
                                "extracted", ok=False, extra=str(exc))
            continue
        db.update_job(conn, row["id"], jd_extracted=extracted, pipeline_status="extracted")
        progress.job_update(row["id"], row["title"], row["company"], "extracted", ok=True)


# ── scoring ───────────────────────────────────────────────────────────────────

def run_scoring(conn, candidate_profile: str, calibration_notes: str,
                progress: RunProgress) -> None:
    for row in db.jobs_by_status(conn, "extracted"):
        jd_extracted = json.loads(row["jd_extracted"])
        try:
            result = scoring.score(jd_extracted, candidate_profile, calibration_notes)
        except Exception as exc:  # noqa: BLE001
            progress.error(f"[scoring] job {row['id']} failed: {exc}")
            progress.job_update(row["id"], row["title"], row["company"],
                                "scored", ok=False, extra=str(exc))
            continue
        best_score = result["best_score"]
        # score >= threshold → shortlisted for human review in Notion
        # score < threshold  → skipped_low_score, never shown
        new_status = "shortlisted" if best_score >= SCORE_THRESHOLD else "skipped_low_score"
        db.update_job(
            conn, row["id"],
            fit_score=best_score,
            cv_category=result["best_category"],
            score_reason=result["reasoning"],
            pipeline_status=new_status,
        )
        ok = best_score >= SCORE_THRESHOLD
        stage = "shortlisted" if ok else "skipped"
        extra = f"score={best_score} → {result['best_category']}" if ok \
            else f"score={best_score} (below threshold)"
        progress.job_update(row["id"], row["title"], row["company"], stage, ok=ok, extra=extra)


# ── tailoring (only for approved jobs) ────────────────────────────────────────

def run_tailoring(conn, cv_folder: Path, output_dir: Path, progress: RunProgress) -> None:
    """Builds a tailored resume for every job the human has approved in Notion.

    Tailoring runs ONLY on jobs in 'approved' status — meaning the human has
    explicitly set Status = Approved in Notion and poll_decisions() has recorded
    that decision. Shortlisted jobs that have not yet been reviewed are never
    touched here.
    """
    for row in db.jobs_by_status(conn, "approved"):
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
            progress.job_update(row["id"], row["title"], row["company"],
                                "tailored", ok=False, extra=str(exc))
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
    rows = conn.execute(
        "SELECT * FROM jobs WHERE pipeline_status = 'shortlisted' AND notion_page_id IS NULL"
    ).fetchall()
    for row in rows:
        try:
            page_id = notion_sync.create_job_page(dict(row))
        except Exception as exc:  # noqa: BLE001
            progress.error(f"[sync] job {row['id']} Notion sync failed: {exc}")
            progress.job_update(row["id"], row["title"], row["company"],
                                "sync", ok=False, extra=str(exc))
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
            progress.job_update(row["id"], row["title"], row["company"],
                                "applied", ok=False, extra=str(exc))
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
        else:
            flag_note = (f"{len(result['flags'])} field(s) need your input"
                         if result["flags"] else "dry-run screenshot saved")
            progress.job_update(row["id"], row["title"], row["company"],
                                "applied", ok=False, extra=flag_note)


def run_all(candidate_profile: str, calibration_notes: str = "",
            applicant_notes: str = "",
            progress: Optional[RunProgress] = None) -> None:
    conn = db.connect()
    cv_folder = Path(os.environ["CV_FOLDER_PATH"]) if os.environ.get("CV_FOLDER_PATH") else None
    output_dir = Path(os.environ.get("CV_OUTPUT_DIR", CONFIG_DIR.parent / "data" / "tailored"))
    screenshot_dir = Path(os.environ.get("APPLY_SCREENSHOT_DIR",
                                         CONFIG_DIR.parent / "data" / "screenshots"))

    _p = progress
    try:
        _p.stage_start("discover")
        added = run_discovery(conn, _p)
        _p.stage_done("discover", count=added)

        _p.stage_start("filter")
        run_relevance_filter(conn, _p)
        _p.stage_done("filter")

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
            _p.info("CV_FOLDER_PATH not set — skipping tailoring, approved jobs stay at 'approved'")
        _p.stage_done("tailor")

        _p.stage_start("sync")
        run_notion_sync(conn, _p)
        _p.stage_done("sync")

        _p.stage_start("apply")
        run_apply(conn, screenshot_dir, _p, applicant_notes)
        _p.stage_done("apply")
    finally:
        conn.close()
