"""Unit tests for ClaudeCodeBackend (subprocess mocked throughout).

Covers:
  - success path
  - missing CLI (FileNotFoundError)
  - timeout (TimeoutExpired → TimeoutError)
  - non-zero exit code
  - empty stdout (exit 0 but no output)
  - ANTHROPIC_API_KEY is not passed to child process
  - _build_backend('claude_code') returns ClaudeCodeBackend
  - existing 63 tests continue passing (run full suite via pytest)
"""
import subprocess
from unittest.mock import MagicMock, patch, call

import pytest

from pipeline.ai import ClaudeCodeBackend, _build_backend


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_result(stdout: str, returncode: int = 0, stderr: str = "") -> MagicMock:
    r = MagicMock()
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


def _backend() -> ClaudeCodeBackend:
    b = ClaudeCodeBackend()
    b._cli = "claude"          # skip PATH search in tests
    return b


# ── success ───────────────────────────────────────────────────────────────────

def test_success_returns_stripped_text():
    """complete() returns stdout.strip() on success."""
    backend = _backend()
    with patch("subprocess.run", return_value=_make_result('  {"key": 1}\n')) as mock_run:
        result = backend.complete("some prompt", max_tokens=100, purpose="extraction")

    assert result == '{"key": 1}'
    mock_run.assert_called_once()


def test_stdin_used_for_prompt():
    """Prompt is passed via stdin (input=), not as a positional arg."""
    backend = _backend()
    with patch("subprocess.run", return_value=_make_result("ok")) as mock_run:
        backend.complete("my prompt text", max_tokens=100)

    _, kwargs = mock_run.call_args
    assert kwargs.get("input") == "my prompt text", "prompt must go via stdin"
    # Prompt must NOT appear in the CLI args list
    cli_args = mock_run.call_args[0][0]
    assert "my prompt text" not in cli_args


def test_shell_false():
    """subprocess.run is called with shell=False (the default; confirmed explicitly)."""
    backend = _backend()
    with patch("subprocess.run", return_value=_make_result("ok")) as mock_run:
        backend.complete("p", max_tokens=10)
    _, kwargs = mock_run.call_args
    # shell=False is the default; we verify it's not True
    assert kwargs.get("shell", False) is False


def test_purpose_in_command_label(capsys):
    """purpose= is included in error messages (not necessarily in the cmd args)."""
    backend = _backend()
    with patch("subprocess.run", return_value=_make_result("", returncode=1, stderr="oops")):
        with pytest.raises(RuntimeError) as exc_info:
            backend.complete("p", max_tokens=10, purpose="scoring")
    assert "scoring" in str(exc_info.value)


# ── missing CLI ───────────────────────────────────────────────────────────────

def test_missing_cli_raises_file_not_found():
    """FileNotFoundError when 'claude' is not on PATH."""
    backend = ClaudeCodeBackend()
    backend._cli = None
    with patch("shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError, match="claude CLI not found"):
            backend.complete("p", max_tokens=10)


# ── timeout ───────────────────────────────────────────────────────────────────

def test_timeout_raises_timeout_error():
    """TimeoutExpired from subprocess is re-raised as TimeoutError."""
    backend = _backend()
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=5)):
        with pytest.raises(TimeoutError, match="timed out"):
            backend.complete("p", max_tokens=10, purpose="extraction")


def test_custom_timeout_used():
    """CLAUDE_CODE_TIMEOUT env var sets the timeout passed to subprocess."""
    import os
    with patch.dict(os.environ, {"CLAUDE_CODE_TIMEOUT": "42"}):
        backend = ClaudeCodeBackend()
        backend._cli = "claude"
    with patch("subprocess.run", return_value=_make_result("ok")) as mock_run:
        backend.complete("p", max_tokens=10)
    _, kwargs = mock_run.call_args
    assert kwargs.get("timeout") == 42


# ── non-zero exit ─────────────────────────────────────────────────────────────

def test_nonzero_exit_raises_runtime_error():
    """Non-zero return code raises RuntimeError with exit code in message."""
    backend = _backend()
    with patch("subprocess.run",
               return_value=_make_result("", returncode=1, stderr="fatal error")):
        with pytest.raises(RuntimeError, match="exit.*1|1.*exit"):
            backend.complete("p", max_tokens=10)


