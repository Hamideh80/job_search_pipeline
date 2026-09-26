"""Broad discovery trial — DO NOT COMMIT.

Runs the LinkedIn keyword searches configured in companies.yaml,
processes ONLY the newly discovered jobs (not the existing backlog),
and prints a results table.

Does NOT tailor, apply, or sync to Notion.
AUTO_SUBMIT_CONFIRMED is irrelevant — this script never touches apply.
"""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from pipeline import db, extraction, relevance, scoring
from pipeline.orchestrator import (
    _is_french_mandatory, insert_discovered_job, load_watchlist,
    SCORE_THRESHOLD,
)
from pipeline.discovery import linkedin_search

CONFIG_DIR = ROOT / "config"


def _trunc(s, n=40):
    s = (s or "").replace("\n", " ").strip()
    return s[:n] + "…" if len(s) > n else s


def main():
    conn = db.connect()

    # ── snapshot existing discovered IDs so we only process NEW ones ───────────
    existing_ids = {
        r["id"] for r in conn.execute(
            "SELECT id FROM jobs WHERE pipeline_status = 'discovered'"
        ).fetchall()
    }

    # ── run LinkedIn searches ──────────────────────────────────────────────────
    watchlist = load_watchlist()
    searches = watchlist.get("linkedin_searches", [])

    print(f"\n{'='*100}")
    print(f"BROAD DISCOVERY TRIAL — {len(searches)} configured search queries")
    print(f"{'='*100}\n")

    raw_found = 0
    dedup_skipped = 0

    # Record the max existing ID before discovery so we can find new rows by ID range
    max_id_before = conn.execute("SELECT COALESCE(MAX(id), 0) FROM jobs").fetchone()[0]

    for cfg in searches:
        keywords = cfg.get("keywords", "")
        location = cfg.get("location", "Canada")
        max_results = int(cfg.get("max_results", 25))
        print(f"  Searching: '{keywords}' | location='{location}' | max={max_results}")
        try:
            postings = linkedin_search.search_jobs(keywords, location, max_results)
        except Exception as exc:
            print(f"    ERROR: {exc}")
            continue
        raw_found += len(postings)
        for p in postings:
            jd_hash = db.hash_jd(p["company"], p["title"], p["jd_raw"])
            if db.job_exists(conn, jd_hash):
                dedup_skipped += 1
            else:
                insert_discovered_job(conn, p)

    # Query actual new IDs by comparing against pre-discovery snapshot
    newly_inserted_ids = [
        r["id"] for r in conn.execute(
            "SELECT id FROM jobs WHERE id > ? AND pipeline_status = 'discovered'",
            (max_id_before,)
        ).fetchall()
    ]

    print(f"\n  Raw postings fetched : {raw_found}")
    print(f"  Dedup-skipped        : {dedup_skipped}")
    print(f"  Newly inserted       : {len(newly_inserted_ids)}")

    if not newly_inserted_ids:
        print("\n  No new jobs to process.\n")
        conn.close()
        return

    # ── relevance filter ───────────────────────────────────────────────────────
    placeholders = ",".join("?" * len(newly_inserted_ids))
    new_rows = conn.execute(
        f"SELECT * FROM jobs WHERE id IN ({placeholders})",
        newly_inserted_ids,
    ).fetchall() if newly_inserted_ids else []

    relevance_pass = []
    relevance_skip = 0
    french_skip = 0

    for row in new_rows:
        ok, reason = relevance.is_relevant(row["title"] or "", row["jd_raw"] or "")
        if not ok:
            db.update_job(conn, row["id"],
                          pipeline_status="skipped_irrelevant",
                          relevance_skip_reason=reason)
            relevance_skip += 1
            continue
        if _is_french_mandatory(row["jd_raw"] or ""):
            db.update_job(conn, row["id"], pipeline_status="skipped_language_requirement")
            french_skip += 1
            continue
        relevance_pass.append(row)

    print(f"  Relevance-filtered   : {relevance_skip}")
    print(f"  French-filtered      : {french_skip}")
    print(f"  Proceeding to AI     : {len(relevance_pass)}\n")

    if not relevance_pass:
        print("  No jobs survived relevance + French filters.\n")
        conn.close()
        return

    # ── extraction + scoring ───────────────────────────────────────────────────
    profile = scoring.load_candidate_profile(
        Path(os.environ.get("CV_FOLDER_PATH", "")) / "Master CVs"
        if os.environ.get("CV_FOLDER_PATH") else CONFIG_DIR / "cvs"
    )
    notes = scoring.load_calibration_notes(CONFIG_DIR / "calibration_notes.md")

    results = []
    ai_failures = 0

    for row in relevance_pass:
        try:
            extracted = extraction.extract(row["jd_raw"])
            db.update_job(conn, row["id"], jd_extracted=extracted, pipeline_status="extracted")
        except Exception as exc:
            print(f"  [extraction] ID {row['id']} failed: {exc}")
            ai_failures += 1
            continue

        try:
            score_result = scoring.score(
                json.loads(extracted), profile, notes
            )
        except Exception as exc:
            print(f"  [scoring] ID {row['id']} failed: {exc}")
            ai_failures += 1
            continue

        best_score = score_result["best_score"]
        new_status = "shortlisted" if best_score >= SCORE_THRESHOLD else "skipped_low_score"
        db.update_job(
            conn, row["id"],
            fit_score=best_score,
            cv_category=score_result["best_category"],
            score_reason=score_result["reasoning"],
            pipeline_status=new_status,
        )

        jd_ex = json.loads(extracted)
        results.append({
            "id":       row["id"],
            "company":  row["company"],
            "title":    row["title"],
            "location": jd_ex.get("location", "?"),
            "source":   row["source"],
            "category": score_result["best_category"],
            "score":    best_score,
            "status":   new_status,
            "reason":   score_result.get("reasoning", "")[:100],
        })

    # ── results table ──────────────────────────────────────────────────────────
    print(f"\n{'='*120}")
    print("RESULTS")
    print(f"{'='*120}")
    hdr = (f"{'ID':>5}  {'Company':<18}  {'Title':<38}  {'Loc':<12}  "
           f"{'Master CV':<22}  {'Score':>5}  {'Status':<20}  Short Reason")
    print(hdr)
    print("-" * 120)
    for r in sorted(results, key=lambda x: -x["score"]):
        status_flag = "★" if r["score"] >= SCORE_THRESHOLD else " "
        print(
            f"{r['id']:>5}  {_trunc(r['company'], 18):<18}  {_trunc(r['title'], 38):<38}  "
            f"{_trunc(r['location'], 12):<12}  {_trunc(r['category'], 22):<22}  "
            f"{r['score']:>5}  {status_flag}{r['status']:<19}  {_trunc(r['reason'], 80)}"
        )

    shortlisted = [r for r in results if r["score"] >= SCORE_THRESHOLD]

    print(f"\n{'='*120}")
    print("PIPELINE SUMMARY")
    print(f"{'='*120}")
    print(f"  Raw search results found  : {raw_found}")
    print(f"  Duplicates removed        : {dedup_skipped}")
    print(f"  New jobs inserted         : {len(newly_inserted_ids)}")
    print(f"  Relevance-filtered (skip) : {relevance_skip}")
    print(f"  French-filtered (skip)    : {french_skip}")
    print(f"  Sent to AI scoring        : {len(relevance_pass)}")
    print(f"  AI failures               : {ai_failures}")
    print(f"  AI-scored                 : {len(results)}")
    print(f"  Score ≥ {SCORE_THRESHOLD} (shortlisted)   : {len(shortlisted)}")
    print()

    conn.close()


if __name__ == "__main__":
    main()
