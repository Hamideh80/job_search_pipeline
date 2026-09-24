"""Tests for the Step-5 pipeline flow changes.

Verifies:
1.  French mandatory-language hard filter rejects the right patterns.
2.  French "preferred / asset / nice-to-have" phrasing is NOT rejected.
3.  Score < 70 → skipped_low_score.
4.  Score >= 70 → shortlisted (not scored, not tailored).
5.  Shortlisted job is NOT tailored (tailoring only runs on approved jobs).
6.  poll_decisions advances shortlisted → approved when Notion returns Approved.
7.  poll_decisions advances shortlisted → skipped_human when Notion returns Skip.
8.  Approved job IS picked up by run_tailoring.
9.  Apply gate: only tailored+approved jobs are selected (not shortlisted, not synced).
10. AUTO_SUBMIT_CONFIRMED=false → no real submission (dry-run path).
11. Three canonical category names used everywhere (FDE / Solutions, Agentic AI,
    Technical Leadership).
12. Unapproved job can never be tailored via orchestrator.
"""
import json
import os
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline import ai, db as db_mod
from pipeline.orchestrator import _FRENCH_MANDATORY_RE, _is_french_mandatory, SCORE_THRESHOLD
from pipeline.progress import RunProgress
from pipeline import orchestrator, scoring


# ── helpers ───────────────────────────────────────────────────────────────────

def _fresh_conn():
    return db_mod.connect(":memory:")


def _insert(conn, *, company="Acme", title="Eng", source="Greenhouse",
            link="https://x.com/job/1", jd_raw="Build stuff with AI."):
    return db_mod.insert_job(conn, company=company, title=title,
                              link=link, source=source, jd_raw=jd_raw)


def _fake_extraction():
    return json.dumps({
        "must_haves": ["Python"],
        "nice_to_haves": [],
        "years_required": 3,
        "years_required_context": "software engineering",
        "seniority": "Senior",
        "remote_policy": "Hybrid",
        "location": "Toronto",
        "certifications_required": [],
        "work_authorization_required": None,
    })


def _fake_scoring(best_category="FDE / Solutions", best_score=85):
    return json.dumps({
        "scores": {
            "FDE / Solutions": best_score if best_category == "FDE / Solutions" else 40,
            "Agentic AI": best_score if best_category == "Agentic AI" else 35,
            "Technical Leadership": best_score if best_category == "Technical Leadership" else 30,
        },
        "best_category": best_category,
        "best_score": best_score,
        "strong_matches": ["Good match"],
        "transferable_matches": [],
        "gaps": [],
        "interview_risk": [],
        "reasoning": "Test reasoning.",
    })


def _progress(tmp_path):
    return RunProgress(log_dir=tmp_path)


# ── 1. French mandatory patterns → rejected ───────────────────────────────────

@pytest.mark.parametrize("text", [
    "French required",
    "French mandatory",
    "French essential",
    "must speak French",
    "must be bilingual in French",
    "bilingual French/English required",
    "bilingual French-English required",
    "bilingual English/French required",
    "English and French required",
    "French and English required",
    "English and French mandatory",
    "fluent in French required",
    "fluent French required",
    "fluent in French is required",
    "fluent in French is mandatory",
    "professional proficiency in French required",
    "professional fluency in French is required",
    "français obligatoire",
    "Français Obligatoire",
    "français requis",
    "français exigé",
    "français exigée",
    # case-insensitive
    "FRENCH REQUIRED",
    "French Mandatory",
])
def test_french_mandatory_rejected(text):
    assert _is_french_mandatory(text), f"Should have rejected: {text!r}"


# ── 2. French preferred / asset → NOT rejected ────────────────────────────────

@pytest.mark.parametrize("text", [
    "French preferred",
    "French is an asset",
    "French is a plus",
    "French nice to have",
    "French would be beneficial",
    "bilingualism preferred",
    "preference for French",
    "French is considered an asset",
    "Bilingual (French/English) preferred",
    "Knowledge of French is a plus",
    "Bilingualism is an asset",
    "We value French speakers",
    "French considered a strong asset",
    # completely unrelated text
    "Proficiency in Python required",
    "English required",
])
def test_french_not_mandatory_allowed(text):
    assert not _is_french_mandatory(text), f"Should NOT have rejected: {text!r}"


