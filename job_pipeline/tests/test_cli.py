"""Tests for the Step-3 CLI entry point (main.py).

Focus areas:
1. `status` is read-only — does not modify any DB row.
2. `status` works without ANTHROPIC_API_KEY.
3. Invalid / missing subcommand returns a non-zero exit code.
4. `apply-approved` SQL gate never selects unapproved jobs.
5. `apply-approved` SQL gate DOES select genuinely approved jobs.
"""
import sys
import subprocess
import types
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ── helpers ───────────────────────────────────────────────────────────────────

def _db(tmp_path):
    """Return an open connection to a fresh temp-file database."""
    import pipeline.db as db_mod
    return db_mod.connect(str(tmp_path / "test.db"))


def _insert(conn, *, company="Acme", title="Eng",
            link="https://x.com", source="Test", jd_raw="some JD"):
    import pipeline.db as db_mod
    return db_mod.insert_job(conn, company=company, title=title,
                              link=link, source=source, jd_raw=jd_raw)


# ── 1. status is read-only ────────────────────────────────────────────────────

def test_status_readonly(tmp_path):
    """cmd_status must not modify any job row."""
    import pipeline.db as db_mod

    db_path = str(tmp_path / "test.db")
    conn = db_mod.connect(db_path)
    job_id = _insert(conn)
    before = dict(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    conn.close()

    # Save the real connect before patching so the side_effect doesn't recurse.
    real_connect = db_mod.connect
    from main import cmd_status
    with patch("pipeline.db.connect",
               side_effect=lambda *_a, **_kw: real_connect(db_path)):
        cmd_status(types.SimpleNamespace())

    # Reopen and compare — nothing should have changed.
    conn2 = real_connect(db_path)
    after = dict(conn2.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    conn2.close()

    assert before == after, "cmd_status modified a job row"


# ── 2. status works without ANTHROPIC_API_KEY ─────────────────────────────────

def test_status_no_api_key(tmp_path, monkeypatch):
    """cmd_status must complete successfully even when ANTHROPIC_API_KEY is absent.

    If cmd_status imported scoring.py, answers.py, or any other module whose
    module-level code calls os.environ["ANTHROPIC_API_KEY"], it would raise
    a KeyError here.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    import pipeline.db as db_mod
    db_path = str(tmp_path / "test.db")

    real_connect = db_mod.connect
    from main import cmd_status
    with patch("pipeline.db.connect",
               side_effect=lambda *_a, **_kw: real_connect(db_path)):
        # Must not raise — no Anthropic client should be instantiated.
        cmd_status(types.SimpleNamespace())


# ── 3. invalid / missing subcommand ──────────────────────────────────────────

def test_invalid_command_exits_nonzero():
    """An unrecognised subcommand must exit non-zero with a useful message."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "nonexistent-command"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    # argparse must mention "usage", "error", or "invalid" somewhere.
    assert any(kw in combined.lower() for kw in ("usage", "error", "invalid", "argument")), (
        f"Expected a helpful error message, got:\n{combined}"
    )


def test_no_subcommand_exits_nonzero():
    """Running with no subcommand must exit non-zero."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "main.py")],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode != 0


# ── 4 & 5. apply-approved SQL gate ───────────────────────────────────────────
#
# We test the exact SQL used by orchestrator.run_apply() against an in-memory
# DB to avoid importing orchestrator (which has module-level Anthropic clients).
# This verifies the gate logic is sound without needing an API key.

_APPLY_SOURCES = ("Greenhouse", "Ashby", "Lever")
_APPLY_SQL = (
    "SELECT * FROM jobs "
    "WHERE pipeline_status = 'synced' AND decision = 'approved' "
    f"AND source IN ({','.join('?' * len(_APPLY_SOURCES))})"
)


def test_apply_gate_excludes_unapproved():
    """apply-approved must not select a synced job with no decision."""
    import pipeline.db as db_mod
    conn = db_mod.connect(":memory:")

    _insert(conn, source="Greenhouse", link="https://a.com")
    conn.execute(
        "UPDATE jobs SET pipeline_status='synced', decision=NULL WHERE company='Acme'"
    )
    conn.commit()

    rows = conn.execute(_APPLY_SQL, _APPLY_SOURCES).fetchall()
    assert len(rows) == 0, "Unapproved job was selected by the apply gate"


def test_apply_gate_excludes_skipped():
    """apply-approved must not select a job the human marked 'skipped'."""
    import pipeline.db as db_mod
    conn = db_mod.connect(":memory:")

    _insert(conn, source="Greenhouse", link="https://a.com")
    conn.execute(
        "UPDATE jobs SET pipeline_status='synced', decision='skipped' WHERE company='Acme'"
    )
    conn.commit()

    rows = conn.execute(_APPLY_SQL, _APPLY_SOURCES).fetchall()
    assert len(rows) == 0, "Skipped job was selected by the apply gate"


def test_apply_gate_selects_approved():
    """apply-approved DOES select a synced job with decision='approved'."""
    import pipeline.db as db_mod
    conn = db_mod.connect(":memory:")

    _insert(conn, source="Greenhouse", link="https://a.com")
    conn.execute(
        "UPDATE jobs SET pipeline_status='synced', decision='approved' WHERE company='Acme'"
    )
    conn.commit()

    rows = conn.execute(_APPLY_SQL, _APPLY_SOURCES).fetchall()
    assert len(rows) == 1, "Approved Greenhouse job was not selected"


def test_apply_gate_excludes_non_ats_source():
    """apply-approved must not select approved LinkedIn jobs (manual-apply sources)."""
    import pipeline.db as db_mod
    conn = db_mod.connect(":memory:")

    _insert(conn, source="LinkedIn", link="https://a.com")
    conn.execute(
        "UPDATE jobs SET pipeline_status='synced', decision='approved' WHERE company='Acme'"
    )
    conn.commit()

    rows = conn.execute(_APPLY_SQL, _APPLY_SOURCES).fetchall()
    assert len(rows) == 0, "LinkedIn job was selected — it should stay manual"
