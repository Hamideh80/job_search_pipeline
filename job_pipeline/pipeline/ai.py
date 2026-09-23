"""AI client abstraction for the pipeline.

This is the single place where AI backend selection and instantiation happen.
Every reasoning module (extraction, scoring, tailoring, answers, apply,
learning) calls get_ai_client() to obtain a configured AIClient rather than
instantiating Anthropic() directly.

Importing this module is safe without any API keys — the client is only
created on the first call to complete(), not at import time.

Backend selection
-----------------
Read from AI_BACKEND env var (default: "anthropic").
Currently implemented: "anthropic".
Step 5 will add "claude_code".

Injecting a fake backend for tests
------------------------------------
    from pipeline.ai import FakeAIBackend, set_ai_client
    set_ai_client(FakeAIBackend({"extraction": json.dumps({...})}))
    # ... run tests ...
    set_ai_client(None)   # resets to default on next get_ai_client() call
"""
import os
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
                AnthropicAPIBackend.
        """
        raise NotImplementedError


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
    raise ValueError(
        f"Unknown AI_BACKEND {backend!r}. "
        "Valid values: 'anthropic'. "
        "('claude_code' will be added in Step 5.)"
    )
