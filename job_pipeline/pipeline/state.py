"""Canonical job state machine.

Every pipeline_status value that can appear in the jobs table is declared
here. The transition map encodes what moves are legal, and the transition()
helper enforces it at runtime.

Legacy states (synced, ready_to_submit) are the values written by the
pre-Step-2 pipeline. They remain valid so that existing database rows are
never invalidated. Future pipeline code should write the new equivalents;
the legacy states are kept in TRANSITIONS so any row in the DB can still
advance without manual repair.

Transition map design notes:
- States not in TRANSITIONS are treated as unknown (future extension or
  external writes). transition() allows any move FROM an unknown state
  rather than rejecting rows we don't recognize.
- Terminal states map to an empty frozenset. Attempting to advance a
  terminal state raises InvalidTransitionError.
- failed → discovered allows a full retry from scratch.
- apply_failed → applying allows re-attempting the application step.
"""
from enum import Enum


class JobStatus(str, Enum):
    """String enum so values compare equal to plain strings from the DB."""

    # ── main lifecycle ────────────────────────────────────────────────────
    DISCOVERED        = "discovered"
    NEEDS_JD          = "needs_jd"          # JD text unavailable at discovery time
    EXTRACTED         = "extracted"
    SCORED            = "scored"
    SKIPPED_LOW_SCORE = "skipped_low_score"        # score < threshold; never sent to Notion
    SKIPPED_LANGUAGE  = "skipped_language_requirement"  # hard French-mandatory filter
    SHORTLISTED       = "shortlisted"        # score ≥ threshold; Notion card created
    SKIPPED_HUMAN     = "skipped_human"      # human set Skip in Notion
    APPROVED          = "approved"           # human set Approved in Notion
    TAILORED          = "tailored"
    READY_TO_APPLY    = "ready_to_apply"
    APPLYING          = "applying"
    APPLIED           = "applied"

    # ── failure / recovery ────────────────────────────────────────────────
    APPLY_FAILED      = "apply_failed"
    FAILED            = "failed"

    # ── legacy (written by pre-Step-2 code; preserved for backward compat) ──
    SYNCED            = "synced"          # old name for "Notion card created"
    READY_TO_SUBMIT   = "ready_to_submit" # old name for "form filled, dry-run"


# ---------------------------------------------------------------------------
# Transition map
# Keys are source states; values are the set of states reachable from them.
# An empty frozenset marks a terminal state.
# States absent from this dict are treated as unknown: any target is allowed.
# ---------------------------------------------------------------------------
TRANSITIONS: dict[str, frozenset] = {
    # ── main lifecycle ──────────────────────────────────────────────────
    "discovered":        frozenset({"extracted", "needs_jd", "failed",
                                    "skipped_language_requirement"}),
    "needs_jd":          frozenset({"extracted", "failed"}),
    "extracted":         frozenset({"scored", "skipped_low_score", "failed",
                                    "shortlisted"}),  # scoring now goes directly to shortlisted
    "scored":            frozenset({
                             "shortlisted", "skipped_low_score", "failed",
                             "tailored",        # legacy branch: scored → tailored
                         }),
    "skipped_low_score":          frozenset(),   # terminal
    "skipped_language_requirement": frozenset(), # terminal
    "shortlisted":       frozenset({
                             "approved", "skipped_human", "failed",
                             "tailored",        # legacy branch: shortlisted → tailored
                         }),
    "skipped_human":     frozenset(),           # terminal
    "approved":          frozenset({"tailored", "failed"}),
    "tailored":          frozenset({
                             "ready_to_apply", "failed",
                             "synced",          # legacy branch: tailored → synced
                         }),
    "ready_to_apply":    frozenset({"applying", "applied", "failed"}),
    "applying":          frozenset({"applied", "apply_failed", "failed"}),
    "applied":           frozenset(),           # terminal
    "apply_failed":      frozenset({"applying", "failed"}),
    "failed":            frozenset({"discovered"}),  # full retry

    # ── legacy states ───────────────────────────────────────────────────
    "synced":            frozenset({
                             "ready_to_submit", "applied", "failed",
                             "approved", "ready_to_apply",
                         }),
    "ready_to_submit":   frozenset({"applied", "failed", "ready_to_apply"}),
}

# Convenience sets ---------------------------------------------------------

ALL_STATUSES: frozenset = frozenset(TRANSITIONS)

TERMINAL: frozenset = frozenset(
    status for status, targets in TRANSITIONS.items() if not targets
)

# States a new run can safely re-enter and advance
RETRYABLE: frozenset = frozenset(
    s for s in ALL_STATUSES if s not in TERMINAL
)

LEGACY: frozenset = frozenset({"synced", "ready_to_submit"})


# ---------------------------------------------------------------------------
# Transition helper
# ---------------------------------------------------------------------------

class InvalidTransitionError(ValueError):
    """Raised when a pipeline_status move is not in the transition map."""


def transition(current: str, target: str) -> str:
    """Validate and return *target* if the move is legal.

    Rules:
    - If *current* is not in TRANSITIONS (unknown / future state), the move
      is allowed without validation so we never reject rows written by newer
      code running against an older state.py.
    - If *current* IS in TRANSITIONS and *target* is not in its allowed set,
      InvalidTransitionError is raised.
    - Returns *target* (the plain string) so callers can write::

          db.update_job(conn, job_id, pipeline_status=transition(row["pipeline_status"], "extracted"))
    """
    allowed = TRANSITIONS.get(current)
    if allowed is None:
        # Unknown source state — allow without validation.
        return target
    if target not in allowed:
        raise InvalidTransitionError(
            f"Illegal transition {current!r} → {target!r}. "
            f"Allowed from {current!r}: {sorted(allowed) or '(terminal — no moves allowed)'}"
        )
    return target
