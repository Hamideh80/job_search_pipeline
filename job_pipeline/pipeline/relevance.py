"""Cheap discovery relevance filter — no AI calls, no network.

Two sequential gates applied between discovery and AI extraction:

  Gate 1 — Title blocklist
    Job title matches an obvious-negative pattern → skipped_irrelevant.
    Patterns are conservative: full-phrase matches on role functions that
    are unambiguously outside all three target families (Sales, HR,
    Finance/Legal, non-technical Marketing, placeholder roles, co-ops).

  Gate 2 — Weighted title + JD evidence
    Accumulates positive and negative signals from title and JD body.
    Score below threshold → skipped_irrelevant.
    Score at or above threshold → proceed (status stays 'discovered').

Signal weights:
  Title strong positive   +10  (clearly target-family role function)
  Title moderate positive  +5  (plausible; needs JD support to pass)
  JD positive signal       +3  (each, role-specific; capped at +15 total)
  JD hard-negative        −15  (each; a single match makes recovery very hard)

Pass threshold: score >= 8, meaning any of:
  - strong title alone passes
  - moderate title + one JD positive signal passes
  - no title signal + three JD positive signals passes (unusual title case)
  - one hard-negative drops any normally-passing job below threshold
"""
import re

# ── Gate 1: Title blocklist ────────────────────────────────────────────────────

_TITLE_BLOCK_PATTERNS = [
    # Sales / GTM
    r"\baccount\s+executive\b",              # but NOT "technical account"
    r"\bsales\s+development\b",
    r"\bbusiness\s+development\s+rep",
    r"\brevenue\s+enablement\b",
    r"\bsales\s+operations\s+specialist\b",
    r"\bgovernment\s+affairs\b",
    r"\bstrategic.*sourcing.*specialist\b",
    # HR / People
    r"\btalent\s+partner\b",
    r"\btalent\s+sourcer\b",
    r"\btalent\s+acquisition\b",
    r"\btalent\s+attraction\b",
    r"\bemployer\s+brand\b",
    r"\bhr\s+business\s+partner\b",
    r"\bpeople\s+operations\s+manager\b",
    r"\bpeople\s+systems\b",
    r"\bpeople\s+projects\s+manager\b",
    r"\bearly\s+careers\b",
    r"\binternship.*specialist\b",
    # Finance / Legal
    r"\btax\s+(?:counsel|specialist|associate|manager|director)\b",
    r"\bcontract\s+manager\b",
    r"\blegal\s+counsel\b",
    r"\bfp&a\b",
    r"\bfinancial\s+planning\b",
    r"\bfinancial\s+reporting\b",
    r"\bpayroll\b",
    r"\baccounts\s+receivable\b",
    r"\bar\s+credit\b",
    r"\bcollections\s+specialist\b",
    r"\brevenue\s+accounting\b",
    # Non-technical Marketing / Comms
    r"\bdemand\s+generation\b",
    r"\bpr\s+director\b",
    r"\bpublic\s+relations\b",
    r"\bexecutive\s+communications\b",
    r"\bcorporate\s+communications\b",
    r"\bintegrated\s+marketing\b",
    r"\bgrowth\s+campaigns\b",
    r"\bproduct\s+marketing\b",
    r"\bdata\s+annotation\s+specialist\b",
    r"\bsocial\s+(?:media\s+)?(?:&\s+)?(?:video\s+)?creator\b",
    r"\bvideo\s+creator\b",
    # IT operations (support, not engineering)
    r"\bit\s+support\s+(?:specialist|advisor)\b",
    # Ghost / placeholder roles
    r"not\s+an\s+active\s+opening",
    r"building\s+talent\s+pipeline",
    r"dream\s+job\s+not\s+listed",
    r"spontaneous\s+applic",
    r"this\s+is\s+not\s+a\s+live\s+role",
    # Interns / co-ops (year in title confirms junior level)
    r"\bco-?op\b.{0,30}\b20[2-9]\d\b",
    r"\bintern\b.{0,30}\b20[2-9]\d\b",
    r"\bnew\s+graduate\b",
    r"\bresearch\s+internship\b",
]

_TITLE_BLOCK_RE = re.compile(
    "|".join(r"(?:" + p + r")" for p in _TITLE_BLOCK_PATTERNS),
    re.IGNORECASE,
)


