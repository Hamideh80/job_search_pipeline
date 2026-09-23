"""Tests for the Step-4 AI abstraction layer.

Verifies:
1. Domain modules can be imported without ANTHROPIC_API_KEY.
2. orchestrator can be imported without ANTHROPIC_API_KEY.
3. discover does not require ANTHROPIC_API_KEY unless AI is invoked.
4. FakeAIBackend can drive extraction and scoring end-to-end.
5. AnthropicAPIBackend raises a clear RuntimeError when invoked without key.
6. set_ai_client / get_ai_client injection round-trip works correctly.
7. All existing CLI / state / schema tests continue to pass (run via pytest).
"""
import json
import sys
import pytest
from unittest.mock import patch

# ── fixture: inject/reset fake AI backend ─────────────────────────────────────

@pytest.fixture(autouse=False)
def fake_ai(request):
    """Inject a FakeAIBackend and reset to None (default) after the test."""
    from pipeline import ai
    responses = getattr(request, "param", {})
    fake = ai.FakeAIBackend(responses)
    ai.set_ai_client(fake)
    yield fake
    ai.set_ai_client(None)  # reset — next call recreates from AI_BACKEND


# ── 1. domain modules import without ANTHROPIC_API_KEY ───────────────────────

def test_extraction_imports_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import importlib
    import pipeline.extraction as mod
    importlib.reload(mod)   # force fresh re-evaluation of module-level code


def test_scoring_imports_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import importlib
    import pipeline.scoring as mod
    importlib.reload(mod)


def test_tailoring_imports_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import importlib
    import pipeline.tailoring as mod
    importlib.reload(mod)


def test_answers_imports_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import importlib
    import pipeline.answers as mod
    importlib.reload(mod)


def test_apply_imports_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import importlib
    import pipeline.apply as mod
    importlib.reload(mod)


def test_learning_imports_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import importlib
    import pipeline.learning as mod
    importlib.reload(mod)


# ── 2. orchestrator imports without ANTHROPIC_API_KEY ────────────────────────

def test_orchestrator_imports_without_api_key(monkeypatch):
    """Importing orchestrator must not instantiate any AI or Notion client."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    import importlib
    import pipeline.orchestrator as mod
    importlib.reload(mod)


# ── 3. discover does not require ANTHROPIC_API_KEY ────────────────────────────

def test_discover_does_not_invoke_ai(monkeypatch, tmp_path):
    """run_discovery only hits external APIs — it must not call get_ai_client().

    We inject a FakeAIBackend that records calls; after a mocked discovery run
    we assert it was never invoked.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("NOTION_TOKEN", raising=False)

    from pipeline import ai
    fake = ai.FakeAIBackend()
    ai.set_ai_client(fake)

    from pipeline import db, orchestrator

    # Stub out the external fetchers so no network calls happen.
    monkeypatch.setattr("pipeline.orchestrator.load_watchlist",
                        lambda: {})  # empty watchlist → no fetchers called
    monkeypatch.setattr("pipeline.discovery.gmail_linkedin.fetch_job_links",
                        lambda label: [])

    conn = db.connect(":memory:")
    from pipeline.progress import RunProgress
    with RunProgress(log_dir=tmp_path) as progress:
        added = orchestrator.run_discovery(conn, progress)

    conn.close()
    ai.set_ai_client(None)

    assert added == 0
    assert len(fake.calls) == 0, (
        f"run_discovery invoked AI {len(fake.calls)} time(s) — it should not"
    )


# ── 4. FakeAIBackend drives extraction and scoring ────────────────────────────

_FAKE_EXTRACTION = {
    "must_haves": ["Python", "SQL"],
    "nice_to_haves": ["Tableau"],
    "years_required": 5,
    "years_required_context": "software engineering",
    "seniority": "Senior",
    "remote_policy": "Hybrid",
    "location": "Toronto",
    "certifications_required": [],
    "work_authorization_required": None,
}

_FAKE_SCORING = {
    "scores": {
        "AI Transformation Consultant": 85,
        "Technical Business Analyst": 75,
        "Implementation / FDE": 60,
    },
    "best_category": "AI Transformation Consultant",
    "best_score": 85,
    "strong_matches": ["AI and data experience"],
    "transferable_matches": [],
    "gaps": [],
    "interview_risk": [],
    "reasoning": "Strong match for AI roles based on documented experience.",
}


def test_fake_backend_drives_extraction():
    """extraction.extract() returns parsed data using FakeAIBackend — no HTTP."""
    from pipeline import ai, extraction

    fake = ai.FakeAIBackend({"extraction": json.dumps(_FAKE_EXTRACTION)})
    ai.set_ai_client(fake)
    try:
        result = extraction.extract("Senior Python engineer wanted, 5 years required.")
    finally:
        ai.set_ai_client(None)

    assert result["must_haves"] == ["Python", "SQL"]
    assert result["seniority"] == "Senior"
    assert result["years_required"] == 5
    assert len(fake.calls) == 1
    assert fake.calls[0]["purpose"] == "extraction"


