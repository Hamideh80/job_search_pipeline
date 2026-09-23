"""Phase 5: fill (and optionally submit) applications on Greenhouse, Ashby,
and Lever -- the three structured ATSs this pipeline auto-acts on, per your
call to keep LinkedIn/Indeed manual.

SAFETY DESIGN -- read this before turning on AUTO_SUBMIT_CONFIRMED:

This defaults to DRY RUN. Every job it processes gets the form filled out
(contact info, resume upload, custom question answers) and a screenshot
taken -- but the final Submit click only happens if the AUTO_SUBMIT_CONFIRMED
environment variable is exactly "true". Until then, jobs land at
'ready_to_submit' with a screenshot for you to look at. This is deliberate:
a brand-new automation that fills out real job applications on your behalf
deserves a human looking at the filled form at least once before anything
goes out, and the per-ATS selectors below were only live-verified against
one real Greenhouse posting (see README) -- Ashby and Lever are best-effort
based on their documented form structure.

Recommended way to turn this on: leave AUTO_SUBMIT_CONFIRMED unset, run a
batch, look at a few application_screenshot files, and only flip it on once
you trust the fill quality. Even then, consider it a "trust but verify"
switch, not a "never look again" one.

Requires: `pip install playwright` + `playwright install chromium`, and
normal internet access to boards.greenhouse.io / jobs.ashbyhq.com /
jobs.lever.co from wherever this runs. (It will NOT work from inside a
restricted sandbox whose network is limited to a small allowlist -- run it
from your own machine, a VPS, or a CI runner with normal internet, or
interactively in a Cowork session connected to your computer.)
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .ai import get_ai_client

AUTO_SUBMIT_CONFIRMED = os.environ.get("AUTO_SUBMIT_CONFIRMED", "false").strip().lower() == "true"
APPLY_HEADLESS = os.environ.get("APPLY_HEADLESS", "true").strip().lower() != "false"

APPLICANT = {
    "first_name": os.environ.get("APPLICANT_FIRST_NAME", ""),
    "last_name": os.environ.get("APPLICANT_LAST_NAME", ""),
    "email": os.environ.get("APPLICANT_EMAIL", ""),
    "phone": os.environ.get("APPLICANT_PHONE", ""),
}


def load_applicant_notes(notes_path: Path) -> str:
    """Reads config/applicant_notes.md (gitignored, personal) -- free-text
    context fed into custom-answer drafting (work authorization, notice
    period, salary expectation, etc). See applicant_notes.example.md.
    Returns "" if the file hasn't been created yet."""
    return notes_path.read_text() if notes_path.exists() else ""

CUSTOM_ANSWER_PROMPT = """Answer these application questions as the candidate, using ONLY facts
from the CV text below -- never invent a technology, employer, title, or
metric that isn't there. For a yes/no eligibility question (work
authorization, willingness to relocate, sponsorship needs), answer only if
the CV or the notes below clearly settle it; otherwise return null so the
human fills it in themselves. Keep OMID Foundation framed as part-time
volunteer work if it comes up.

Return ONLY valid JSON (no prose, no markdown fences):
{{"answers": {{"<question text>": "<answer, or null if you're not confident>", ...}}}}

CANDIDATE CV:
{cv_text}

APPLICANT NOTES (work authorization etc., may be empty):
{applicant_notes}

JOB REQUIREMENTS (structured):
{jd_extracted}

QUESTIONS (exact text from the form):
{questions}
"""


def draft_custom_answers(questions: list[str], cv_text: str, jd_extracted: dict,
                          applicant_notes: str = "") -> dict:
    if not questions:
        return {}
    prompt = CUSTOM_ANSWER_PROMPT.format(
        cv_text=cv_text,
        applicant_notes=applicant_notes or "(none provided)",
        jd_extracted=json.dumps(jd_extracted, indent=2),
        questions="\n".join(f"- {q}" for q in questions),
    )
    raw = get_ai_client().complete(prompt, max_tokens=1536, purpose="apply_custom_answers")
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw).get("answers", {})


def _screenshot_path(output_dir: Path, company: str, role: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w\s-]", "", f"{company}_{role}")
    stem = re.sub(r"\s+", "_", stem.strip())
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return output_dir / f"{stem}_{ts}.png"


