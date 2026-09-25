"""AI client abstraction for the pipeline.

This is the single place where AI backend selection and instantiation happen.
Every reasoning module (extraction, scoring, tailoring, answers, apply,
learning) calls get_ai_client() to obtain a configured AIClient rather than
instantiating Anthropic() directly.

Importing this module is safe without any API keys — the client is only
created on the first call to complete(), not at import time.

Backend selection
-----------------
Set via AI_BACKEND env var:
  AI_BACKEND=anthropic    (default) — Anthropic Python SDK, requires ANTHROPIC_API_KEY
  AI_BACKEND=claude_code  — Claude Code CLI, uses OAuth/subscription auth

Configuration
-------------
  ANTHROPIC_MODEL      model for AnthropicAPIBackend  (default: claude-sonnet-4-5)
  CLAUDE_CODE_MODEL    model for ClaudeCodeBackend     (default: claude-sonnet-4-5)
  CLAUDE_CODE_TIMEOUT  seconds per call for CLI backend (default: 120)

Injecting a fake backend for tests
-----------------------------------
    from pipeline.ai import FakeAIBackend, set_ai_client
    set_ai_client(FakeAIBackend({"extraction": json.dumps({...})}))
    # ... run tests ...
    set_ai_client(None)   # resets to default on next get_ai_client() call
"""
import os
import subprocess
import sys
from typing import Optional


class AIClient:
    """Base AI interface.

    A backend must implement complete() and return the raw text content of
    the model's response (not parsed — callers do their own JSON parsing).
    """

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        purpose: Optional[str] = None,
    ) -> str:
        """Send *prompt* to the model and return the response text.

        Args:
            prompt: The full user-turn prompt string.
            max_tokens: Hard limit on response length.
            purpose: Optional label (e.g. "extraction", "scoring") used by
                FakeAIBackend to select a canned response.  Ignored by
                AnthropicAPIBackend.  Logged by ClaudeCodeBackend.
        """
        raise NotImplementedError


# ── Anthropic SDK backend ─────────────────────────────────────────────────────

class AnthropicAPIBackend(AIClient):
    """Anthropic Python SDK backend.

    ANTHROPIC_API_KEY is read lazily — only on the first complete() call, so
    importing this class (or instantiating it) is safe without the key set.
    A clear RuntimeError is raised at call time if the key is missing.
    """

    def __init__(self) -> None:
        self._client = None
        # Read model once at instantiation (has a safe default; won't crash).
        self._model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")

    def _ensure_client(self):
        if self._client is None:
            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set. "
                    "Add it to your .env file or the system environment. "
                    "It is required when AI_BACKEND=anthropic (the default)."
                )
            from anthropic import Anthropic  # import deferred — safe at module level
            self._client = Anthropic(api_key=key)
        return self._client

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        purpose: Optional[str] = None,
    ) -> str:
        client = self._ensure_client()
        message = client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()


# ── Claude Code CLI backend ───────────────────────────────────────────────────