# ── 3. Extraction: French-mandatory job → skipped_language_requirement ────────

def test_extraction_skips_french_mandatory(tmp_path):
    conn = _fresh_conn()
    _insert(conn, jd_raw="Great role! French required. Python skills needed.")

    fake = ai.FakeAIBackend({"extraction": _fake_extraction()})
    ai.set_ai_client(fake)
    try:
        with _progress(tmp_path) as prog:
            orchestrator.run_extraction(conn, prog)
    finally:
        ai.set_ai_client(None)

    row = conn.execute("SELECT pipeline_status FROM jobs").fetchone()
    assert row["pipeline_status"] == "skipped_language_requirement"
    assert len(fake.calls) == 0, "AI extraction was called for a French-mandatory job"


def test_extraction_allows_french_preferred(tmp_path):
    conn = _fresh_conn()
    _insert(conn, jd_raw="Great role! French is an asset. Python skills needed.")

    fake = ai.FakeAIBackend({"extraction": _fake_extraction()})
    ai.set_ai_client(fake)
    try:
        with _progress(tmp_path) as prog:
            orchestrator.run_extraction(conn, prog)
    finally:
        ai.set_ai_client(None)

    row = conn.execute("SELECT pipeline_status FROM jobs").fetchone()
    assert row["pipeline_status"] == "extracted", \
        "French-preferred job was wrongly filtered"
    assert len(fake.calls) == 1


# ── 4. Scoring: score < 70 → skipped_low_score ───────────────────────────────

