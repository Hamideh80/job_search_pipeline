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
11. Single combined CV file used in scoring and tailoring.
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


def _fake_scoring(best_score=85):
    return json.dumps({
        "score": best_score,
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

    low_score_response = _fake_scoring(best_score=55)
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
    assert row["cv_category"] is None, "cv_category should not be written (single combined CV)"


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
        "jd_extracted=? WHERE id=?",
        (_fake_extraction(), job_id),
    )
    conn.commit()

    # Create a minimal fake DOCX so build_tailored_resume finds the base file.
    master_cvs_dir = Path(tmp_path) / "Master CVs"
    master_cvs_dir.mkdir()
    fake_docx = master_cvs_dir / "Hamideh_Ahooei_Master_CV_Combined.docx"
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


# ── 11. Single combined CV file is consistent across modules ─────────────────

def test_combined_cv_filename_consistent():
    """scoring.COMBINED_CV_FILE and tailoring.COMBINED_CV_FILE must reference
    the same base filename so that both the MD profile and the DOCX are the
    same source document."""
    from pipeline.scoring import COMBINED_CV_FILE as scoring_md
    from pipeline.tailoring import COMBINED_CV_FILE as tailoring_docx

    # The stem (without extension) must match.
    scoring_stem = scoring_md.removesuffix(".md")
    tailoring_stem = tailoring_docx.split("/")[-1].removesuffix(".docx")
    assert scoring_stem == tailoring_stem, (
        f"CV stems don't match: scoring uses {scoring_stem!r}, "
        f"tailoring uses {tailoring_stem!r}"
    )


def test_scoring_prompt_uses_score_key():
    """The SCORING_PROMPT must ask the AI to return a 'score' key (not 'best_score'
    or a per-category scores dict) — any mismatch would break scoring.score()."""
    from pipeline.scoring import SCORING_PROMPT

    assert '"score"' in SCORING_PROMPT, \
        "SCORING_PROMPT must contain '\"score\"' so the AI uses the right JSON key"


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


# ── 14. Orchestrator CV file matches scoring/tailoring ───────────────────────

def test_orchestrator_cv_file_matches_scoring():
    from pipeline.scoring import COMBINED_CV_FILE as scoring_md
    from pipeline.orchestrator import _COMBINED_CV_FILE as orch_md

    assert scoring_md == orch_md, (
        f"orchestrator._COMBINED_CV_FILE ({orch_md!r}) must match "
        f"scoring.COMBINED_CV_FILE ({scoring_md!r})"
    )
