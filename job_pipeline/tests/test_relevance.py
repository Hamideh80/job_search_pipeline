"""Tests for the Step-7 relevance filter (pipeline/relevance.py).

Verifies:
1.  Gate 1 title blocklist rejects known-irrelevant role functions.
2.  Gate 1 does NOT block ambiguous or target-family titles.
3.  Gate 2: strong title alone passes (score >= threshold without JD signals).
4.  Gate 2: neutral title + JD positive signals passes.
5.  Gate 2: hard-negative JD signal overrides positive title.
6.  Gate 2: no signals fails (below threshold).
7.  LinkedIn unknown title passes only if JD has enough positive signals.
8.  Score threshold boundary: score=7 fails, score=8 passes.
9.  is_relevant() combines both gates correctly.
10. skipped_irrelevant is a registered terminal state in state.py.
"""
import pytest
from pipeline.relevance import (
    is_title_blocked,
    is_relevant,
    relevance_score,
    _PASS_THRESHOLD,
)


# ── 1. Gate 1: titles that must be blocked ───────────────────────────────────

@pytest.mark.parametrize("title", [
    "Account Executive",
    "Senior Account Executive",
    "Senior Account Executive - US Public Sector",
    "Sales Development Representative",
    "Business Development Representative",
    "Business Development Rep, APAC",
    "Revenue Enablement Program Manager",
    "Sales Operations Specialist",
    "Government Affairs Lead",
    "Talent Partner, GTM",
    "Talent Sourcer, G&A",
    "Talent Acquisition Specialist",
    "Talent Attraction and Employer Brand Specialist",
    "Employer Brand Manager",
    "HR Business Partner",
    "People Operations Manager",
    "People Systems Manager",
    "Early Careers & Interns Specialist",
    "Tax Counsel, Tax Planning",
    "Tax Specialist",
    "Tax Associate",
    "Contract Manager",
    "Legal Counsel",
    "Financial Planning & Analysis Manager",
    "Financial Reporting Manager",
    "FP&A Analyst",
    "Payroll Specialist",
    "AR Credit & Collections Specialist",
    "Revenue Accounting Manager",
    "Senior Director, Integrated Marketing",
    "Demand Generation Manager",
    "PR Director, APAC",
    "Executive Communications Manager",
    "Corporate Communications Director",
    "Growth Campaigns Manager",
    "Product Marketing Manager",
    "Data Annotation Specialist, Data Science",
    "Social & Video Creator",
    "Video Creator",
    "IT Support Specialist",
    "IT Support Advisor",
    # Ghost / placeholder
    "Senior Machine Learning Engineer - Not an Active Opening, Building Talent Pipeline",
    "Dream Job Not Listed? Click Here!",
    "Spontaneous Application",
    # Co-ops / interns
    "Software Developer Co-op (January to August 2027)",
    "Software Developer Intern Winter 2026",
    "New Graduate Software Engineer",
    "Research Internship",
])
def test_gate1_blocks(title):
    blocked, reason = is_title_blocked(title)
    assert blocked, f"Expected '{title}' to be blocked by Gate 1"
    assert reason.startswith("gate1:"), f"Unexpected reason format: {reason!r}"


# ── 2. Gate 1: titles that must NOT be blocked ───────────────────────────────

@pytest.mark.parametrize("title", [
    # Clearly target
    "Forward Deployed Engineer",
    "Forward Deployed Engineer, Agentic Platform",
    "Senior Forward Deployed AI Lead",
    "Solutions Architect",
    "Solutions Architect - Public Sector",
    "Senior Solutions Engineer",
    "Senior Solution Consultant - Implementation",
    "Implementation Consultant",
    "Customer Solutions Consultant II",
    "Technical Account Manager",
    "Technical Account Consultant",
    "Applied AI Engineer, Agents & Automations",
    "Agentic AI Engineer",
    "AI Integration Engineer",
    "AI Delivery Manager",
    "Engineering Manager, FDE Agentic Platform",
    # Ambiguous (should reach Gate 2)
    "Member of Technical Staff, Agent Code",
    "Program Manager",
    "Engineering Manager",
    "Customer Success Manager",
    "Enterprise Technical Lead",
    "Senior Software Engineer, AI",
    "Sr. Advisory Services Consultant",
    "Senior Professional Services Consultant",
    # Should not confuse "technical" in title with HR/sales blocklist
    "Technical Recruiter",   # has "recruiter" but not in exact blocklist pattern — let Gate 2 handle
    "Business Intelligence Consultant",
    "Strategic Account Director",  # "account" alone is not blocked
    # Moderate-signal titles that need JD support but must not be blocked at Gate 1
    "Senior II Software Developer - Agentic System",  # agentic in title → moderate
    "Member of Technical Staff, Agent Code",          # MTS → moderate
    "Member of Technical Staff, Agentic Environments",
])
def test_gate1_does_not_block(title):
    blocked, _ = is_title_blocked(title)
    assert not blocked, f"Gate 1 incorrectly blocked: '{title}'"


