"""Ties discovery -> dedup -> extraction -> scoring -> Notion sync into one run.

Run via `python main.py`. Safe to run repeatedly: dedup on JD hash means a
re-run only processes what's new, and every stage only picks up jobs still
sitting at the previous pipeline_status.
"""
import json
from pathlib import Path

import yaml

from . import db, extraction, notion_sync, scoring
from .discovery import ashby, gmail_linkedin, greenhouse, lever

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


def run_discovery(conn) -> int:
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
                print(f"[discovery] {ats}/{token} failed: {exc}")
                continue
            for posting in postings:
                new_count += _insert_if_new(conn, posting)

    if watchlist.get("linkedin_gmail_label"):
        new_count += _run_linkedin_intake(conn, watchlist["linkedin_gmail_label"])

    return new_count


def _run_linkedin_intake(conn, label_name: str) -> int:
    try:
        links = gmail_linkedin.fetch_job_links(label_name)
    except Exception as exc:  # noqa: BLE001
        print(f"[discovery] LinkedIn Gmail intake failed: {exc}")
        return 0
    added = 0
    for link in links:
        jd_text = gmail_linkedin.fetch_jd_text(link)
        if not jd_text:
            print(f"[discovery] could not fetch JD text for {link}, skipping")
            continue
        posting = {
            "company": "Unknown (LinkedIn)",
            "title": "Unknown (see JD)",
            "link": link,
            "jd_raw": jd_text,
            "source": "LinkedIn",
        }
        added += _insert_if_new(conn, posting)
    return added


def _insert_if_new(conn, posting: dict) -> int:
    jd_hash = db.hash_jd(posting["company"], posting["title"], posting["jd_raw"])
    if db.job_exists(conn, jd_hash):
        return 0
    db.insert_job(conn, **posting)
    return 1


def run_extraction(conn) -> None:
    for row in db.jobs_by_status(conn, "discovered"):
        try:
            extracted = extraction.extract(row["jd_raw"])
        except Exception as exc:  # noqa: BLE001
            print(f"[extraction] job {row['id']} failed: {exc}")
            continue
        db.update_job(conn, row["id"], jd_extracted=extracted, pipeline_status="extracted")


def run_scoring(conn, candidate_profile: str, calibration_notes: str) -> None:
    for row in db.jobs_by_status(conn, "extracted"):
        jd_extracted = json.loads(row["jd_extracted"])
        try:
            result = scoring.score(jd_extracted, candidate_profile, calibration_notes)
        except Exception as exc:  # noqa: BLE001
            print(f"[scoring] job {row['id']} failed: {exc}")
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
        print(f"[scoring] job {row['id']} ({row['title']!r} @ {row['company']}): "
              f"{best_score} -> {new_status}")


def run_notion_sync(conn) -> None:
    for row in db.jobs_by_status(conn, "scored"):
        page_id = notion_sync.create_job_page(dict(row))
        db.update_job(conn, row["id"], notion_page_id=page_id, pipeline_status="synced")
    notion_sync.poll_decisions(conn)


def run_all(candidate_profile: str, calibration_notes: str = "") -> None:
    conn = db.connect()
    try:
        added = run_discovery(conn)
        print(f"[discovery] {added} new postings")
        run_extraction(conn)
        run_scoring(conn, candidate_profile, calibration_notes)
        run_notion_sync(conn)
    finally:
        conn.close()
