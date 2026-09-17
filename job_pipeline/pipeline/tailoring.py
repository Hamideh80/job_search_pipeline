"""Resume tailoring: edit an existing category-base .docx in place, in your
own template styling, and export a PDF.

You already have three base CVs styled the way you want, one per category,
sitting in your CV folder:
  AI Transformation Consultant -> Hamideh_Ahooei_CV_AI_Transformation_Consultant.docx
  Technical Business Analyst   -> Hamideh_Ahooei_CV_Technical_Business_Analyst.docx
  Implementation / FDE         -> Hamideh_Ahooei_CV_Implementation_FDE.docx

Rather than rebuilding a document from a template (the old one-off method),
tailoring here opens the matching base file, reworks the wording of the
summary/skills/bullet paragraphs for the specific JD, and leaves every
heading, date line, and contact line untouched -- so the visual template
never has to be touched or rebuilt.

Honesty rules (from your CV-tailoring workflow notes) are enforced in the
prompt, not just requested: only reword what's already true, never invent a
technology/employer/title/metric that isn't in the base CV, keep OMID
framed as part-time volunteer leadership, and don't reframe a prototype or
personal initiative as production ML research.
"""
import json
import os
import re
import subprocess
from pathlib import Path

from anthropic import Anthropic

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")

CATEGORY_CV_FILES = {
    "AI Transformation Consultant": "Hamideh_Ahooei_CV_AI_Transformation_Consultant.docx",
    "Technical Business Analyst": "Hamideh_Ahooei_CV_Technical_Business_Analyst.docx",
    "Implementation / FDE": "Hamideh_Ahooei_CV_Implementation_FDE.docx",
}

_DATE_LINE_RE = re.compile(r"\b(19|20)\d{2}\b.*(Present|\b(19|20)\d{2}\b)")

TAILOR_PROMPT = """You are tailoring a resume for a specific job. You may ONLY reword or
re-prioritize content that already appears somewhere in the FULL CV text below --
never introduce a skill, technology, employer, title, certification, or metric
that isn't already there. If the job wants something genuinely missing from the
CV, do not paper over the gap -- list it in "flags" instead and leave the
resume as-is on that point.

Rules:
- OMID Foundation experience must always read as part-time volunteer work --
  never remove or soften "Part-time Volunteer" / "volunteer" language.
- Never reframe the Bell "Applied AI" initiative or any personal/self-directed
  project as production machine-learning research, or claim it was an
  official production deployment if the source text describes it as
  self-directed/internal.
- Prefer re-ordering and re-emphasizing existing bullet content over rewriting
  it wholesale; keep the same rough length per line.
- Return ONLY valid JSON (no prose, no markdown fences):

{{
  "edits": {{"<paragraph index>": "<new text for that paragraph>", ...}},
  "notes": "<1-3 sentences: what you emphasized and why, for the human's own reference>",
  "flags": ["<a requirement the JD wants that genuinely isn't in the CV, if any>"]
}}

Only include paragraph indices you're actually changing -- an empty edits
object is a valid answer if the base CV is already a strong fit as-is.

FULL CURRENT CV TEXT:
{full_text}

EDITABLE PARAGRAPHS (index: current text):
{editable_paragraphs}

JOB REQUIREMENTS (structured):
{jd_extracted}

JOB DESCRIPTION (raw, for context/tone):
{jd_raw}
"""


def is_editable(paragraph) -> bool:
    style = paragraph.style.name if paragraph.style else ""
    if style.startswith("Heading"):
        return False
    text = paragraph.text.strip()
    if not text:
        return False
    if "@" in text or "linkedin.com" in text.lower():
        return False  # contact line
    if len(text) < 90 and _DATE_LINE_RE.search(text):
        return False  # "City · Apr 2020 - Present" style line
    return True


def set_paragraph_text(paragraph, new_text: str) -> None:
    """Replace a paragraph's content while preserving a bold 'Label: ' lead-in
    run, if there is one (the Core Skills / Education lines use this)."""
    runs = paragraph.runs
    if not runs:
        return
    keep = 0
    if runs[0].bold and runs[0].text.strip().endswith(":"):
        keep = 1
        if len(runs) > 1 and runs[1].text.strip() == "":
            keep = 2
    for r in runs[keep:-1] if keep < len(runs) - 1 else []:
        r.text = ""
    if keep < len(runs):
        runs[-1].text = new_text
    else:
        runs[0].text = new_text


def extract_full_text(doc) -> str:
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def extract_editable_paragraphs(doc) -> list[dict]:
    return [
        {"index": i, "style": p.style.name, "text": p.text}
        for i, p in enumerate(doc.paragraphs)
        if is_editable(p)
    ]


def propose_edits(doc, jd_extracted: dict, jd_raw: str) -> dict:
    prompt = TAILOR_PROMPT.format(
        full_text=extract_full_text(doc),
        editable_paragraphs=json.dumps(extract_editable_paragraphs(doc), indent=2),
        jd_extracted=json.dumps(jd_extracted, indent=2),
        jd_raw=jd_raw[:6000],
    )
    message = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


def apply_edits(doc, edits: dict) -> None:
    for idx_str, new_text in edits.items():
        idx = int(idx_str)
        set_paragraph_text(doc.paragraphs[idx], new_text)


def sanitize(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s+", "_", text.strip())


def convert_to_pdf(docx_path: Path, out_dir: Path) -> Path | None:
    """Best-effort LibreOffice conversion. Returns None (with a printed
    warning) if soffice isn't available -- the .docx is still produced."""
    import shutil

    if not shutil.which("soffice") and not shutil.which("libreoffice"):
        print("[tailoring] soffice/libreoffice not found -- skipping PDF export")
        return None
    binary = shutil.which("soffice") or shutil.which("libreoffice")
    lo_profile = Path.home() / "work" / "lo"
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [binary, "--headless", f"-env:UserInstallation=file://{lo_profile}",
         "--convert-to", "pdf", "--outdir", str(out_dir), str(docx_path)],
        check=True, capture_output=True, timeout=60,
    )
    pdf_path = out_dir / (docx_path.stem + ".pdf")
    return pdf_path if pdf_path.exists() else None


def build_tailored_resume(*, cv_folder: Path, category: str, company: str, role: str,
                           jd_extracted: dict, jd_raw: str, output_dir: Path) -> dict:
    """Returns {"docx_path", "pdf_path", "notes", "flags"}. docx_path/pdf_path
    are written under output_dir, named Hamideh_Ahooei_<Company>_<Role>.*"""
    import docx

    base_filename = CATEGORY_CV_FILES.get(category)
    if not base_filename:
        raise ValueError(f"No base CV mapped for category {category!r}")
    base_path = cv_folder / base_filename
    if not base_path.exists():
        raise FileNotFoundError(f"Base CV not found: {base_path}")

    doc = docx.Document(str(base_path))
    result = propose_edits(doc, jd_extracted, jd_raw)
    apply_edits(doc, result.get("edits", {}))

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"Hamideh_Ahooei_{sanitize(company)}_{sanitize(role)}"
    docx_path = output_dir / f"{stem}.docx"
    doc.save(str(docx_path))

    pdf_path = convert_to_pdf(docx_path, output_dir)

    return {
        "docx_path": str(docx_path),
        "pdf_path": str(pdf_path) if pdf_path else None,
        "notes": result.get("notes", ""),
        "flags": result.get("flags", []),
    }
