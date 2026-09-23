"""Tests for pipeline/db.py — schema creation and migration."""
import sqlite3

import pytest

from pipeline.db import (
    SCHEMA,
    _apply_migrations,
    connect,
    insert_job,
    job_exists,
    hash_jd,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _indexes(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }


# ── fresh DB ──────────────────────────────────────────────────────────────────

def test_fresh_db_has_jobs_table():
    conn = connect(":memory:")
    assert "jobs" in _tables(conn)


def test_fresh_db_has_new_columns():
    conn = connect(":memory:")
    cols = _columns(conn)
    assert "source_job_id" in cols
    assert "discovery_url" in cols


def test_fresh_db_has_gmail_tables():
    conn = connect(":memory:")
    tables = _tables(conn)
    assert "gmail_messages" in tables
    assert "gmail_intake_state" in tables


def test_fresh_db_has_source_job_id_index():
    conn = connect(":memory:")
    assert "idx_jobs_source_job_id" in _indexes(conn)


# ── migration on old DB ───────────────────────────────────────────────────────

def _old_schema_conn() -> sqlite3.Connection:
    """Simulate a pre-Step-2 database: jobs table without new columns."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            jd_hash TEXT UNIQUE NOT NULL,
            company TEXT NOT NULL,
            title TEXT NOT NULL,
            link TEXT,
            source TEXT NOT NULL,
            jd_raw TEXT,
            fit_score INTEGER,
            pipeline_status TEXT NOT NULL DEFAULT 'discovered',
            notion_page_id TEXT,
            decision TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
    return conn


def test_migration_adds_columns_to_old_db():
    conn = _old_schema_conn()
    before = _columns(conn)
    assert "source_job_id" not in before
    assert "discovery_url" not in before

    _apply_migrations(conn, None)

    after = _columns(conn)
    assert "source_job_id" in after
    assert "discovery_url" in after


def test_migration_idempotent():
    """Running migration twice must not raise or duplicate columns."""
    conn = _old_schema_conn()
    _apply_migrations(conn, None)
    _apply_migrations(conn, None)   # second call must be a no-op

    cols = _columns(conn)
    # No duplicate column names (set vs list comparison would silently hide
    # duplicates, so use the raw PRAGMA count instead).
    raw = [row[1] for row in conn.execute("PRAGMA table_info(jobs)")]
    assert len(raw) == len(set(raw)), "Duplicate columns detected after double migration"


def test_migration_preserves_existing_rows():
    conn = _old_schema_conn()
    import json
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO jobs (jd_hash, company, title, link, source, jd_raw, "
        "pipeline_status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("hash1", "Acme", "Engineer", "https://example.com",
         "LinkedIn", "some jd", "discovered", now, now),
    )
    conn.commit()

    _apply_migrations(conn, None)

    rows = conn.execute("SELECT * FROM jobs").fetchall()
    assert len(rows) == 1
    assert rows[0]["company"] == "Acme"
    assert rows[0]["source_job_id"] is None   # new column, NULL for old rows


# ── deduplication ─────────────────────────────────────────────────────────────

def test_source_job_id_unique_constraint():
    conn = connect(":memory:")
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        "INSERT INTO jobs (jd_hash, company, title, link, source, source_job_id, "
        "pipeline_status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'discovered', ?, ?)",
        ("h1", "Acme", "Eng A", "https://a.com", "LinkedIn", "job42", now, now),
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO jobs (jd_hash, company, title, link, source, source_job_id, "
            "pipeline_status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'discovered', ?, ?)",
            ("h2", "Other", "Eng B", "https://b.com", "LinkedIn", "job42", now, now),
        )
        conn.commit()


def test_source_job_id_null_not_constrained():
    """Multiple rows with NULL source_job_id must be allowed."""
    conn = connect(":memory:")
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    for i in range(3):
        conn.execute(
            "INSERT INTO jobs (jd_hash, company, title, link, source, "
            "pipeline_status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'discovered', ?, ?)",
            (f"h{i}", "Acme", f"Eng {i}", f"https://x{i}.com", "Manual", now, now),
        )
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM jobs WHERE source_job_id IS NULL").fetchone()[0]
    assert count == 3


# ── gmail_messages uniqueness ─────────────────────────────────────────────────

def test_gmail_message_id_unique():
    conn = connect(":memory:")
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        "INSERT INTO gmail_messages (message_id, label_name, processed_at) VALUES (?, ?, ?)",
        ("msg001", "LinkedIn/Jobs", now),
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO gmail_messages (message_id, label_name, processed_at) VALUES (?, ?, ?)",
            ("msg001", "LinkedIn/Jobs", now),
        )
        conn.commit()


# ── insert_job helper ─────────────────────────────────────────────────────────

def test_insert_job_returns_id():
    conn = connect(":memory:")
    job_id = insert_job(
        conn,
        company="Acme",
        title="SWE",
        link="https://acme.com/jobs/1",
        source="Test",
        jd_raw="Build stuff.",
    )
    assert isinstance(job_id, int)
    assert job_id > 0


def test_insert_job_with_source_job_id():
    conn = connect(":memory:")
    job_id = insert_job(
        conn,
        company="Acme",
        title="SWE",
        link="https://acme.com/jobs/1",
        source="LinkedIn",
        jd_raw="Build stuff.",
        source_job_id="abc123",
        discovery_url="https://linkedin.com/jobs/view/abc123",
    )
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["source_job_id"] == "abc123"
    assert row["discovery_url"] == "https://linkedin.com/jobs/view/abc123"


def test_job_exists_by_hash():
    conn = connect(":memory:")
    insert_job(
        conn,
        company="Acme",
        title="SWE",
        link="https://acme.com/jobs/1",
        source="Test",
        jd_raw="Build stuff.",
    )
    h = hash_jd("Acme", "SWE", "Build stuff.")
    assert job_exists(conn, h) is True
    assert job_exists(conn, "nonexistent") is False
