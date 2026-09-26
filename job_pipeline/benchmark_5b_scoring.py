"""Step 5B: Controlled scoring stability benchmark.

Anthropic API unavailable (credit balance zero) -> 3 independent CC runs per job.

For each job:
  - Uses stored jd_extracted (same input every run, no re-extraction variance)
  - Uses current profile from config/cvs/ (same three CVs)
  - Uses current applicant_notes.md (same calibration notes)
  - Calls scoring.score() via ClaudeCodeBackend 3 times, measuring:
      shortlist decision stability (>=70 or <70)
      CV category stability
      score range (max - min)
      gaps penalized consistently?
      reasoning themes stable?

No production state changes.  Read-only SQLite access.
Usage:
    set PYTHONIOENCODING=utf-8
    python benchmark_5b_scoring.py
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pipeline.ai import ClaudeCodeBackend, set_ai_client
from pipeline import scoring as scoring_mod

BENCHMARK_IDS = [2, 6, 3, 4, 8]
RUNS = 3
SHORTLIST_THRESHOLD = 70


# ── data loading ──────────────────────────────────────────────────────────────

def _load_jobs(db_path: str) -> dict[int, dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ph = ",".join("?" * len(BENCHMARK_IDS))
    rows = conn.execute(f"SELECT * FROM jobs WHERE id IN ({ph})", BENCHMARK_IDS).fetchall()
    conn.close()
    return {r["id"]: dict(r) for r in rows}


def _load_profile_and_notes() -> tuple[str, str]:
    base = Path(__file__).parent / "config"
    profile = scoring_mod.load_candidate_profile(base / "cvs")
    notes = scoring_mod.load_calibration_notes(base / "applicant_notes.md")
    return profile, notes


# ── single scoring run ────────────────────────────────────────────────────────

def _run_score(extracted: dict, profile: str, notes: str) -> tuple[dict, float]:
    t0 = time.perf_counter()
    result = scoring_mod.score(extracted, profile, notes)
    return result, round(time.perf_counter() - t0, 1)


# ── stability analysis ────────────────────────────────────────────────────────

def _shortlist(score: int) -> str:
    return ">= 70 SHORTLIST" if score >= SHORTLIST_THRESHOLD else "< 70 skip"


def _reasoning_themes(text: str) -> list[str]:
    """Extract a few key signal words from reasoning for quick comparison."""
    keywords = [
        "years", "engineering", "management", "LLM", "AI", "Android",
        "consulting", "leadership", "technical", "gap", "missing",
        "strong", "weak", "risk", "transferable",
    ]
    text_lower = text.lower()
    return [k for k in keywords if k.lower() in text_lower]


def _analyze_stability(runs: list[dict]) -> dict:
    scores = [r["best_score"] for r in runs]
    cats = [r["best_category"] for r in runs]
    decisions = [_shortlist(s) for s in scores]
    gaps_sets = [set(r.get("gaps", [])) for r in runs]

    # gaps in common across all runs
    if gaps_sets:
        common_gaps = gaps_sets[0].intersection(*gaps_sets[1:])
    else:
        common_gaps = set()

    reasoning_themes = [_reasoning_themes(r.get("reasoning", "")) for r in runs]

    return {
        "scores": scores,
        "score_range": max(scores) - min(scores),
        "categories": cats,
        "cat_stable": len(set(cats)) == 1,
        "decisions": decisions,
        "decision_stable": len(set(decisions)) == 1,
        "common_gaps": sorted(common_gaps),
        "reasoning_themes": reasoning_themes,
    }


# ── table rendering ───────────────────────────────────────────────────────────

def _cell(v, w: int) -> str:
    s = str(v) if v is not None else ""
    return s[:w].ljust(w)


def _print_table(headers: list[str], widths: list[int], rows: list[list]) -> None:
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    def line(cells):
        return "|" + "|".join(f" {_cell(c, w)} " for c, w in zip(cells, widths)) + "|"
    print(sep)
    print(line(headers))
    print(sep)
    for r in rows:
        print(line(r))
    print(sep)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    db_path = Path(__file__).parent / "data" / "pipeline.db"
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    jobs = _load_jobs(str(db_path))
    profile, notes = _load_profile_and_notes()
    print(f"Profile: {len(profile)} chars | Notes: {len(notes)} chars")

    # Inject CC backend for all scoring calls
    backend = ClaudeCodeBackend()
    set_ai_client(backend)

    print(f"\nStep 5B: Scoring stability — {RUNS} CC runs per job, same inputs each time")
    print(f"Shortlist threshold: >= {SHORTLIST_THRESHOLD}\n")

    all_results = {}

    for jid in BENCHMARK_IDS:
        job = jobs.get(jid)
        if not job:
            print(f"Job {jid}: NOT FOUND in DB\n")
            continue

        label = f"{job['company']} / {job['title']}"
        stored_score = job.get("fit_score", "?")
        stored_cat = job.get("cv_category", "?")

        # Use stored jd_extracted — no re-extraction variance
        extracted_raw = job.get("jd_extracted") or "{}"
        try:
            extracted = json.loads(extracted_raw)
        except json.JSONDecodeError:
            print(f"  Job {jid}: cannot parse stored jd_extracted — skip\n")
            continue

        print(f"Job {jid}: {label}")
        print(f"  Stored: score={stored_score}  cat={stored_cat}")

        run_results = []
        run_times = []
        errors = []

        for i in range(1, RUNS + 1):
            print(f"  Run {i}/{RUNS}...", end="", flush=True)
            try:
                result, elapsed = _run_score(extracted, profile, notes)
                run_results.append(result)
                run_times.append(elapsed)
                sc = result.get("best_score", "?")
                cat = result.get("best_category", "?")
                print(f" score={sc}  cat={cat}  ({elapsed}s)")
            except Exception as exc:
                errors.append(str(exc)[:100])
                print(f" ERROR: {str(exc)[:80]}")

        if len(run_results) < 2:
            print(f"  Too few successful runs ({len(run_results)}) — skip analysis\n")
            continue

        stability = _analyze_stability(run_results)
        all_results[jid] = {
            "label": label,
            "stored_score": stored_score,
            "stored_cat": stored_cat,
            "runs": run_results,
            "times": run_times,
            "stability": stability,
            "errors": errors,
        }

        print(f"  Scores: {stability['scores']}  range={stability['score_range']}")
        print(f"  Category stable: {stability['cat_stable']} {stability['categories']}")
        print(f"  Decision stable: {stability['decision_stable']} {stability['decisions']}")
        print(f"  Common gaps: {stability['common_gaps'][:3]}")
        print()

    set_ai_client(None)

    # ── Summary table ─────────────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("STABILITY SUMMARY TABLE")
    print("=" * 100)

    headers = ["ID", "Job", "Stored", "R1", "R2", "R3", "Range", "Cat stable", "Decision stable", "Common gaps"]
    widths  = [ 3,    32,    10,       8,    8,    8,    5,       10,            15,                 35]
    rows = []

    for jid in BENCHMARK_IDS:
        r = all_results.get(jid)
        if not r:
            rows.append([jid] + ["?"] * (len(headers) - 1))
            continue
        s = r["stability"]
        stored = f"{r['stored_score']}/{r['stored_cat'][:6]}"
        r1 = f"{s['scores'][0]}/{r['runs'][0]['best_category'][:6]}"
        r2 = f"{s['scores'][1]}/{r['runs'][1]['best_category'][:6]}" if len(s['scores']) > 1 else "?"
        r3 = f"{s['scores'][2]}/{r['runs'][2]['best_category'][:6]}" if len(s['scores']) > 2 else "?"
        gaps_str = "; ".join(r["stability"]["common_gaps"][:2]) if r["stability"]["common_gaps"] else "none consistent"
        rows.append([
            jid,
            r["label"][:32],
            stored,
            r1, r2, r3,
            s["score_range"],
            "YES" if s["cat_stable"] else "NO",
            "YES" if s["decision_stable"] else "NO",
            gaps_str[:35],
        ])

    _print_table(headers, widths, rows)

    # ── Metric breakdown ──────────────────────────────────────────────────────
    print("\nMETRIC ANALYSIS")
    print("-" * 70)

    decision_stable_count = sum(
        1 for r in all_results.values() if r["stability"]["decision_stable"]
    )
    cat_stable_count = sum(
        1 for r in all_results.values() if r["stability"]["cat_stable"]
    )
    avg_range = (
        sum(r["stability"]["score_range"] for r in all_results.values()) / len(all_results)
        if all_results else 0
    )

    n = len(all_results)
    print(f"1. Shortlist decision (>=70 / <70) stable:  {decision_stable_count}/{n} jobs")
    print(f"2. CV category selection stable:            {cat_stable_count}/{n} jobs")
    print(f"3. Average score range across 3 runs:       {avg_range:.1f} points")

    print("\nPer-job reasoning themes (what signals CC consistently picked up):")
    for jid, r in all_results.items():
        themes = r["stability"]["reasoning_themes"]
        common = set(themes[0]).intersection(*themes[1:]) if len(themes) > 1 else set(themes[0]) if themes else set()
        print(f"  Job {jid} ({r['label'][:30]}): consistent themes = {sorted(common)}")

    # ── Recommendation ────────────────────────────────────────────────────────
    print("\nRECOMMENDATION")
    print("-" * 70)

    if decision_stable_count >= 4 and cat_stable_count >= 4 and avg_range <= 15:
        rec = "A"
        verdict = "ClaudeCodeBackend is sufficiently stable for scoring."
        detail = (
            f"Decision stable {decision_stable_count}/{n}, "
            f"category stable {cat_stable_count}/{n}, "
            f"avg range {avg_range:.1f} pts -- acceptable for a human-gated pipeline."
        )
    elif avg_range > 20 or decision_stable_count <= 2:
        rec = "B"
        verdict = "Scoring prompt/rubric needs stabilization before either backend is trusted."
        detail = (
            f"Decision stable only {decision_stable_count}/{n}, avg range {avg_range:.1f} pts. "
            "High variance is in the prompt design, not the backend. "
            "Add explicit scoring anchors (0=no overlap, 50=some match, 70=strong, 90+=exceptional) "
            "and require the model to cite specific CV lines before committing to a score."
        )
    else:
        rec = "B (lean toward A once prompt is tightened)"
        verdict = "Scoring is borderline stable; prompt calibration would help."
        detail = (
            f"Decision stable {decision_stable_count}/{n}, "
            f"category stable {cat_stable_count}/{n}, "
            f"avg range {avg_range:.1f} pts. "
            "The prompt lacks numeric anchors and relies on holistic judgment, "
            "which explains run-to-run drift. Tightening the rubric would increase "
            "stability regardless of which backend is used."
        )

    print(f"\n  Recommendation: {rec}")
    print(f"  Verdict: {verdict}")
    print(f"  Detail: {detail}")
    print()


if __name__ == "__main__":
    main()