def apply_greenhouse(*, job_link: str, resume_pdf_path: str, cv_text: str,
                      jd_extracted: dict, output_dir: Path,
                      applicant_notes: str = "") -> dict:
    """Live-verified structure (checked against a real Anthropic Greenhouse
    posting): #first_name, #last_name, #email, #phone, #resume (file input),
    and custom questions as inputs/selects/textareas whose id starts with
    "question_", labeled via the nearest .field label."""
    from playwright.sync_api import sync_playwright

    result = {"screenshot_path": None, "custom_answers": {}, "flags": [], "submitted": False}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=APPLY_HEADLESS)
        page = browser.new_page()
        page.goto(job_link, timeout=30000, wait_until="domcontentloaded")

        apply_button = page.locator("button:has-text('Apply')").first
        if apply_button.count():
            apply_button.click()
            page.wait_for_timeout(1000)

        for field, value in [("#first_name", APPLICANT["first_name"]),
                              ("#last_name", APPLICANT["last_name"]),
                              ("#email", APPLICANT["email"]),
                              ("#phone", APPLICANT["phone"])]:
            locator = page.locator(field)
            if value and locator.count():
                locator.fill(value)

        resume_input = page.locator("#resume")
        if resume_input.count() and resume_pdf_path:
            resume_input.set_input_files(resume_pdf_path)

        questions, question_locators = [], []
        for el in page.locator("[id^='question_']").all():
            label_el = el.locator(
                "xpath=ancestor::*[contains(@class,'field')][1]//label"
            )
            label = label_el.first.inner_text().strip() if label_el.count() else el.get_attribute("id")
            questions.append(label)
            question_locators.append((label, el))

        answers = draft_custom_answers(questions, cv_text, jd_extracted, applicant_notes)
        result["custom_answers"] = answers

        for label, el in question_locators:
            answer = answers.get(label)
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            if answer is None:
                result["flags"].append(f"Needs your input: {label!r}")
                continue
            try:
                if tag == "select":
                    options = el.evaluate(
                        "e => Array.from(e.options).map(o => o.text.trim()).filter(o => o)"
                    )
                    match = (
                        next((o for o in options if o == answer), None)
                        or next((o for o in options if o.lower() == answer.lower()), None)
                        or next((o for o in options if answer.lower() in o.lower()), None)
                    )
                    if match:
                        el.select_option(label=match)
                    else:
                        result["flags"].append(
                            f"No option match for {label!r}: tried {answer!r}, "
                            f"available: {options}"
                        )
                else:
                    # React typeahead input: type to filter, then click matching dropdown option
                    el.click()
                    el.fill("")
                    el.type(str(answer), delay=80)
                    page.wait_for_timeout(600)
                    opt = page.locator("div[role='option'], .select__option").filter(
                        has_text=str(answer)
                    ).first
                    if opt.count() and opt.is_visible():
                        opt.click()
                        page.wait_for_timeout(300)
                    # else: plain text field, value already typed — leave as-is
            except Exception as exc:  # noqa: BLE001 -- one bad field shouldn't kill the run
                result["flags"].append(f"Couldn't fill {label!r} ({tag}): {exc}")

        shot_path = _screenshot_path(output_dir, jd_extracted.get("_company", "company"),
                                      jd_extracted.get("_role", "role"))
        page.screenshot(path=str(shot_path), full_page=True)
        result["screenshot_path"] = str(shot_path)

        if AUTO_SUBMIT_CONFIRMED and not result["flags"]:
            submit_button = page.locator("button:has-text('Submit application')").first
            if submit_button.count():
                submit_button.click()
                page.wait_for_timeout(2000)
                result["submitted"] = True
        elif AUTO_SUBMIT_CONFIRMED and result["flags"]:
            result["flags"].append("Submit skipped: unanswered fields needed your input first.")

        browser.close()

    return result


def apply_lever(*, job_link: str, resume_pdf_path: str, cv_text: str,
                 jd_extracted: dict, output_dir: Path, applicant_notes: str = "") -> dict:
    """Best-effort, NOT live-verified this session (no open Lever posting was
    available to check against -- see README). Based on Lever's documented
    hosted-form field names: name="name", name="email", name="phone",
    name="resume" (file), and custom questions under name="cards[...]" or
    similar, read generically via every visible input/textarea/select in
    the <form>. Sanity-check the first screenshot carefully before trusting
    this one."""
    from playwright.sync_api import sync_playwright

    result = {"screenshot_path": None, "custom_answers": {}, "flags": [], "submitted": False}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=APPLY_HEADLESS)
        page = browser.new_page()
        page.goto(job_link.rstrip("/") + "/apply", timeout=30000, wait_until="domcontentloaded")

        for field, value in [("input[name='name']", f"{APPLICANT['first_name']} {APPLICANT['last_name']}".strip()),
                              ("input[name='email']", APPLICANT["email"]),
                              ("input[name='phone']", APPLICANT["phone"])]:
            locator = page.locator(field)
            if value and locator.count():
                locator.fill(value)

        resume_input = page.locator("input[name='resume']")
        if resume_input.count() and resume_pdf_path:
            resume_input.set_input_files(resume_pdf_path)

        questions, question_locators = [], []
        for el in page.locator("form textarea, form input[type=text]:not([name=name]):not([name=email]):not([name=phone])").all():
            label = el.evaluate(
                "e => e.closest('.application-question')?.querySelector('.application-label')?.innerText "
                "|| e.getAttribute('placeholder') || e.getAttribute('name') || ''"
            ).strip()
            if not label:
                continue
            questions.append(label)
            question_locators.append((label, el))

        answers = draft_custom_answers(questions, cv_text, jd_extracted, applicant_notes)
        result["custom_answers"] = answers
        for label, el in question_locators:
            answer = answers.get(label)
            if answer is None:
                result["flags"].append(f"Needs your input: {label!r}")
                continue
            try:
                el.fill(str(answer))
            except Exception as exc:  # noqa: BLE001
                result["flags"].append(f"Couldn't fill {label!r}: {exc}")

        shot_path = _screenshot_path(output_dir, jd_extracted.get("_company", "company"),
                                      jd_extracted.get("_role", "role"))
        page.screenshot(path=str(shot_path), full_page=True)
        result["screenshot_path"] = str(shot_path)

        if AUTO_SUBMIT_CONFIRMED and not result["flags"]:
            submit_button = page.locator("button[type=submit]").first
            if submit_button.count():
                submit_button.click()
                page.wait_for_timeout(2000)
                result["submitted"] = True
        elif AUTO_SUBMIT_CONFIRMED and result["flags"]:
            result["flags"].append("Submit skipped: unanswered fields needed your input first.")

        browser.close()

    return result