# ── 3. Gate 2: strong title passes without JD signals ────────────────────────

@pytest.mark.parametrize("title", [
    "Forward Deployed Engineer, Agentic Platform",
    "Solutions Architect",
    "Senior Solutions Engineer",
    "Implementation Consultant",
    "Customer Solutions Consultant",
    "Technical Account Manager",
    "Applied AI Engineer",
    "AI Delivery Manager",
    "AI Enablement Manager",
    "Professional Services Consultant",
    "Solutions Consultant - Implementation",
])
def test_strong_title_passes_without_jd(title):
    ok, reason = is_relevant(title, "Some generic job description with no signals.")
    assert ok, f"Strong title '{title}' should pass with empty JD. Got: {reason}"


# ── 4. Gate 2: neutral title + JD signals passes ─────────────────────────────

def test_neutral_title_passes_with_jd_signals():
    title = "Member of Technical Staff, Agent Code"
    jd = (
        "We're building autonomous agents and multi-agent systems. "
        "Deep knowledge of LLMs required. RAG and tool calling experience preferred. "
        "You'll work on agentic workflows."
    )
    ok, reason = is_relevant(title, jd)
    assert ok, f"Neutral title with strong JD should pass. Got: {reason}"


def test_program_manager_passes_with_ai_delivery_jd():
    title = "Program Manager"
    jd = (
        "Manage AI delivery programs across engineering teams. "
        "Drive AI adoption across the organization. "
        "Lead cross-functional technical delivery with stakeholder alignment."
    )
    ok, reason = is_relevant(title, jd)
    assert ok, f"'Program Manager' with AI delivery JD should pass. Got: {reason}"


def test_consultant_passes_with_implementation_jd():
    title = "Consultant"
    jd = (
        "Lead implementation projects with enterprise customers. "
        "Drive technical discovery and requirements gathering. "
        "Configure and deploy our platform, provide professional services delivery."
    )
    ok, reason = is_relevant(title, jd)
    assert ok, f"'Consultant' with implementation JD should pass. Got: {reason}"


# ── 5. Gate 2: hard-negative JD signal overrides positive title ──────────────

def test_hard_negative_overrides_strong_title():
    title = "Solutions Architect"
    jd = (
        "As a Solutions Architect you will own the full sales cycle, "
        "manage a pipeline of leads, and close deals with enterprise customers. "
        "You will carry a quota of $1M ARR."
    )
    ok, reason = is_relevant(title, jd)
    assert not ok, (
        f"Sales SA role should be rejected despite strong title. Got: {reason}"
    )


def test_hard_negative_sales_cycle_blocks():
    title = "Senior Account Executive"  # already blocked by Gate 1, but let's test JD signal too
    jd = "You will manage the full sales cycle and close deals with Fortune 500 companies."
    ok, _ = is_relevant(title, jd)
    assert not ok, "AE role should be rejected"


def test_hard_negative_rlhf_blocks_mts():
    title = "Member of Technical Staff, Post-Training"
    jd = (
        "Design and implement novel model training techniques. "
        "Run experiments on pre-training data pipelines and post-training fine-tuning. "
        "RLHF and reward model development."
    )
    ok, reason = is_relevant(title, jd)
    assert not ok, f"ML research role should be rejected. Got: {reason}"


# ── 6. Gate 2: no signals fails ──────────────────────────────────────────────

def test_no_signals_fails():
    title = "Manager"
    jd = "Manage a team and deliver results. Collaborate with stakeholders. Drive growth."
    ok, reason = is_relevant(title, jd)
    assert not ok, f"Generic manager with no target signals should fail. Got: {reason}"


# ── 7. LinkedIn unknown title ─────────────────────────────────────────────────

def test_linkedin_unknown_title_passes_with_agentic_jd():
    title = "Unknown (see JD)"
    jd = (
        "We are building agentic AI systems for enterprise customers. "
        "Experience with LLMs, RAG, and tool calling required. "
        "You'll implement and deploy AI workflows."
    )
    ok, reason = is_relevant(title, jd)
    assert ok, f"Unknown title with strong agentic JD should pass. Got: {reason}"


def test_linkedin_unknown_title_fails_with_sales_jd():
    title = "Unknown (see JD)"
    jd = (
        "Manage a pipeline of leads and prospect new accounts. "
        "Hit quarterly quota of $500k ARR. Cold outreach to target accounts."
    )
    ok, reason = is_relevant(title, jd)
    assert not ok, f"Unknown title with sales JD should fail. Got: {reason}"


def test_linkedin_unknown_title_fails_with_no_signals():
    title = "Unknown (see JD)"
    jd = "Join our team and help us grow. Great benefits. Remote-friendly environment."
    ok, reason = is_relevant(title, jd)
    assert not ok, f"Unknown title with generic JD should fail. Got: {reason}"


# ── Regression: non-standard external title where JD body says "Solution Architect" ──

