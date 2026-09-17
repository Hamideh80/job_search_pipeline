"""Local hand-off point for broad web-search discovery.

    python seed_discoveries.py candidates.json

This is the ONLY connection between "search the web for jobs" and the rest
of the pipeline. It's meant to be run by a Claude scheduled task (with
"requires this computer" turned on) that does the actual web search itself
using its own WebSearch/WebFetch tools, then writes what it found to
candidates.json and runs this script -- immediately followed by
`python main.py` in the same run, so discovery, scoring, tailoring, and
Notion sync all happen in one continuous execution. See the README section
on the scheduled-task architecture for the full picture and an example
task prompt.

candidates.json is a JSON array, one object per posting found:
    [{"company": "...", "title": "...", "link": "...", "jd_raw": "...",
      "source": "Web Search"}, ...]

jd_raw must be the actual job description text (not just a search
snippet) -- extraction.py and scoring.py need the real text to do their
job. "source" is free text; it only changes behavior if it happens to be
exactly "Greenhouse", "Ashby", or "Lever" (that makes the job eligible for
phase 5 auto-apply once scored, tailored, and approved) -- anything else,
including the default "Web Search", stays manual-apply like LinkedIn/Indeed.

Dedup is automatic: a posting whose company+title+JD text hash already
exists in the pipeline DB is silently skipped, so re-running this with an
overlapping candidate list (e.g. the same company search turning up again
next run) is safe.
"""
import json
import sys

from dotenv import load_dotenv

load_dotenv()

from pipeline import db, orchestrator  # noqa: E402  (import after load_dotenv)

REQUIRED_FIELDS = {"company", "title", "link", "jd_raw"}


def seed(path: str) -> None:
    candidates = json.loads(open(path).read())
    if not isinstance(candidates, list):
        raise ValueError("candidates.json must be a JSON array of job objects")

    conn = db.connect()
    added = 0
    try:
        for candidate in candidates:
            missing = REQUIRED_FIELDS - candidate.keys()
            if missing:
                print(f"[seed] skipping candidate missing {missing}: {candidate.get('title', '?')}")
                continue
            posting = {
                "company": candidate["company"],
                "title": candidate["title"],
                "link": candidate["link"],
                "jd_raw": candidate["jd_raw"],
                "source": candidate.get("source") or "Web Search",
            }
            added += orchestrator.insert_discovered_job(conn, posting)
    finally:
        conn.close()
    print(f"[seed] added {added} new job(s) out of {len(candidates)} candidate(s)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python seed_discoveries.py candidates.json")
        sys.exit(1)
    seed(sys.argv[1])
