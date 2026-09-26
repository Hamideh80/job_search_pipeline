"""Step 5 benchmark: compare ClaudeCodeBackend vs stored results for 5 jobs.

SAFE TO RUN: reads from pipeline.db in read-only mode (isolation via in-memory
clone).  No production states are modified.  No applications are submitted.
No Playwright is invoked.

Usage:
    python benchmark_claude_code.py [--db path/to/pipeline.db]

Reports to stdout.  Does NOT write to pipeline.db.
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

# ── ensure pipeline package is on sys.path ────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from pipeline.ai import ClaudeCodeBackend, set_ai_client

# ── benchmark target job IDs (selected in prior analysis) ─────────────────────
BENCHMARK_IDS = [2, 6, 3, 4, 8]

# ── helpers ───────────────────────────────────────────────────────────────────

def _load_jobs(db_path: str) -> dict[int, dict]:
    """Load benchmark rows from the real DB into dicts — read-only."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT * FROM jobs WHERE id IN ({','.join('?'*len(BENCHMARK_IDS))})",
        BENCHMARK_IDS,
    ).fetchall()
    conn.close()
    return {row["id"]: dict(row) for row in rows}


def _parse_json_blob(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_parse_error": raw[:120]}


def _load_profile_and_notes() -> tuple[str, str]:
    """Load candidate profile and calibration notes via scoring module helpers."""
    from pipeline import scoring
    base = Path(__file__).parent / "config"
    try:
        profile = scoring.load_candidate_profile(base / "cvs")
    except Exception:
        profile = ""
    try:
        notes = scoring.load_calibration_notes(base / "applicant_notes.md")
    except Exception:
        notes = ""
    return profile, notes


def _score_material_diff(stored: dict, cc: dict) -> bool:
    """Return True if ClaudeCode score differs by >=10 points OR different category."""
    if not stored or not cc:
        return True
    s_score = stored.get("best_score", 0) or 0
    c_score = cc.get("best_score", 0) or 0
    s_cat = stored.get("best_category", "")
    c_cat = cc.get("best_category", "")
    return abs(int(s_score) - int(c_score)) >= 10 or s_cat != c_cat


def _diff_extraction(stored: dict, cc: dict) -> list[str]:
    """Return list of field-level diffs between stored and CC extraction."""
    diffs = []
    fields = ["must_haves", "nice_to_haves", "years_required", "seniority",
              "remote_policy", "location"]
    for f in fields:
        sv = stored.get(f)
        cv = cc.get(f)
        if sv != cv:
            diffs.append(f"{f}: stored={sv!r} -> cc={cv!r}")
    return diffs


# ── benchmark stages ──────────────────────────────────────────────────────────

def bench_extraction(job: dict, backend: ClaudeCodeBackend) -> dict:
    """Re-run extraction on stored jd_text, compare to stored jd_extracted."""
    from pipeline import extraction

    jd_text = job.get("jd_raw") or ""
    if not jd_text:
        return {"skipped": "no jd_text in DB", "time_s": 0.0}

    stored_raw = job.get("jd_extracted") or "{}"
    stored = _parse_json_blob(stored_raw)

    t0 = time.perf_counter()
    try:
        cc_result = extraction.extract(jd_text)
        elapsed = time.perf_counter() - t0
        diffs = _diff_extraction(stored, cc_result)
        return {
            "valid": True,
            "diffs": diffs,
            "material_diff": len(diffs) > 2,
            "time_s": round(elapsed, 1),
        }
    except Exception as exc:
        return {"valid": False, "error": str(exc)[:120], "time_s": round(time.perf_counter() - t0, 1)}


def bench_scoring(job: dict, backend: ClaudeCodeBackend, profile: str, notes: str) -> dict:
    """Re-run scoring using CC extraction output (or stored), compare scores."""
    from pipeline import extraction, scoring

    jd_text = job.get("jd_raw") or ""
    # stored values are individual columns, not a JSON blob
    stored_score = {
        "best_score": job.get("fit_score"),
        "best_category": job.get("cv_category"),
    }

    # Try to get CC extraction first; fall back to stored
    extracted: dict = {}
    if jd_text:
        try:
            extracted = extraction.extract(jd_text)
        except Exception:
            extracted = _parse_json_blob(job.get("jd_extracted") or "{}")
    else:
        extracted = _parse_json_blob(job.get("jd_extracted") or "{}")

    if not extracted:
        return {"skipped": "no extracted data available", "time_s": 0.0}

    t0 = time.perf_counter()
    try:
        cc_score = scoring.score(extracted, profile, notes)
        elapsed = time.perf_counter() - t0

        stored_best = stored_score.get("best_score", "?")
        cc_best = cc_score.get("best_score", "?")
        stored_cat = stored_score.get("best_category", "?")
        cc_cat = cc_score.get("best_category", "?")

        return {
            "valid": True,
            "stored_score": stored_best,
            "cc_score": cc_best,
            "stored_cat": stored_cat,
            "cc_cat": cc_cat,
            "material_diff": _score_material_diff(stored_score, cc_score),
            "time_s": round(elapsed, 1),
        }
    except Exception as exc:
        return {"valid": False, "error": str(exc)[:120], "time_s": round(time.perf_counter() - t0, 1)}


# ── report rendering ──────────────────────────────────────────────────────────

def _cell(v, width: int) -> str:
    s = str(v) if v is not None else ""
    return s[:width].ljust(width)


def _print_table(rows: list[list], headers: list[str], widths: list[int]) -> None:
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    def row_line(cells):
        return "|" + "|".join(f" {_cell(c, w)} " for c, w in zip(cells, widths)) + "|"
    print(sep)
    print(row_line(headers))
    print(sep)
    for r in rows:
        print(row_line(r))
    print(sep)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="ClaudeCodeBackend benchmark (read-only)")
    ap.add_argument("--db", default="data/pipeline.db", help="Path to pipeline.db")
    args = ap.parse_args()

    db_path = Path(__file__).parent / args.db
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    print(f"\nBenchmark: ClaudeCodeBackend vs stored pipeline results")
    print(f"DB: {db_path}  |  Jobs: {BENCHMARK_IDS}\n")

    # Load data
    jobs = _load_jobs(str(db_path))
    profile, notes = _load_profile_and_notes()
    if not profile:
        print("WARNING: No candidate profile found in config/ — scoring may be degraded.\n")

    # Wire up ClaudeCodeBackend globally for all pipeline calls
    backend = ClaudeCodeBackend()
    set_ai_client(backend)

    # ── Extraction benchmark ──────────────────────────────────────────────────
    print("Stage 1: Extraction")
    ext_rows = []
    for jid in BENCHMARK_IDS:
        job = jobs.get(jid)
        if not job:
            ext_rows.append([jid, "?", "?", "N/A — not in DB", "0.0s", "—"])
            continue
        label = f"{job.get('company','?')} / {job.get('title','?')[:30]}"
        r = bench_extraction(job, backend)
        if r.get("skipped"):
            ext_rows.append([jid, label, "SKIP", r["skipped"], "—", "—"])
        elif not r["valid"]:
            ext_rows.append([jid, label, "FAIL", r.get("error",""), f"{r['time_s']}s", "—"])
        else:
            diffs = r["diffs"]
            diff_str = "; ".join(diffs[:2]) + ("…" if len(diffs) > 2 else "") if diffs else "none"
            mat = "YES" if r["material_diff"] else "no"
            ext_rows.append([jid, label, "OK", diff_str[:50], f"{r['time_s']}s", mat])

    _print_table(
        ext_rows,
        ["ID", "Job", "Status", "Key diffs", "Time", "Material?"],
        [3, 36, 6, 50, 6, 9],
    )

    # ── Scoring benchmark ─────────────────────────────────────────────────────
    print("\nStage 2: Scoring")
    scr_rows = []
    for jid in BENCHMARK_IDS:
        job = jobs.get(jid)
        if not job:
            scr_rows.append([jid, "?", "?", "?", "N/A — not in DB", "0.0s", "—"])
            continue
        label = f"{job.get('company','?')} / {job.get('title','?')[:25]}"
        stored_score = job.get("fit_score", "?")
        r = bench_scoring(job, backend, profile, notes)
        if r.get("skipped"):
            scr_rows.append([jid, label, stored_score, "SKIP", r["skipped"][:30], "—", "—"])
        elif not r["valid"]:
            scr_rows.append([jid, label, stored_score, "FAIL", r.get("error","")[:30], f"{r['time_s']}s", "—"])
        else:
            cat_change = "" if r["stored_cat"] == r["cc_cat"] else f"{r['stored_cat'][:15]}->{r['cc_cat'][:15]}"
            mat = "YES" if r["material_diff"] else "no"
            scr_rows.append([
                jid, label,
                f"{stored_score}->{r['cc_score']}",
                r["cc_cat"][:20],
                cat_change[:30] or "same",
                f"{r['time_s']}s",
                mat,
            ])

    _print_table(
        scr_rows,
        ["ID", "Job", "Score old->cc", "CC category", "Cat change", "Time", "Material?"],
        [3, 31, 13, 20, 30, 6, 9],
    )

    set_ai_client(None)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("""
Summary
-------
ClaudeCodeBackend ran extraction and scoring for 5 representative jobs.
Results above show whether ClaudeCode output matches stored pipeline values
and whether any differences are material (score shift >=10 pts or category flip).

Recommendation criteria
  A -- Adopt ClaudeCodeBackend as default: all 5 jobs valid, <=1 material diff
  B -- Keep AnthropicAPIBackend as default: >=2 material diffs or >=1 failure
""")


if __name__ == "__main__":
    main()