class ClaudeCodeBackend(AIClient):
    """Invoke Claude Code CLI non-interactively via subprocess.

    Uses your local Claude Code OAuth/subscription authentication — does NOT
    require ANTHROPIC_API_KEY.  The API key is explicitly stripped from the
    child process environment so there is no ambiguity about which auth path
    is exercised.

    Configuration env vars (all optional):
        CLAUDE_CODE_MODEL    model alias or full ID (default: claude-sonnet-4-5)
        CLAUDE_CODE_TIMEOUT  per-call timeout in seconds        (default: 120)

    Why no --dangerously-skip-permissions
    ---------------------------------------
    These reasoning calls ask for pure JSON output from a prompt.  The model
    has no reason to use filesystem, shell, or browser tools.  Skipping
    permissions would be unnecessary and would widen the attack surface if
    a prompt ever contained injected tool-call instructions.  No skip flag
    is used; if tool calls appear in output they are harmless because the
    output is parsed as JSON and any surrounding prose causes a parse error.
    """

    _CLI_CANDIDATES = ["claude"]   # searched on PATH; add full paths if needed

    def __init__(self) -> None:
        self._model = os.environ.get("CLAUDE_CODE_MODEL", "claude-sonnet-4-5")
        self._timeout = int(os.environ.get("CLAUDE_CODE_TIMEOUT", "120"))
        self._cli = None

    def _find_cli(self) -> str:
        """Return the first claude executable found on PATH, or raise."""
        if self._cli:
            return self._cli
        import shutil
        for name in self._CLI_CANDIDATES:
            path = shutil.which(name)
            if path:
                self._cli = path
                return path
        raise FileNotFoundError(
            "claude CLI not found on PATH. "
            "Install Claude Code: https://claude.ai/code"
        )

    @staticmethod
    def _child_env() -> dict:
        """Return the os.environ without ANTHROPIC_API_KEY.

        The CLI uses OAuth/keychain auth.  Explicitly removing the API key
        guarantees the subprocess cannot fall back to it, proving that the
        ClaudeCodeBackend is independent of the project's API key.
        """
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)
        return env

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        purpose: Optional[str] = None,
    ) -> str:
        """Run the prompt through the Claude Code CLI and return the text.

        Raises
        ------
        FileNotFoundError   claude CLI not found on PATH.
        TimeoutError        Call exceeded CLAUDE_CODE_TIMEOUT seconds.
        RuntimeError        CLI exited non-zero or returned empty output.
        """
        cli = self._find_cli()
        cmd = [
            cli,
            "-p",                        # non-interactive / print mode
            "--output-format", "text",   # plain text stdout (no JSON envelope)
            "--no-session-persistence",  # don't bleed state between calls
            "--model", self._model,
        ]

        label = f"[claude_code/{purpose or 'unknown'}]"
        try:
            result = subprocess.run(
                cmd,
                input=prompt,           # prompt via stdin (no arg-length limits)
                capture_output=True,
                text=True,
                encoding="utf-8",       # explicit UTF-8 — Windows default is cp1252
                timeout=self._timeout,
                env=self._child_env(),
                # shell=False is the default — no shell expansion, no injection risk
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError(
                f"{label} timed out after {self._timeout}s. "
                "Increase CLAUDE_CODE_TIMEOUT if needed."
            )
        except FileNotFoundError:
            raise FileNotFoundError(
                "claude CLI not found on PATH. "
                "Install Claude Code: https://claude.ai/code"
            )

        if result.returncode != 0:
            stderr_snip = result.stderr.strip()[:400] if result.stderr else ""
            stdout_snip = result.stdout.strip()[:200] if result.stdout else ""
            raise RuntimeError(
                f"{label} CLI exited with code {result.returncode}. "
                f"stderr={stderr_snip!r} stdout={stdout_snip!r}"
            )

        output = result.stdout.strip()
        if not output:
            raise RuntimeError(
                f"{label} CLI returned empty output (exit 0). "
                f"stderr={result.stderr.strip()[:200]!r}"
            )

        return output


# ── Fake backend (tests only) ─────────────────────────────────────────────────

class FakeAIBackend(AIClient):
    """Test-only backend: returns canned responses without any network calls.

    Responses are keyed by *purpose*.  If the purpose has no registered
    response the "default" key is tried; if that's also absent, "{}" is
    returned so every domain module's JSON parsing still runs.

    Usage::

        fake = FakeAIBackend({
            "extraction": json.dumps({...valid extraction dict...}),
            "scoring":    json.dumps({...valid scoring dict...}),
        })
        set_ai_client(fake)
        result = extraction.extract("some JD text")
        set_ai_client(None)  # reset after test
    """

    def __init__(self, responses: Optional[dict] = None) -> None:
        self._responses: dict = responses or {}
        self.calls: list[dict] = []  # records every complete() call for assertions

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        purpose: Optional[str] = None,
    ) -> str:
        self.calls.append({"purpose": purpose, "max_tokens": max_tokens,
                           "prompt_len": len(prompt)})
        if purpose and purpose in self._responses:
            return self._responses[purpose]
        return self._responses.get("default", "{}")


# ── singleton management ──────────────────────────────────────────────────────

_default: Optional[AIClient] = None


def get_ai_client() -> AIClient:
    """Return the configured AI client (lazy singleton).

    Creates the backend on first call using AI_BACKEND env var.  Tests can
    call set_ai_client(FakeAIBackend(...)) to inject a fake before calling
    any domain function, and set_ai_client(None) to restore the default.
    """
    global _default
    if _default is None:
        _default = _build_backend()
    return _default


def set_ai_client(client: Optional[AIClient]) -> None:
    """Override the global AI client.  Pass None to reset to the default
    (a new backend will be created from AI_BACKEND on the next get_ai_client
    call).  Intended for tests and tool-switching only."""
    global _default
    _default = client


def _build_backend(name: Optional[str] = None) -> AIClient:
    """Instantiate the backend named by *name* (or AI_BACKEND env var)."""
    backend = (name or os.environ.get("AI_BACKEND", "anthropic")).lower().strip()
    if backend == "anthropic":
        return AnthropicAPIBackend()
    if backend == "claude_code":
        return ClaudeCodeBackend()
    raise ValueError(
        f"Unknown AI_BACKEND {backend!r}. "
        "Valid values: 'anthropic', 'claude_code'."
    )