def test_business_architect_ps_passes_via_jd_sol_arch():
    """ID 84: Coveo posts 'Business Architect - PS' externally but the JD opens with
    'As a Solution Architect on our Professional Services team'.  The jd_sol_arch
    signal (+3) plus implementation (+3) and prof_services (+3) must reach threshold."""
    title = "Business Architect - PS"
    jd = (
        "As a Solution Architect on our Professional Services team, you will be the "
        "trusted technical advisor helping customers unlock the full power of our platform. "
        "Lead solution design and implementation of our AI platform across customer digital "
        "ecosystems. Deliver professional services projects from discovery to go-live."
    )
    ok, reason = is_relevant(title, jd)
    assert ok, f"'Business Architect - PS' with SA JD body should pass: {reason}"


# ── Talent-acquisition boilerplate does NOT block legitimate roles ────────────

def test_talent_acquisition_boilerplate_does_not_block_solutions_role():
    """'Talent Acquisition team' appearing in hiring-process boilerplate must not
    block a Solutions Architect / Solutions Engineer title."""
    title = "Solutions Architect"
    jd = (
        "Help customers deploy and integrate our platform. Lead technical discovery "
        "and requirements gathering with enterprise clients. Your application will be "
        "personally reviewed by a member of our Talent Acquisition team — yes, a real "
        "person looks at every resume."
    )
    ok, reason = is_relevant(title, jd)
    assert ok, f"TA boilerplate should not block Solutions Architect: {reason}"


# ── Agentic and MTS as moderate title signals ─────────────────────────────────

def test_agentic_in_title_moderate():
    """'Agentic' anywhere in the title should register as a moderate signal."""
    title = "Senior II Software Developer - Agentic System"
    jd = "Build agentic AI workflows using LLMs and tool-calling frameworks."
    ok, reason = is_relevant(title, jd)
    assert ok, f"'Agentic' title + JD signals should pass: {reason}"


def test_mts_agent_code_passes_with_jd_signals():
    """Member of Technical Staff with agent/LLM JD signals should pass."""
    title = "Member of Technical Staff, Agent Code"
    jd = "Build multi-agent autonomous systems. Deep expertise in LLMs and code generation."
    ok, reason = is_relevant(title, jd)
    assert ok, f"MTS Agent Code should pass with JD signals: {reason}"


def test_mts_without_jd_signals_fails():
    """Member of Technical Staff with no relevant JD signals should fail."""
    title = "Member of Technical Staff, Pre-Training Data"
    jd = "Curate and process pre-training data for our foundation models. Design data pipelines."
    ok, reason = is_relevant(title, jd)
    assert not ok, f"MTS Pre-Training should fail without positive signals: {reason}"


# ── 8. Threshold boundary ────────────────────────────────────────────────────

def test_threshold_value():
    assert _PASS_THRESHOLD == 8


def test_score_below_threshold_fails():
    # moderate title (+5) + no JD signals = score 5 < 8
    title = "Consultant"
    jd = "Join our growing team. Great benefits."
    score, signals = relevance_score(title, jd)
    assert score < _PASS_THRESHOLD, f"Expected score < 8, got {score} signals={signals}"


def test_score_at_threshold_passes():
    # moderate title (+5) + one JD positive signal (+3) = score 8
    title = "Consultant"
    jd = "Lead implementation projects for enterprise customers with technical delivery."
    score, signals = relevance_score(title, jd)
    assert score >= _PASS_THRESHOLD, f"Expected score >= 8, got {score} signals={signals}"


# ── 9. is_relevant() combines gates correctly ─────────────────────────────────

def test_is_relevant_gate1_short_circuits():
    """Gate 1 match must prevent Gate 2 from running (reason prefix is gate1:)."""
    ok, reason = is_relevant("Account Executive", "LLMs agentic AI agents tool calling")
    assert not ok
    assert reason.startswith("gate1:"), f"Expected gate1 reason, got: {reason!r}"


def test_is_relevant_gate2_reason_prefix():
    """Jobs filtered by Gate 2 carry a gate2: reason prefix."""
    ok, reason = is_relevant("Manager", "Join our growing team.")
    assert not ok
    assert reason.startswith("gate2:"), f"Expected gate2 reason, got: {reason!r}"


def test_is_relevant_pass_returns_gate2_score():
    """Passing jobs carry their Gate 2 score in the reason string."""
    ok, reason = is_relevant("Solutions Architect", "Configure and deploy for customers.")
    assert ok
    assert "gate2:score=" in reason, f"Expected score in reason: {reason!r}"


# ── 10. skipped_irrelevant is a terminal state in state.py ───────────────────

def test_skipped_irrelevant_in_state_machine():
    from pipeline.state import TRANSITIONS, TERMINAL, JobStatus
    assert "skipped_irrelevant" in TRANSITIONS
    assert "skipped_irrelevant" in TERMINAL
    assert "skipped_irrelevant" in TRANSITIONS.get("discovered", set())
    # Enum member exists
    assert JobStatus.SKIPPED_IRRELEVANT == "skipped_irrelevant"