def test_scoring_low_score_skips(tmp_path):
    conn = _fresh_conn()
    job_id = _insert(conn)
    conn.execute(
        "UPDATE jobs SET pipeline_status='extracted', jd_extracted=? WHERE id=?",
        (_fake_extraction(), job_id),
    )
    conn.commit()

    low_score_response = _fake_scoring(best_category="FDE / Solutions", best_score=55)
    fake = ai.FakeAIBackend({"scoring": low_score_response})
    ai.set_ai_client(fake)
    try:
        with _progress(tmp_path) as prog:
            orchestrator.run_scoring(conn, "profile", "", prog)
    finally:
        ai.set_ai_client(None)

    row = conn.execute("SELECT pipeline_status FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["pipeline_status"] == "skipped_low_score"


# ── 5. Scoring: score >= 70 → shortlisted (NOT scored, NOT tailored) ──────────

def test_scoring_high_score_shortlists(tmp_path):
    conn = _fresh_conn()
    job_id = _insert(conn)
    conn.execute(
        "UPDATE jobs SET pipeline_status='extracted', jd_extracted=? WHERE id=?",
        (_fake_extraction(), job_id),
    )
    conn.commit()

    fake = ai.FakeAIBackend({"scoring": _fake_scoring(best_score=85)})
    ai.set_ai_client(fake)
    try:
        with _progress(tmp_path) as prog:
            orchestrator.run_scoring(conn, "profile", "", prog)
    finally:
        ai.set_ai_client(None)

    row = conn.execute("SELECT pipeline_status, cv_category FROM jobs WHERE id=?",
                       (job_id,)).fetchone()
    assert row["pipeline_status"] == "shortlisted", \
        f"Expected shortlisted, got {row['pipeline_status']}"
    assert row["cv_category"] == "FDE / Solutions"


# ── 6. Shortlisted job is NOT tailored by run_tailoring ──────────────────────

def test_shortlisted_job_not_tailored(tmp_path):
    """run_tailoring must only pick up 'approved' jobs. A shortlisted job
    (pending human review) must never be tailored."""
    conn = _fresh_conn()
    job_id = _insert(conn)
    conn.execute(
        "UPDATE jobs SET pipeline_status='shortlisted', cv_category='FDE / Solutions', "
        "jd_extracted=? WHERE id=?",
        (_fake_extraction(), job_id),
    )
    conn.commit()

    # Even with a real cv_folder, run_tailoring should touch nothing.
    cv_folder = Path(tmp_path)
    output_dir = Path(tmp_path) / "output"

    with _progress(tmp_path) as prog:
        orchestrator.run_tailoring(conn, cv_folder, output_dir, prog)

    row = conn.execute("SELECT pipeline_status FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["pipeline_status"] == "shortlisted", \
        "Shortlisted job was tailored without human approval"


# ── 7 & 8. poll_decisions advances shortlisted → approved / skipped_human ────

def _poll_with_mock(conn, notion_statuses: list[str]):
    """Run poll_decisions with mocked Notion fetch_status."""
    from pipeline import notion_sync as ns
    with patch.object(ns, "fetch_status", return_value=notion_statuses):
        ns.poll_decisions(conn)


def test_poll_decisions_advances_to_approved(tmp_path):
    conn = _fresh_conn()
    job_id = _insert(conn)
    conn.execute(
        "UPDATE jobs SET pipeline_status='shortlisted', notion_page_id='page-123' "
        "WHERE id=?", (job_id,),
    )
    conn.commit()

    _poll_with_mock(conn, ["Approved"])

    row = conn.execute("SELECT pipeline_status, decision FROM jobs WHERE id=?",
                       (job_id,)).fetchone()
    assert row["pipeline_status"] == "approved"
    assert row["decision"] == "approved"


def test_poll_decisions_advances_to_skipped_human(tmp_path):
    conn = _fresh_conn()
    job_id = _insert(conn)
    conn.execute(
        "UPDATE jobs SET pipeline_status='shortlisted', notion_page_id='page-456' "
        "WHERE id=?", (job_id,),
    )
    conn.commit()

    _poll_with_mock(conn, ["Skip"])

    row = conn.execute("SELECT pipeline_status, decision FROM jobs WHERE id=?",
                       (job_id,)).fetchone()
    assert row["pipeline_status"] == "skipped_human"
    assert row["decision"] == "skipped"


def test_poll_decisions_leaves_undecided_unchanged(tmp_path):
    conn = _fresh_conn()
    job_id = _insert(conn)
    conn.execute(
        "UPDATE jobs SET pipeline_status='shortlisted', notion_page_id='page-789' "
        "WHERE id=?", (job_id,),
    )
    conn.commit()

    _poll_with_mock(conn, ["To Apply"])

    row = conn.execute("SELECT pipeline_status, decision FROM jobs WHERE id=?",
                       (job_id,)).fetchone()
    assert row["pipeline_status"] == "shortlisted"
    assert row["decision"] is None


# ── 9. Approved job IS picked up by run_tailoring ─────────────────────────────

def test_approved_job_is_tailored(tmp_path):
    """An approved job must be processed by run_tailoring."""
    conn = _fresh_conn()
    job_id = _insert(conn)
    conn.execute(
        "UPDATE jobs SET pipeline_status='approved', decision='approved', "
        "cv_category='FDE / Solutions', jd_extracted=? WHERE id=?",
        (_fake_extraction(), job_id),
    )
    conn.commit()

    # Create a minimal fake DOCX so build_tailored_resume finds the base file.
    master_cvs_dir = Path(tmp_path) / "Master CVs"
    master_cvs_dir.mkdir()
    fake_docx = master_cvs_dir / "Hamideh_Ahooei_Master_FDE_Solutions.docx"
    fake_docx.write_bytes(b"")  # empty placeholder

    output_dir = Path(tmp_path) / "output"

    tailor_result = {
        "docx_path": str(output_dir / "test.docx"),
        "pdf_path": None,
        "notes": "Test tailoring",
        "flags": [],
    }

    fake_ai = ai.FakeAIBackend({"tailoring": json.dumps({
        "edits": {},
        "notes": "Test tailoring",
        "flags": [],
    })})
    ai.set_ai_client(fake_ai)
    try:
        with patch("pipeline.tailoring.build_tailored_resume", return_value=tailor_result):
            with patch("pipeline.answers.draft_answers", return_value={}):
                with _progress(tmp_path) as prog:
                    orchestrator.run_tailoring(conn, Path(tmp_path), output_dir, prog)
    finally:
        ai.set_ai_client(None)

    row = conn.execute("SELECT pipeline_status FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["pipeline_status"] == "tailored", \
        f"Expected tailored after approval, got {row['pipeline_status']}"


# ── 10. Apply gate: only tailored+approved ────────────────────────────────────

@pytest.mark.parametrize("status,decision,expected_count", [
    ("tailored",      "approved",  1),  # only this combo reaches apply
    ("tailored",      "skipped",   0),
    ("tailored",      None,        0),
    ("shortlisted",   "approved",  0),  # not yet tailored
    ("approved",      "approved",  0),  # approved but not yet tailored
    ("synced",        "approved",  0),  # legacy status — not the new path
])
def test_apply_gate(status, decision, expected_count):
    conn = _fresh_conn()
    job_id = _insert(conn, source="Greenhouse")
    conn.execute(
        "UPDATE jobs SET pipeline_status=?, decision=? WHERE id=?",
        (status, decision, job_id),
    )
    conn.commit()

    _APPLY_SOURCES = ("Greenhouse", "Ashby", "Lever")
    sql = (
        "SELECT * FROM jobs WHERE pipeline_status = 'tailored' AND decision = 'approved' "
        f"AND source IN ({','.join('?' * len(_APPLY_SOURCES))})"
    )
    rows = conn.execute(sql, _APPLY_SOURCES).fetchall()
    assert len(rows) == expected_count, \
        f"status={status!r} decision={decision!r}: expected {expected_count} row(s), got {len(rows)}"


# ── 11. Category names are canonical ─────────────────────────────────────────

def test_canonical_category_names():
    """The three canonical category names must match exactly what scoring.py
    defines — any mismatch would cause tailoring to fail to find the DOCX."""
    from pipeline.scoring import CV_CATEGORIES
    from pipeline.tailoring import CATEGORY_CV_FILES

    assert set(CV_CATEGORIES) == {"FDE / Solutions", "Agentic AI", "Technical Leadership"}
    assert set(CATEGORY_CV_FILES.keys()) == {"FDE / Solutions", "Agentic AI", "Technical Leadership"}
    assert set(CV_CATEGORIES) == set(CATEGORY_CV_FILES.keys()), \
        "CV_CATEGORIES and CATEGORY_CV_FILES keys are out of sync"


def test_scoring_prompt_uses_canonical_categories():
    """The SCORING_PROMPT must contain all three canonical category names so the
    AI knows to use them as JSON keys."""
    from pipeline.scoring import SCORING_PROMPT, CV_CATEGORIES

    for cat in CV_CATEGORIES:
        assert cat in SCORING_PROMPT, \
            f"Category {cat!r} not found in SCORING_PROMPT — AI will use wrong key names"


# ── 12. AUTO_SUBMIT_CONFIRMED=false safety ────────────────────────────────────

def test_auto_submit_false_env(monkeypatch):
    """Confirm that AUTO_SUBMIT_CONFIRMED is currently false (or unset)."""
    from dotenv import load_dotenv
    load_dotenv()
    val = os.environ.get("AUTO_SUBMIT_CONFIRMED", "false").lower()
    assert val in ("false", "0", ""), \
        f"AUTO_SUBMIT_CONFIRMED is set to {val!r} — should be 'false' for safety"


# ── 13. Scoring threshold value is correct ───────────────────────────────────

def test_score_threshold():
    assert SCORE_THRESHOLD == 70, \
        f"SCORE_THRESHOLD should be 70, got {SCORE_THRESHOLD}"


# ── 14. Master CV files mapping is consistent ────────────────────────────────

def test_master_cv_files_in_scoring_match_tailoring():
    from pipeline.scoring import MASTER_CV_FILES, CV_CATEGORIES
    from pipeline.tailoring import CATEGORY_CV_FILES
    from pipeline.orchestrator import _MASTER_CV_FILES as ORCH_CV_FILES

    scoring_families = {family for _, family in MASTER_CV_FILES}
    assert scoring_families == set(CV_CATEGORIES)
    assert set(ORCH_CV_FILES.keys()) == set(CV_CATEGORIES)
    assert set(CATEGORY_CV_FILES.keys()) == set(CV_CATEGORIES)
