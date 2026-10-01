"""Resume tailoring: edit the combined Master CV .docx in place and export a PDF.

Tailoring only runs AFTER the human approves the job in Notion.
The single base file is:

  Master CVs/Hamideh_Ahooei_Master_CV_Combined.docx

Tailoring opens that file, reworks summary/skills/bullet paragraphs for the
specific JD, and leaves every heading, date line, and contact line untouched.

Honesty rules are enforced in the prompt: only reword what's already true,
never invent a technology/employer/title/metric that isn't in the Master CV,
keep OMID framed as part-time volunteer leadership, and don't reframe a
prototype or self-directed initiative as production ML research.
"""
import re
import subprocess
from pathlib import Path

COMBINED_CV_FILE = "Master CVs/Hamideh_Ahooei_Master_CV_Combined.docx"

_DATE_LINE_RE = re.compile(r"\b(19|20)\d{2}\b.*(Present|\b(19|20)\d{2}\b)")


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
    label = ""
    if runs[0].bold and runs[0].text.strip().endswith(":"):
        keep = 1
        label = runs[0].text.strip()
        if len(runs) > 1 and runs[1].text.strip() == "":
            keep = 2
    # Strip label prefix if the AI echoed it back in new_text.
    if label:
        for prefix in (label + " ", label):
            if new_text.startswith(prefix):
                new_text = new_text[len(prefix):]
                break
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


def build_tailored_resume(*, cv_folder: Path, company: str, role: str,
                           output_dir: Path,
                           edits: dict | None = None,
                           notes: str = "",
                           flags: list | None = None) -> dict:
    """Apply pre-computed edits (from the tailor skill) to the base CV and save.

    Returns {"docx_path", "pdf_path", "notes", "flags"}.
    Called by tailor/commit.py after the skill has produced the edits JSON.
    """
    import docx

    base_path = cv_folder / COMBINED_CV_FILE
    if not base_path.exists():
        raise FileNotFoundError(f"Base CV not found: {base_path}")

    doc = docx.Document(str(base_path))
    if edits:
        apply_edits(doc, edits)

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"Hamideh_Ahooei_{sanitize(company)}_{sanitize(role)}"
    docx_path = output_dir / f"{stem}.docx"
    doc.save(str(docx_path))

    pdf_path = convert_to_pdf(docx_path, output_dir)

    return {
        "docx_path": str(docx_path),
        "pdf_path": str(pdf_path) if pdf_path else None,
        "notes": notes,
        "flags": flags or [],
    }