def is_title_blocked(title: str) -> tuple[bool, str]:
    """Return (blocked, reason_string). O(1) regex check on title only."""
    m = _TITLE_BLOCK_RE.search(title or "")
    if m:
        return True, f"gate1:{m.group(0).strip().lower()[:60]}"
    return False, ""


# ── Gate 2: Weighted title + JD evidence ─────────────────────────────────────

# Strong title patterns: role function is unambiguously in a target family.
# A single match here (+10) passes the threshold alone.
_TITLE_STRONG_RE = re.compile(
    r"""
    \b(?:
        forward[\s\-]deployed[\s\-](?:engineer|AI|lead) |
        solutions?[\s\-]architect |
        solutions?[\s\-]engineer |
        solutions?[\s\-]consultant |
        implementation[\s\-](?:engineer|consultant|manager|lead|specialist) |
        customer[\s\-](?:engineer|solutions[\s\-]consultant) |
        integration[\s\-](?:engineer|specialist|consultant) |
        professional[\s\-]services[\s\-](?:engineer|consultant|manager) |
        technical[\s\-]account[\s\-](?:manager|consultant) |
        pre[\s\-]?sales[\s\-]engineer |
        applied[\s\-]AI[\s\-]engineer |
        agentic[\s\-]AI[\s\-]engineer |
        AI[\s\-]agent[\s\-]engineer |
        AI[\s\-]automation[\s\-]engineer |
        AI[\s\-]integration[\s\-]engineer |
        AI[\s\-]workflow[\s\-]engineer |
        AI[\s\-]delivery[\s\-]manager |
        AI[\s\-]enablement[\s\-]manager |
        AI[\s\-]transformation[\s\-]manager
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Moderate title patterns: plausible-target titles that need JD confirmation.
# (+5) — needs at least one JD positive signal (+3) to reach threshold (8).
_TITLE_MODERATE_RE = re.compile(
    r"""
    \b(?:
        solutions? |
        consultant |
        advisory |
        technical[\s\-](?:manager|lead|director|advisor|program) |
        program[\s\-]manager |
        engineering[\s\-]manager |
        customer[\s\-]success |
        deployment |
        implementation |
        enterprise[\s\-]technical |
        forward[\s\-]deployed |
        technical[\s\-]delivery |
        agentic |
        member[\s\-]of[\s\-]technical[\s\-]staff
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# JD positive signals (weight +3 each, capped at +15 total).
# Phrases specific enough that they're unlikely to appear in company boilerplate.
_JD_POSITIVE: list[tuple[re.Pattern, str]] = [
    # FDE / Solutions signals
    (re.compile(r"\bforward[\s-]deployed\b", re.I),                       "fwd_deployed"),
    (re.compile(r"\bimplementation\b", re.I),                              "implementation"),
    (re.compile(r"\btechnical\s+(?:discovery|requirements?)\b", re.I),    "tech_discovery"),
    (re.compile(r"\bprofessional\s+services\b", re.I),                    "prof_services"),
    (re.compile(r"\bdeployment.{0,40}customer|customer.{0,40}deployment\b", re.I), "cust_deploy"),
    (re.compile(r"\btechnical\s+troubleshoot", re.I),                     "troubleshoot"),
    (re.compile(r"\bintegrat\w+.{0,30}customer|customer.{0,30}integrat\w+", re.I), "cust_integ"),
    (re.compile(r"\bconfigure\b.{0,60}\bcustomer\b|\bcustomer\b.{0,60}\bconfigure\b", re.I), "configure"),
    # Agentic AI signals
    (re.compile(r"\bagentic\b", re.I),                                     "agentic"),
    (re.compile(r"\bmulti-?agent\b|\bagent\s+system", re.I),               "multi_agent"),
    (re.compile(r"\bLLM\b|\blarge\s+language\s+model\b", re.I),           "LLM"),
    (re.compile(r"\bRAG\b|\bretrieval.{0,20}augmented\b|\bgrounding\b", re.I), "RAG"),
    (re.compile(r"\bMCP\b|\bmodel\s+context\s+protocol\b", re.I),         "MCP"),
    (re.compile(r"\btool\s+calling\b|\bfunction\s+calling\b", re.I),      "tool_calling"),
    (re.compile(r"\bAI\s+orchestration\b|\borchestrat\w+.{0,20}AI\b", re.I), "ai_orch"),
    (re.compile(r"\bworkflow\s+automation\b.{0,60}AI|AI.{0,60}\bworkflow\s+automation\b", re.I), "wf_auto_ai"),
    # Technical Leadership signals
    (re.compile(r"\bAI\s+(?:delivery|adoption|enablement)\b", re.I),      "ai_delivery"),
    (re.compile(r"\bdigital\s+transformation\b|\bAI\s+transformation\b", re.I), "digital_tx"),
    (re.compile(r"\bstakeholder\s+alignment\b", re.I),                    "stakeholder"),
    (re.compile(r"\btechnical\s+program\b|\bcross.functional.{0,20}technical\b", re.I), "tech_prog"),
]

# JD hard-negative signals (weight −15 each).
# A single match drops the score by 15, making it nearly impossible to pass
# even with a strong title. These identify role functions outside all target families.
_JD_HARD_NEG: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:manage|own|build)\s+(?:a\s+)?(?:sales\s+)?pipeline\s+of\b", re.I), "sales_pipeline"),
    (re.compile(r"\bprospect(?:ing)?\b.{0,60}\b(?:leads?|accounts?|clients?)\b", re.I),   "prospecting"),
    (re.compile(r"\bcold\s+call\b|\bcold\s+outreach\b", re.I),                            "cold_call"),
    (re.compile(r"\bquota\b|\bsales\s+target\b|\brevenue\s+target\b", re.I),              "quota"),
    (re.compile(r"\bsales\s+cycle\b|\bfull\s+(?:sales\s+)?cycle\b", re.I),               "sales_cycle"),
    (re.compile(r"\bclose\s+(?:deals?|contracts?|opportunities)\b", re.I),                "deal_close"),
    (re.compile(r"\bsource\s+candidates?\b|\bcandidate\s+sourc", re.I),                   "candidate_src"),
    # NOTE: "talent acquisition" omitted from hard-negatives — it commonly appears in
    # company hiring-process boilerplate ("reviewed by our Talent Acquisition team") inside
    # otherwise relevant JDs. Gate 1 title blocklist catches actual TA role titles instead.
    (re.compile(r"\bfinancial\s+statements?\b|\btax\s+returns?\b|\baccounts\s+payable\b", re.I), "finance_ops"),
    (re.compile(r"\bpre-?train(?:ing)?\s+data\b|\bpost-?train(?:ing)\b", re.I),           "model_train"),
    (re.compile(r"\btrain\s+and\s+fine-?tune\b|\bRLHF\b|\bRL\s+from\s+human\s+feedback\b", re.I), "rlhf"),
    (re.compile(r"\bpress\s+releases?\b|\bmedia\s+relations\b|\bspokesperson\b", re.I),   "pr_comms"),
]

