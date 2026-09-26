"""Temporary validation runner -- DO NOT COMMIT.

Processes exactly 15 specified job IDs through:
  discovered -> French mandatory filter -> extraction -> scoring
  -> shortlisted (score >= 70) or skipped_low_score (score < 70)

Does NOT: sync to Notion, tailor, run applications, touch any other job.
Safe to re-run: jobs not at 'discovered' are skipped, not reprocessed.
"""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# Force Claude Code backend (OAuth auth, no API credits required).
# The pipeline's ClaudeCodeBackend calls `claude -p` via subprocess.
os.environ.setdefault("AI_BACKEND", "claude_code")
os.environ.setdefault("CLAUDE_CODE_MODEL", "claude-sonnet-4-6")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from pipeline import db, extraction, scoring
from pipeline.orchestrator import _is_french_mandatory, SCORE_THRESHOLD

# ── The only 15 jobs this script will ever touch ─────────────────────────────
TARGET_IDS = [446, 443, 442, 441, 440, 438, 437, 436, 435, 434,
              432, 431, 430, 429, 428]

CV_FOLDER = os.environ.get("CV_FOLDER_PATH", "")
MASTER_CV_DIR = Path(CV_FOLDER) / "Master CVs" if CV_FOLDER else ROOT / "config" / "cvs"
CAL_NOTES_PATH = ROOT / "config" / "calibration_notes.md"


def _truncate(s, n=90):
    s = (s or "").replace("\n", " ").strip()
    return s[:n] + "…" if len(s) > n else s


def main():
    print("=" * 70)
    print("VALIDATION BATCH RUNNER")
    print(f"  Target IDs : {TARGET_IDS}")
    print(f"  Master CVs : {MASTER_CV_DIR}")
    print(f"  Score threshold : {SCORE_THRESHOLD}")
    print("=" * 70)
    print()

    conn = db.connect()
    profile = scoring.load_candidate_profile(MASTER_CV_DIR)
    cal_notes = scoring.load_calibration_notes(CAL_NOTES_PATH)

    if "[missing:" in profile:
        print("[WARN] One or more Master CV files not found — check MASTER_CV_DIR")
        print(profile[:300])
        print()

    results = []
    ai_calls = 0

    for job_id in TARGET_IDS:
        row = db.get_job(conn, job_id)
        if row is None:
            print(f"[{job_id}] NOT FOUND in DB — skipping")
            continue
        if row["pipeline_status"] != "discovered":
            print(f"[{job_id}] Already at '{row['pipeline_status']}' — skipping")
            continue

        company = row["company"]
        title   = row["title"]
        jd_raw  = row["jd_raw"] or ""

        rec = dict(id=job_id, company=company, title=title,
                   french="pass", category=None, score=None,
                   status=None, reason=None, error=None)

        # ── French filter ─────────────────────────────────────────────────
        if _is_french_mandatory(jd_raw):
            db.update_job(conn, job_id, pipeline_status="skipped_language_requirement")
            rec.update(french="REJECT", status="skipped_language_requirement",
                       reason="French mandatory")
            print(f"[{job_id}] FRENCH FILTERED  {company} / {title}")
            results.append(rec)
            continue

        # ── Extraction ────────────────────────────────────────────────────
        try:
            extracted = extraction.extract(jd_raw)
            db.update_job(conn, job_id, jd_extracted=extracted,
                          pipeline_status="extracted")
            ai_calls += 1
        except Exception as exc:
            rec.update(status="extraction_error", error=str(exc)[:120])
            print(f"[{job_id}] EXTRACTION FAILED  {exc}")
            results.append(rec)
            continue

        # ── Scoring ───────────────────────────────────────────────────────
        try:
            result = scoring.score(extracted, profile, cal_notes)
            ai_calls += 1
            best_score    = result["best_score"]
            best_category = result["best_category"]
            reasoning     = result.get("reasoning", "")

            new_status = "shortlisted" if best_score >= SCORE_THRESHOLD else "skipped_low_score"
            db.update_job(conn, job_id,
                          fit_score=best_score,
                          cv_category=best_category,
                          score_reason=reasoning,
                          pipeline_status=new_status)

            rec.update(category=best_category, score=best_score,
                       status=new_status, reason=reasoning)
            flag = "SHORTLISTED" if best_score >= SCORE_THRESHOLD else "LOW SCORE "
            print(f"[{job_id}] {flag}  score={best_score:3d}  {best_category:<22}  "
                  f"{company} / {title}")
        except Exception as exc:
            rec.update(status="scoring_error", error=str(exc)[:120])
            # Leave at extracted so a real run can retry
            print(f"[{job_id}] SCORING FAILED  {exc}")

        results.append(rec)

    conn.close()

    # ── Results table ─────────────────────────────────────────────────────
    print()
    print("=" * 140)
    print("RESULTS")
    print("=" * 140)
    hdr = (f"{'ID':>4}  {'Company':<12} {'Title':<44} {'French':<6} "
           f"{'Master':<22} {'Score':>5}  {'Status':<22}  Reason")
    print(hdr)
    print("-" * 140)
    for r in results:
        french  = r["french"] or "pass"
        cat     = r["category"] or "—"
        score   = str(r["score"]) if r["score"] is not None else "—"
        status  = r["status"] or "—"
        reason  = _truncate(r.get("reason") or r.get("error") or "", 60)
        print(f"{r['id']:>4}  {r['company'][:12]:<12} {r['title'][:44]:<44} {french:<6} "
              f"{cat:<22} {score:>5}  {status:<22}  {reason}")

    # ── Summary ───────────────────────────────────────────────────────────
    processed       = len(results)
    french_rejected = sum(1 for r in results if r["french"] == "REJECT")
    shortlisted     = sum(1 for r in results if r["status"] == "shortlisted")
    low_score       = sum(1 for r in results if r["status"] == "skipped_low_score")
    errors          = sum(1 for r in results if "error" in r["status"] if r["status"])

    from collections import Counter
    cat_dist = Counter(r["category"] for r in results if r["category"])

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Processed          : {processed}")
    print(f"  French rejected    : {french_rejected}")
    print(f"  Shortlisted (>=70) : {shortlisted}")
    print(f"  Low score (<70)    : {low_score}")
    print(f"  Errors             : {errors}")
    print(f"  AI calls (approx)  : {ai_calls}  (extraction + scoring, 2 per job)")
    print()
    print("  Category distribution:")
    for cat, n in cat_dist.most_common():
        print(f"    {cat:<25} {n}")
    print()
    print("  Notion sync: NOT performed")
    print("  Tailoring:   NOT performed")
    print("  Applications:NOT performed")
    print("  AUTO_SUBMIT_CONFIRMED remains: false")
    print("=" * 70)


if __name__ == "__main__":
    main()
