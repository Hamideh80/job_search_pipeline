"""SQLite pipeline database.

Holds the full audit trail for every job the pipeline has seen: raw JD,
extraction, score results, tailoring output, and the Notion page it's
synced to. This is the system of record for the learning loop (phase 6) --
Notion is just the review/approval surface (see the build-plan doc).

Schema evolution
----------------
SCHEMA defines the target schema for fresh installs (CREATE TABLE IF NOT
EXISTS).  For existing databases _apply_migrations() adds any columns or
tables that are missing without touching existing data.  connect() calls
both on every open, so migration is automatic and idempotent.
"""
import hashlib
import json
import shutil
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
    source TEXT NOT NULL,              -- LinkedIn / Greenhouse / Ashby / Lever / Web Search / etc.
    source_job_id TEXT,                -- external job ID (e.g. LinkedIn "4290917572", GH job id)
    discovery_url TEXT,                -- original discovery URL when link is a canonical ATS URL
    jd_raw TEXT,
    jd_extracted TEXT,                 -- JSON: must_haves, nice_to_haves, years_required, seniority, remote, location
    fit_score INTEGER,
    score_reason TEXT,
    cv_category TEXT,                  -- AI Transformation Consultant / Technical Business Analyst / Implementation / FDE
    tailored_resume_docx TEXT,
    tailored_resume_pdf TEXT,
    tailoring_notes TEXT,              -- what was emphasized and why (for your own reference)
    tailoring_flags TEXT,              -- JSON list: JD requirements the CV genuinely can't support
    answers TEXT,                      -- JSON: evergreen application question -> drafted answer
    custom_answers TEXT,               -- JSON: this posting's actual custom questions -> drafted answer
    apply_flags TEXT,                  -- JSON list: fields the auto-fill couldn't confidently answer
    application_screenshot TEXT,       -- path to the filled-form screenshot, for review before real submit
    submitted_at TEXT,                 -- when a REAL (non-dry-run) submission happened
    notion_page_id TEXT,
    pipeline_status TEXT NOT NULL DEFAULT 'discovered',
        -- full state machine in pipeline/state.py
        -- legacy values still valid: synced, ready_to_submit
    decision TEXT,                     -- approved / skipped (read back from Notion Status)
    applied_via TEXT,                  -- Auto / Manual / N/A
    outcome TEXT,                      -- Interview / Rejected / Offer / null
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_jd_hash ON jobs(jd_hash);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(pipeline_status);

-- Partial unique index: enforces one DB row per external job ID, but only
-- when source_job_id is actually set (NULLs are excluded so legacy rows
-- without a source_job_id are never affected).
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_source_job_id
    ON jobs(source_job_id) WHERE source_job_id IS NOT NULL;

-- Gmail intake: processed-message registry.
-- message_id is the primary key and the authoritative dedup key.
CREATE TABLE IF NOT EXISTS gmail_messages (
    message_id   TEXT PRIMARY KEY,
    label_name   TEXT NOT NULL,
    processed_at TEXT NOT NULL
);

-- Gmail intake: per-label checkpoint.
-- after_epoch (Unix seconds) is a performance optimisation for the Gmail
-- API query; message_id registry is the true dedup authority.
CREATE TABLE IF NOT EXISTS gmail_intake_state (
    label_name   TEXT PRIMARY KEY,
    after_epoch  INTEGER NOT NULL,
    last_run_at  TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------

# Columns added after the initial release, in the order they were introduced.
# Each entry is (column_name, sql_type).  The list is append-only; never
# remove or reorder entries -- the order determines which ALTER TABLE
# statements run first.
_MIGRATION_COLUMNS: list[tuple[str, str]] = [
    # Step-2 additions
    ("source_job_id", "TEXT"),
    ("discovery_url",  "TEXT"),
]


def _apply_migrations(conn: sqlite3.Connection,
                      db_path: Optional[Path] = None) -> None:
    """Add missing columns and tables to an existing database.

    Safe to call repeatedly: checks existing schema before each ALTER TABLE
    so no statement runs twice.  If *db_path* is provided and the file exists
    and at least one column needs to be added, a pre-migration snapshot is
    written to the same parent directory exactly once (snapshot is skipped on
    subsequent calls once columns exist).
    """
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    cols_to_add = [(col, typ) for col, typ in _MIGRATION_COLUMNS
                   if col not in existing_cols]

    if cols_to_add and db_path and db_path.exists():
        snap = db_path.parent / f"{db_path.stem}_pre_step2_migration{db_path.suffix}"
        if not snap.exists():
            shutil.copy2(db_path, snap)
            print(f"[db] pre-migration snapshot → {snap.name}")

    for col, typ in cols_to_add:
        conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")

    # CREATE TABLE IF NOT EXISTS for new tables (safe to run every time).
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS gmail_messages (
            message_id   TEXT PRIMARY KEY,
            label_name   TEXT NOT NULL,
            processed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS gmail_intake_state (
            label_name   TEXT PRIMARY KEY,
            after_epoch  INTEGER NOT NULL,
            last_run_at  TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_source_job_id
            ON jobs(source_job_id) WHERE source_job_id IS NOT NULL;
    """)

    if cols_to_add:
        conn.commit()
        print(f"[db] schema migrated: added column(s) {[c for c, _ in cols_to_add]}")


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def connect(path: Optional[str] = None) -> sqlite3.Connection:
    """Open (or create) the pipeline SQLite database and apply any pending
    schema migrations.

    Pass path=':memory:' for an isolated in-memory database (tests only).
    The default is DB_PATH (data/pipeline.db).
    """
    if path == ":memory:":
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        _apply_migrations(conn, None)
        return conn

    p = Path(path) if path is not None else DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _apply_migrations(conn, p)
    return conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_now = now_iso  # internal alias


def hash_jd(company: str, title: str, jd_raw: str) -> str:
    """Dedup key: company + title + JD text."""
    basis = f"{company.strip().lower()}|{title.strip().lower()}|{(jd_raw or '').strip()}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def job_exists(conn: sqlite3.Connection, jd_hash: str) -> bool:
    row = conn.execute("SELECT 1 FROM jobs WHERE jd_hash = ?", (jd_hash,)).fetchone()
    return row is not None


def job_link_exists(conn: sqlite3.Connection, link: str) -> bool:
    row = conn.execute("SELECT 1 FROM jobs WHERE link = ?", (link,)).fetchone()
    return row is not None


def insert_job(conn: sqlite3.Connection, *, company: str, title: str, link: str,
               source: str, jd_raw: str,
               source_job_id: Optional[str] = None,
               discovery_url: Optional[str] = None) -> int:
    jd_hash = hash_jd(company, title, jd_raw)
    now = _now()
    cur = conn.execute(
        """INSERT INTO jobs
               (jd_hash, company, title, link, source, source_job_id,
                discovery_url, jd_raw, pipeline_status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?, ?)""",
        (jd_hash, company, title, link, source, source_job_id,
         discovery_url, jd_raw, now, now),
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
