"""Tests for pipeline/tailoring.py — set_paragraph_text label-dedup fix."""
from unittest.mock import MagicMock

from pipeline.tailoring import set_paragraph_text


def _make_run(text, bold=False):
    r = MagicMock()
    r.text = text
    r.bold = bold
    return r


def _make_paragraph(runs):
    p = MagicMock()
    p.runs = runs
    return p


# ── label dedup (the duplicate-prefix bug) ───────────────────────────────────

def test_label_prefix_not_duplicated_when_ai_echoes_it():
    """AI returns 'Skills: foo'; bold run already has 'Skills:' — must not double it."""
    bold_run = _make_run("Skills:", bold=True)
    body_run = _make_run("original content")
    p = _make_paragraph([bold_run, body_run])

    set_paragraph_text(p, "Skills: foo bar baz")

    assert bold_run.text == "Skills:"
    assert body_run.text == "foo bar baz"


def test_label_prefix_stripped_without_trailing_space():
    """Edge case: AI echoes label with no space after colon."""
    bold_run = _make_run("Skills:", bold=True)
    body_run = _make_run("original")
    p = _make_paragraph([bold_run, body_run])

    set_paragraph_text(p, "Skills:foo bar")

    assert body_run.text == "foo bar"


def test_no_label_run_leaves_text_unchanged():
    """Non-bold first run: just replaces last run's text as-is."""
    run = _make_run("hello", bold=False)
    p = _make_paragraph([run])

    set_paragraph_text(p, "new content")

    assert run.text == "new content"


def test_label_run_new_text_without_prefix_is_untouched():
    """AI returns text that does NOT start with the label — leave it alone."""
    bold_run = _make_run("Skills:", bold=True)
    body_run = _make_run("original")
    p = _make_paragraph([bold_run, body_run])

    set_paragraph_text(p, "Python · Java · Kotlin")

    assert bold_run.text == "Skills:"
    assert body_run.text == "Python · Java · Kotlin"


def test_empty_paragraph_returns_early():
    p = _make_paragraph([])
    set_paragraph_text(p, "anything")  # must not raise