def test_fake_backend_drives_scoring():
    """scoring.score() returns parsed scores using FakeAIBackend — no HTTP."""
    from pipeline import ai, scoring

    fake = ai.FakeAIBackend({"scoring": json.dumps(_FAKE_SCORING)})
    ai.set_ai_client(fake)
    try:
        result = scoring.score(_FAKE_EXTRACTION, "candidate profile text", "")
    finally:
        ai.set_ai_client(None)

    assert result["best_score"] == 85
    assert result["best_category"] == "AI Transformation Consultant"
    assert len(fake.calls) == 1
    assert fake.calls[0]["purpose"] == "scoring"


def test_fake_backend_records_all_calls():
    """FakeAIBackend.calls accumulates every complete() invocation."""
    from pipeline import ai

    fake = ai.FakeAIBackend({"default": "{}"})
    assert len(fake.calls) == 0
    fake.complete("prompt 1", max_tokens=100, purpose="p1")
    fake.complete("prompt 2", max_tokens=200, purpose="p2")
    assert len(fake.calls) == 2
    assert fake.calls[0]["purpose"] == "p1"
    assert fake.calls[1]["max_tokens"] == 200


# ── 5. AnthropicAPIBackend fails cleanly without key ─────────────────────────

def test_anthropic_backend_fails_cleanly_at_invocation(monkeypatch):
    """AnthropicAPIBackend.complete() raises RuntimeError (not KeyError) when
    ANTHROPIC_API_KEY is absent, and only at invocation time — not at import
    or instantiation time."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from pipeline.ai import AnthropicAPIBackend

    # Instantiation must not raise.
    backend = AnthropicAPIBackend()

    # complete() must raise a clear RuntimeError, not a KeyError.
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        backend.complete("test prompt", max_tokens=10, purpose="test")


def test_anthropic_backend_import_safe_without_key(monkeypatch):
    """Importing pipeline.ai must never require ANTHROPIC_API_KEY."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import importlib
    import pipeline.ai as mod
    importlib.reload(mod)   # must not raise


# ── 6. get_ai_client / set_ai_client injection round-trip ─────────────────────

def test_set_get_ai_client_round_trip():
    """set_ai_client then get_ai_client returns the injected instance."""
    from pipeline import ai

    fake = ai.FakeAIBackend()
    ai.set_ai_client(fake)
    try:
        assert ai.get_ai_client() is fake
    finally:
        ai.set_ai_client(None)


def test_set_none_resets_to_default(monkeypatch):
    """set_ai_client(None) causes the next get_ai_client() to build a fresh
    AnthropicAPIBackend (the default when AI_BACKEND is 'anthropic')."""
    from pipeline import ai

    ai.set_ai_client(None)
    # We don't want to actually build an AnthropicAPIBackend here — just verify
    # that the factory is called.  Patch _build_backend to avoid it.
    sentinel = ai.FakeAIBackend()
    with patch.object(ai, "_build_backend", return_value=sentinel) as mock_build:
        result = ai.get_ai_client()
        mock_build.assert_called_once()
        assert result is sentinel
    ai.set_ai_client(None)   # cleanup


def test_unknown_backend_raises():
    """_build_backend raises ValueError for unknown backend names."""
    from pipeline.ai import _build_backend
    with pytest.raises(ValueError, match="Unknown AI_BACKEND"):
        _build_backend("some_future_backend_not_yet_supported")


# ── 7. ai.py itself does not import anthropic at module level ─────────────────

def test_ai_module_does_not_import_anthropic_at_module_level():
    """The 'anthropic' package must not be in sys.modules due solely to
    importing pipeline.ai (the import is deferred inside _ensure_client)."""
    import importlib

    # Remove pipeline.ai from sys.modules to force a fresh load.
    for key in list(sys.modules):
        if key == "pipeline.ai":
            del sys.modules[key]

    # Also remove anthropic so we can detect if re-importing pipeline.ai
    # causes it to be loaded.
    anthropic_was_loaded = "anthropic" in sys.modules
    for key in list(sys.modules):
        if key == "anthropic" or key.startswith("anthropic."):
            del sys.modules[key]

    import pipeline.ai  # noqa: F401 (just checking the import side-effect)

    # If anthropic got pulled in, it must have been because it was already
    # loaded before this test (transitively by another test).  That's fine.
    # What we want to confirm is that pipeline.ai itself does not have a
    # top-level `from anthropic import ...` or `import anthropic` statement.
    # We verify this by reading the source and asserting no such bare import.
    import ast, inspect
    source = inspect.getsource(pipeline.ai)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # Top-level imports (not inside a function/method body)
            # The import inside _ensure_client is in a function body, so
            # its parent will not be the module.
            pass  # We check via the function-body condition below

    # Simpler check: confirm 'from anthropic import' does NOT appear at
    # module scope (outside any def/class).
    for node in tree.body:
        assert not (isinstance(node, ast.ImportFrom) and node.module == "anthropic"), (
            "pipeline/ai.py has a module-level 'from anthropic import ...' — "
            "this would crash without the package installed at import time."
        )
        assert not (isinstance(node, ast.Import)
                    and any(alias.name == "anthropic" for alias in node.names)), (
            "pipeline/ai.py has a module-level 'import anthropic' statement."
        )
