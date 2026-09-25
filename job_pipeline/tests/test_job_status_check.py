"""Tests for job_status_check.is_job_open (HTTP mocked throughout)."""
from unittest.mock import patch, MagicMock

import pytest

from pipeline.job_status_check import is_job_open


def _mock_response(status_code: int, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.text = text
    return r


def _patch_get(status_code: int, text: str = ""):
    return patch(
        "pipeline.job_status_check.requests.get",
        return_value=_mock_response(status_code, text),
    )


# ── open jobs ────────────────────────────────────────────────────────────────

def test_http_200_no_closed_phrase_is_open():
    with _patch_get(200, "<html>Apply now! Great opportunity.</html>"):
        assert is_job_open("https://boards.greenhouse.io/company/jobs/123") is True


def test_linkedin_200_no_closed_phrase_is_open():
    with _patch_get(200, "<h1>Senior Engineer</h1><p>Join our team.</p>"):
        assert is_job_open("https://www.linkedin.com/jobs/view/4421222626/") is True


def test_empty_url_is_open():
    """No URL → assume open, no HTTP call made."""
    with patch("pipeline.job_status_check.requests.get") as mock_get:
        assert is_job_open("") is True
        mock_get.assert_not_called()


def test_network_error_treated_as_open():
    with patch("pipeline.job_status_check.requests.get", side_effect=Exception("timeout")):
        assert is_job_open("https://example.com/job/1") is True


# ── closed jobs — HTTP status ─────────────────────────────────────────────────

def test_http_404_is_closed():
    with _patch_get(404):
        assert is_job_open("https://boards.greenhouse.io/company/jobs/999") is False


def test_http_410_is_closed():
    with _patch_get(410):
        assert is_job_open("https://jobs.lever.co/company/uuid") is False


# ── closed jobs — phrase detection ───────────────────────────────────────────

def test_no_longer_accepting_applications():
    body = "<p>No longer accepting applications</p>"
    with _patch_get(200, body):
        assert is_job_open("https://boards.greenhouse.io/company/jobs/123") is False


def test_not_currently_accepting_applications():
    body = '<div class="closed-notice">Not currently accepting applications</div>'
    with _patch_get(200, body):
        assert is_job_open("https://www.linkedin.com/jobs/view/123456/") is False


def test_not_accepting_applications():
    body = "We are not accepting applications at this time."
    with _patch_get(200, body):
        assert is_job_open("https://example.com/job") is False


def test_this_job_is_no_longer():
    body = "This job is no longer available."
    with _patch_get(200, body):
        assert is_job_open("https://jobs.ashbyhq.com/company/uuid") is False


def test_case_insensitive_phrase():
    body = "NO LONGER ACCEPTING APPLICATIONS"
    with _patch_get(200, body):
        assert is_job_open("https://example.com/job") is False


# ── LinkedIn guest API routing ────────────────────────────────────────────────

def test_linkedin_url_uses_guest_api():
    """LinkedIn job URLs should hit the guest API, not the full page."""
    with patch("pipeline.job_status_check.requests.get",
               return_value=_mock_response(200, "open role")) as mock_get:
        is_job_open("https://www.linkedin.com/jobs/view/4421222626/")
    called_url = mock_get.call_args[0][0]
    assert "jobs-guest/jobs/api/jobPosting/4421222626" in called_url


def test_linkedin_comm_url_extracts_id():
    with patch("pipeline.job_status_check.requests.get",
               return_value=_mock_response(200, "open role")) as mock_get:
        is_job_open("https://www.linkedin.com/comm/jobs/view/9876543210/")
    called_url = mock_get.call_args[0][0]
    assert "9876543210" in called_url


def test_non_linkedin_url_fetched_directly():
    with patch("pipeline.job_status_check.requests.get",
               return_value=_mock_response(200, "open")) as mock_get:
        is_job_open("https://boards.greenhouse.io/company/jobs/123456")
    called_url = mock_get.call_args[0][0]
    assert "greenhouse.io" in called_url
    assert "linkedin" not in called_url
