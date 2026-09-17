"""SQLite pipeline database.

Holds the full audit trail for every job the pipeline has seen: raw JD,
extraction, score results, tailoring output, and the Notion page it's
synced to. This is the system of record for the learning loop (phase 6) --
Notion is just the review/approval surface (see the build-plan doc).
"""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "pipeline.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    jd_hash TEXT UNIQUE NOT NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    link TEXT,
    source TEXT NOT NULL,              -- LinkedIn / Indeed / Greenhouse / Ashby / Lever / Company site
    jd_raw TEXT,
    jd_extracted TEXT,                 -- JSON: must_haves, nice_to_haves, years_required, seniority, remote, location
    fit_score INTEGER,
    score_reason TEXT,
    cv_category TEXT,                  -- AI Transformation Consultant / Technical Business Analyst / Implementation / FDE
    tailored_resume_path TEXT,
    answers TEXT,                      -- JSON: application question -> drafted answer (phase 4)
    notion_page_id TEXT,
    pipeline_status TEXT NOT NULL DEFAULT 'discovered',
        -- discovered -> extracted -> scored | skipped_low_score -> synced -> applied
    decision TEXT,                     -- approved / skipped (read back from Notion Status)
    applied_via TEXT,                  -- Auto / Manual / N/A
    outcome TEXT,                      -- Interview / Rejected / Offer / null
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_jd_hash ON jobs(jd_hash);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(pipeline_status);
"""


def hash_jd(company: str, title: str, jd_raw: str) -> str:
    """Dedup key: company + title + JD text."""
    basis = f"{company.strip().lower()}|{title.strip().lower()}|{(jd_raw or '').strip()}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def job_exists(conn: sqlite3.Connection, jd_hash: str) -> bool:
    row = conn.execute("SELECT 1 FROM jobs WHERE jd_hash = ?", (jd_hash,)).fetchone()
    return row is not None


def insert_job(conn: sqlite3.Connection, *, company: str, title: str, link: str,
               source: str, jd_raw: str) -> int:
    jd_hash = hash_jd(company, title, jd_raw)
    now = _now()
    cur = conn.execute(
        """INSERT INTO jobs (jd_hash, company, title, link, source, jd_raw,
                              pipeline_status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'discovered', ?, ?)""",
        (jd_hash, company, title, link, source, jd_raw, now, now),
    )
    conn.commit()
    return cur.lastrowid


def update_job(conn: sqlite3.Connection, job_id: int, **fields: Any) -> None:
    """Update arbitrary columns on a job row. JSON-encodes dict/list values."""
    if not fields:
        return
    cols, vals = [], []
    for k, v in fields.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v)
        cols.append(f"{k} = ?")
        vals.append(v)
    cols.append("updated_at = ?")
    vals.append(_now())
    vals.append(job_id)
    conn.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE id = ?", vals)
    conn.commit()


def get_job(conn: sqlite3.Connection, job_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def jobs_by_status(conn: sqlite3.Connection, status: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM jobs WHERE pipeline_status = ?", (status,)
    ).fetchall()


def jobs_with_decisions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every job with a recorded approve/skip decision -- input to the
    learning loop (phase 6)."""
    return conn.execute(
        "SELECT * FROM jobs WHERE decision IS NOT NULL ORDER BY updated_at DESC"
    ).fetchall()