def test_nonzero_exit_includes_stderr():
    """RuntimeError from non-zero exit contains stderr content."""
    backend = _backend()
    with patch("subprocess.run",
               return_value=_make_result("", returncode=2, stderr="auth failure")):
        with pytest.raises(RuntimeError, match="auth failure"):
            backend.complete("p", max_tokens=10)


# ── empty output ──────────────────────────────────────────────────────────────

def test_empty_stdout_raises_runtime_error():
    """Empty stdout with exit 0 is treated as an error, not a valid response."""
    backend = _backend()
    with patch("subprocess.run", return_value=_make_result("", returncode=0)):
        with pytest.raises(RuntimeError, match="empty output"):
            backend.complete("p", max_tokens=10)


def test_whitespace_only_stdout_raises_runtime_error():
    """Whitespace-only stdout is also treated as empty."""
    backend = _backend()
    with patch("subprocess.run", return_value=_make_result("   \n\t  ", returncode=0)):
        with pytest.raises(RuntimeError, match="empty output"):
            backend.complete("p", max_tokens=10)


# ── ANTHROPIC_API_KEY stripped from child env ─────────────────────────────────

def test_api_key_not_in_child_env(monkeypatch):
    """ANTHROPIC_API_KEY must never be forwarded to the claude subprocess."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-key")

    backend = _backend()
    captured_env = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env") or {})
        return _make_result("ok")

    with patch("subprocess.run", side_effect=fake_run):
        backend.complete("p", max_tokens=10)

    assert "ANTHROPIC_API_KEY" not in captured_env, (
        "ANTHROPIC_API_KEY was forwarded to the child process — "
        "ClaudeCodeBackend must strip it"
    )


def test_child_env_preserves_other_vars(monkeypatch):
    """Only ANTHROPIC_API_KEY is stripped; other env vars are preserved."""
    monkeypatch.setenv("MY_OTHER_VAR", "should_survive")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should_be_stripped")

    backend = _backend()
    captured_env = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env") or {})
        return _make_result("ok")

    with patch("subprocess.run", side_effect=fake_run):
        backend.complete("p", max_tokens=10)

    assert "MY_OTHER_VAR" in captured_env
    assert captured_env["MY_OTHER_VAR"] == "should_survive"


# ── backend factory ───────────────────────────────────────────────────────────

def test_build_backend_claude_code():
    """_build_backend('claude_code') returns a ClaudeCodeBackend instance."""
    from pipeline.ai import ClaudeCodeBackend as CCB, _build_backend as bb
    backend = bb("claude_code")
    assert isinstance(backend, CCB)


def test_build_backend_claude_code_env(monkeypatch):
    """AI_BACKEND=claude_code env var selects ClaudeCodeBackend."""
    monkeypatch.setenv("AI_BACKEND", "claude_code")
    from pipeline.ai import ClaudeCodeBackend as CCB, _build_backend as bb
    backend = bb()
    assert isinstance(backend, CCB)


# ── works without ANTHROPIC_API_KEY ──────────────────────────────────────────

def test_claude_code_backend_no_api_key(monkeypatch):
    """ClaudeCodeBackend.complete() must succeed without ANTHROPIC_API_KEY set."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    backend = _backend()
    with patch("subprocess.run", return_value=_make_result('{"ok": true}')):
        result = backend.complete("p", max_tokens=10)
    assert result == '{"ok": true}'


# ── CLI flags ─────────────────────────────────────────────────────────────────

def test_cli_uses_print_flag():
    """The -p / --print flag must be in the command."""
    backend = _backend()
    with patch("subprocess.run", return_value=_make_result("ok")) as mock_run:
        backend.complete("p", max_tokens=10)
    cmd = mock_run.call_args[0][0]
    assert "-p" in cmd or "--print" in cmd


def test_cli_uses_text_output_format():
    """--output-format text must be used (avoids JSON envelope)."""
    backend = _backend()
    with patch("subprocess.run", return_value=_make_result("ok")) as mock_run:
        backend.complete("p", max_tokens=10)
    cmd = mock_run.call_args[0][0]
    assert "--output-format" in cmd
    idx = cmd.index("--output-format")
    assert cmd[idx + 1] == "text"


def test_cli_no_session_persistence():
    """--no-session-persistence must be present to avoid state leakage."""
    backend = _backend()
    with patch("subprocess.run", return_value=_make_result("ok")) as mock_run:
        backend.complete("p", max_tokens=10)
    cmd = mock_run.call_args[0][0]
    assert "--no-session-persistence" in cmd