def apply_ashby(*, job_link: str, resume_pdf_path: str, cv_text: str,
                 jd_extracted: dict, output_dir: Path, applicant_notes: str = "") -> dict:
    """Best-effort, NOT live-verified this session -- see README. Ashby's
    hosted application forms are a client-rendered React app with less
    predictable field naming than Greenhouse/Lever, so this fills by
    matching visible field labels generically rather than fixed selectors.
    Treat this as the least reliable of the three; check the screenshot
    especially carefully."""
    from playwright.sync_api import sync_playwright

    result = {"screenshot_path": None, "custom_answers": {}, "flags": [], "submitted": False}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=APPLY_HEADLESS)
        page = browser.new_page()
        page.goto(job_link, timeout=30000, wait_until="networkidle")

        apply_button = page.locator("button:has-text('Apply')").first
        if apply_button.count():
            apply_button.click()
            page.wait_for_timeout(1000)

        field_map = {
            "name": f"{APPLICANT['first_name']} {APPLICANT['last_name']}".strip(),
            "email": APPLICANT["email"],
            "phone": APPLICANT["phone"],
        }
        for key, value in field_map.items():
            locator = page.get_by_label(re.compile(key, re.I)).first
            if value and locator.count():
                try:
                    locator.fill(value)
                except Exception:  # noqa: BLE001 -- best-effort
                    result["flags"].append(f"Couldn't auto-fill {key}")

        resume_input = page.locator("input[type=file]").first
        if resume_input.count() and resume_pdf_path:
            resume_input.set_input_files(resume_pdf_path)

        questions, question_locators = [], []
        for el in page.locator("textarea, select").all():
            label = el.evaluate(
                "e => e.closest('label')?.innerText || e.getAttribute('aria-label') "
                "|| e.getAttribute('placeholder') || ''"
            ).strip()
            if not label:
                continue
            questions.append(label)
            question_locators.append((label, el))

        answers = draft_custom_answers(questions, cv_text, jd_extracted, applicant_notes)
        result["custom_answers"] = answers
        for label, el in question_locators:
            answer = answers.get(label)
            if answer is None:
                result["flags"].append(f"Needs your input: {label!r}")
                continue
            try:
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                if tag == "select":
                    el.select_option(label=str(answer))
                else:
                    el.fill(str(answer))
            except Exception as exc:  # noqa: BLE001
                result["flags"].append(f"Couldn't fill {label!r}: {exc}")

        shot_path = _screenshot_path(output_dir, jd_extracted.get("_company", "company"),
                                      jd_extracted.get("_role", "role"))
        page.screenshot(path=str(shot_path), full_page=True)
        result["screenshot_path"] = str(shot_path)

        if AUTO_SUBMIT_CONFIRMED and not result["flags"]:
            submit_button = page.get_by_role("button", name=re.compile("submit", re.I)).first
            if submit_button.count():
                submit_button.click()
                page.wait_for_timeout(2000)
                result["submitted"] = True
        elif AUTO_SUBMIT_CONFIRMED and result["flags"]:
            result["flags"].append("Submit skipped: unanswered fields needed your input first.")

        browser.close()

    return result


_HANDLERS = {
    "Greenhouse": apply_greenhouse,
    "Lever": apply_lever,
    "Ashby": apply_ashby,
}


def apply_to_job(*, source: str, job_link: str, resume_pdf_path: str, cv_text: str,
                  jd_extracted: dict, output_dir: Path, company: str, role: str,
                  applicant_notes: str = "") -> dict:
    handler = _HANDLERS.get(source)
    if not handler:
        raise ValueError(f"No apply handler for source {source!r} "
                          "(only Greenhouse/Ashby/Lever are auto-acted on)")
    jd_extracted = dict(jd_extracted, _company=company, _role=role)
    return handler(
        job_link=job_link, resume_pdf_path=resume_pdf_path, cv_text=cv_text,
        jd_extracted=jd_extracted, output_dir=output_dir, applicant_notes=applicant_notes,
    )