_PASS_THRESHOLD = 8
_JD_POS_CAP = 15  # maximum JD positive contribution regardless of keyword count


def relevance_score(title: str, jd_raw: str) -> tuple[int, list[str]]:
    """Compute weighted relevance score. Returns (score, [signal_names])."""
    signals: list[str] = []
    score = 0

    t = title or ""
    j = jd_raw or ""

    if _TITLE_STRONG_RE.search(t):
        score += 10
        signals.append("title_strong(+10)")
    elif _TITLE_MODERATE_RE.search(t):
        score += 5
        signals.append("title_moderate(+5)")

    jd_pos = 0
    for pattern, name in _JD_POSITIVE:
        if jd_pos >= _JD_POS_CAP:
            break
        if pattern.search(j):
            jd_pos += 3
            signals.append(f"+{name}")
    score += jd_pos

    for pattern, name in _JD_HARD_NEG:
        if pattern.search(j):
            score -= 15
            signals.append(f"-{name}")

    return score, signals


def is_relevant(title: str, jd_raw: str) -> tuple[bool, str]:
    """Return (relevant, reason). Gate 1 first, then Gate 2.

    If relevant=True:  reason is a brief signal summary (for logging only).
    If relevant=False: reason is stored in relevance_skip_reason column.
    """
    blocked, reason = is_title_blocked(title)
    if blocked:
        return False, reason

    score, signals = relevance_score(title, jd_raw)
    if score < _PASS_THRESHOLD:
        neg = [s for s in signals if s.startswith("-")]
        pos = [s for s in signals if not s.startswith("-")]
        details = f"pos=[{','.join(pos) or 'none'}] neg=[{','.join(neg) or 'none'}]"
        return False, f"gate2:score={score} {details}"

    return True, f"gate2:score={score} signals=[{','.join(signals)}]"
