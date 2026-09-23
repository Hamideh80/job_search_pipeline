"""Tests for pipeline/state.py — state machine transitions."""
import pytest

from pipeline.state import (
    LEGACY,
    TERMINAL,
    InvalidTransitionError,
    JobStatus,
    transition,
)


# ── valid transitions ─────────────────────────────────────────────────────────

def test_discovered_to_extracted():
    assert transition("discovered", "extracted") == "extracted"


def test_discovered_to_needs_jd():
    assert transition("discovered", "needs_jd") == "needs_jd"


def test_extracted_to_scored():
    assert transition("extracted", "scored") == "scored"


def test_scored_to_shortlisted():
    assert transition("scored", "shortlisted") == "shortlisted"


def test_scored_to_skipped_low_score():
    assert transition("scored", "skipped_low_score") == "skipped_low_score"


def test_shortlisted_to_approved():
    assert transition("shortlisted", "approved") == "approved"


def test_shortlisted_to_skipped_human():
    assert transition("shortlisted", "skipped_human") == "skipped_human"


def test_approved_to_tailored():
    assert transition("approved", "tailored") == "tailored"


def test_tailored_to_ready_to_apply():
    assert transition("tailored", "ready_to_apply") == "ready_to_apply"


def test_ready_to_apply_to_applying():
    assert transition("ready_to_apply", "applying") == "applying"


def test_applying_to_applied():
    assert transition("applying", "applied") == "applied"


def test_apply_failed_retry():
    assert transition("apply_failed", "applying") == "applying"


def test_failed_to_discovered():
    assert transition("failed", "discovered") == "discovered"


# ── invalid transitions ───────────────────────────────────────────────────────

def test_discovered_cannot_go_to_applied():
    with pytest.raises(InvalidTransitionError):
        transition("discovered", "applied")


def test_extracted_cannot_go_to_approved():
    with pytest.raises(InvalidTransitionError):
        transition("extracted", "approved")


def test_shortlisted_cannot_go_to_applying():
    with pytest.raises(InvalidTransitionError):
        transition("shortlisted", "applying")


# ── terminal states ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["applied", "skipped_low_score", "skipped_human"])
def test_terminal_raises(status):
    with pytest.raises(InvalidTransitionError):
        transition(status, "discovered")


def test_terminal_set_contains_expected():
    assert "applied" in TERMINAL
    assert "skipped_low_score" in TERMINAL
    assert "skipped_human" in TERMINAL


# ── legacy states ─────────────────────────────────────────────────────────────

def test_legacy_set():
    assert LEGACY == {"synced", "ready_to_submit"}


def test_legacy_synced_to_approved():
    assert transition("synced", "approved") == "approved"


def test_legacy_ready_to_submit_to_applied():
    assert transition("ready_to_submit", "applied") == "applied"


# ── unknown states ────────────────────────────────────────────────────────────

def test_unknown_state_allows_any_target():
    """States not in TRANSITIONS (future extension) must not raise."""
    result = transition("some_future_state", "extracted")
    assert result == "extracted"


# ── JobStatus enum ────────────────────────────────────────────────────────────

def test_job_status_str_equality():
    """str Enum values must compare equal to plain strings from the DB."""
    assert JobStatus.DISCOVERED == "discovered"
    assert JobStatus.APPLIED == "applied"
    assert JobStatus.SYNCED == "synced"
