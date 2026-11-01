"""Validated, deterministic PDF export for a final tailored resume."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping
from urllib.parse import urlsplit

from .base_resume import (
    BaseResumeArtifact,
    BaseResumeEntry,
    BaseResumeItemType,
    BaseResumeTemporalState,
)
from .resume_fact_check import (
    FinalResumeFactCheckArtifact,
    ResumeSemanticReviewStatus,
    prepare_final_resume_bullet_checks,
)
from .resume_material import ResumeSectionType
from .resume_profile_supplement import ContactFieldType
from .role_catalog import Phase2ValidationError


@dataclass(frozen=True, eq=True)
class ResumePDFExportResult:
    """Non-persistent metadata returned after a successful PDF export."""

    path: Path
    sha256: str
    byte_length: int
    page_count: int


_MONTHS = (
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
_ACRONYMS = {
    "ai": "AI",
    "api": "API",
    "aws": "AWS",
    "css": "CSS",
    "gcp": "GCP",
    "git": "Git",
    "github": "GitHub",
    "html": "HTML",
    "llm": "LLM",
    "ml": "ML",
    "nlp": "NLP",
    "sql": "SQL",
    "ui": "UI",
    "ux": "UX",
}


def skill_group_display_name(value: str) -> str:
    """Map an internal category label to a human-readable heading only."""

    parts = [item for item in re.split(r"[_\s]+", value.strip()) if item]
    if not parts:
        raise Phase2ValidationError("Skill group must be non-empty")
    return " ".join(
        _ACRONYMS.get(item.lower(), item[:1].upper() + item[1:])
        for item in parts
    )


def _safe_text(value: str) -> str:
    if "\x00" in value:
        raise Phase2ValidationError("Resume PDF text cannot contain NUL")
    return html.escape(value, quote=True).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")


def _safe_https_url(value: str) -> str:
    if any(character.isspace() for character in value):
        raise Phase2ValidationError("Resume PDF URL must not contain whitespace")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise Phase2ValidationError("Resume PDF links must use an explicit safe HTTPS URL")
    return value


def _safe_email(value: str) -> str:
    if not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", value):
        raise Phase2ValidationError("Resume PDF email is invalid")
    return value


def _date(value: str, *, expected: bool = False) -> str:
    match = re.fullmatch(r"(\d{4})-(\d{2})(?:-\d{2})?", value)
    if not match:
        return value
    year, month = int(match.group(1)), int(match.group(2))
    rendered = f"{_MONTHS[month]} {year}" if 1 <= month <= 12 else str(year)
    return f"Expected {rendered}" if expected else rendered


def _entry_dates(entry: BaseResumeEntry, fields: Mapping[str, str]) -> str:
    values: list[str] = []
    if fields.get("start_date"):
        values.append(_date(fields["start_date"]))
    if entry.temporal_state == BaseResumeTemporalState.ONGOING:
        values.append("Present")
    elif fields.get("end_date"):
        values.append(_date(fields["end_date"]))
    elif fields.get("expected_graduation_date"):
        values.append(_date(fields["expected_graduation_date"], expected=True))
    return " - ".join(values)


def _skill_text(entry: BaseResumeEntry) -> str:
    fields = dict(entry.fields)
    value = fields.get("text") or fields.get("skill_name") or fields.get("name")
    if not value:
        raise Phase2ValidationError("Resume PDF Skill entry lacks display text")
    proficiency = fields.get("self_reported_proficiency")
    return value if not proficiency else f"{value} ({proficiency})"


def _load_reportlab():
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.pdfgen import canvas
        from reportlab.platypus import (
            CondPageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as error:
        raise Phase2ValidationError(
            'PDF export requires ReportLab. Install Aarvia with `python -m pip install .`.'
        ) from error
    return {
        "colors": colors,
        "TA_CENTER": TA_CENTER,
        "TA_RIGHT": TA_RIGHT,
        "letter": letter,
        "ParagraphStyle": ParagraphStyle,
        "inch": inch,
        "canvas": canvas,
        "CondPageBreak": CondPageBreak,
        "Paragraph": Paragraph,
        "SimpleDocTemplate": SimpleDocTemplate,
        "Spacer": Spacer,
        "Table": Table,
        "TableStyle": TableStyle,
    }


def _story(
    resume: BaseResumeArtifact,
    bullet_text_overrides: Mapping[str, str],
    reportlab: Mapping[str, Any],
) -> list[Any]:
    Paragraph = reportlab["Paragraph"]
    ParagraphStyle = reportlab["ParagraphStyle"]
    Spacer = reportlab["Spacer"]
    Table = reportlab["Table"]
    TableStyle = reportlab["TableStyle"]
    colors = reportlab["colors"]
    inch = reportlab["inch"]
    TA_CENTER = reportlab["TA_CENTER"]
    TA_RIGHT = reportlab["TA_RIGHT"]
    CondPageBreak = reportlab["CondPageBreak"]

    normal = ParagraphStyle(
        "ResumeBody", fontName="Helvetica", fontSize=10, leading=12.5,
        textColor=colors.HexColor("#111827"), spaceAfter=2,
        allowWidows=0, allowOrphans=0,
    )
    small = ParagraphStyle(
        "ResumeSmall", parent=normal, fontSize=9.5, leading=11.5,
    )
    name_style = ParagraphStyle(
        "ResumeName", parent=normal, fontName="Helvetica-Bold", fontSize=18,
        leading=21, alignment=TA_CENTER, spaceAfter=4,
    )
    contact_style = ParagraphStyle(
        "ResumeContact", parent=small, alignment=TA_CENTER, spaceAfter=5,
    )
    section_style = ParagraphStyle(
        "ResumeSection", parent=normal, fontName="Helvetica-Bold", fontSize=11.5,
        leading=14, textColor=colors.HexColor("#0F3D63"), spaceBefore=6,
        spaceAfter=3,
    )
    heading_style = ParagraphStyle(
        "ResumeEntryHeading", parent=normal, fontName="Helvetica-Bold",
        keepWithNext=False,
    )
    right_style = ParagraphStyle(
        "ResumeEntryDate", parent=small, alignment=TA_RIGHT,
    )
    detail_style = ParagraphStyle(
        "ResumeDetail", parent=small, textColor=colors.HexColor("#374151"),
    )
    bullet_style = ParagraphStyle(
        "ResumeBullet", parent=normal, leftIndent=12, firstLineIndent=-7,
        bulletIndent=0, spaceBefore=1, spaceAfter=2,
    )
    skill_style = ParagraphStyle(
        "ResumeSkill", parent=normal, leftIndent=0, spaceAfter=2,
    )

    entries = {item.entry_id: item for item in resume.entries}
    bullets = {item.bullet_id: item for item in resume.bullets}
    if not set(bullet_text_overrides) <= set(bullets):
        raise Phase2ValidationError("Resume PDF override references an unknown bullet")

    contacts = [item for item in resume.entries if item.item_type == BaseResumeItemType.CONTACT]
    contact_values = {
        dict(item.fields)["field_type"]: dict(item.fields)["value"]
        for item in contacts
    }
    story: list[Any] = []
    name = contact_values.get(ContactFieldType.NAME.value)
    if name:
        story.append(Paragraph(_safe_text(name), name_style))
    contact_parts: list[str] = []
    email = contact_values.get(ContactFieldType.EMAIL.value)
    if email:
        safe_email = _safe_email(email)
        contact_parts.append(
            f'<link href="mailto:{html.escape(safe_email, quote=True)}">{_safe_text(email)}</link>'
        )
    phone = contact_values.get(ContactFieldType.PHONE.value)
    if phone:
        contact_parts.append(_safe_text(phone))
    for field, label in (
        (ContactFieldType.GITHUB_URL, "GitHub"),
        (ContactFieldType.LINKEDIN_URL, "LinkedIn"),
        (ContactFieldType.PORTFOLIO_URL, "Portfolio"),
    ):
        value = contact_values.get(field.value)
        if value:
            url = _safe_https_url(value)
            contact_parts.append(
                f'<link href="{html.escape(url, quote=True)}">{label}</link>'
            )
    if contact_parts:
        story.append(Paragraph(" | ".join(contact_parts), contact_style))

    titles = {
        ResumeSectionType.EDUCATION: "Education",
        ResumeSectionType.EXPERIENCE: "Experience",
        ResumeSectionType.PROJECTS: "Projects",
        ResumeSectionType.SKILLS: "Skills",
    }
    for section in resume.sections:
        if not section.entry_ids:
            continue
        story.append(CondPageBreak(0.85 * inch))
        story.append(Paragraph(_safe_text(titles[section.section_type]), section_style))
        story.append(Table(
            [[""]], colWidths=[7.4 * inch], rowHeights=[0.5],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#0F3D63")),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]),
        ))
        story.append(Spacer(1, 3))

        if section.section_type == ResumeSectionType.SKILLS:
            grouped: dict[str, list[str]] = {}
            for entry_id in section.entry_ids:
                entry = entries[entry_id]
                grouped.setdefault(entry.skill_group or "Other", []).append(_skill_text(entry))
            for group, values in grouped.items():
                label = skill_group_display_name(group)
                body = ", ".join(_safe_text(item) for item in values)
                story.append(Paragraph(f"<b>{_safe_text(label)}:</b> {body}", skill_style))
            continue

        for entry_id in section.entry_ids:
            story.append(CondPageBreak(0.5 * inch))
            entry = entries[entry_id]
            fields = dict(entry.fields)
            if entry.item_type == BaseResumeItemType.PROJECT:
                title = fields.get("organization_or_project_name") or fields.get("title_or_role")
                organization = fields.get("title_or_role")
            elif entry.item_type == BaseResumeItemType.EXPERIENCE:
                title = fields.get("title_or_role") or fields.get("organization_or_project_name")
                organization = fields.get("organization_or_project_name")
            else:
                title = fields.get("degree") or fields.get("field_of_study") or fields.get("institution")
                organization = fields.get("institution")
            if not title:
                raise Phase2ValidationError("Resume PDF entry lacks a renderable title")
            heading = _safe_text(title)
            if organization and organization != title:
                heading += f", {_safe_text(organization)}"
            date_text = _entry_dates(entry, fields)
            header = Table(
                [[Paragraph(heading, heading_style), Paragraph(_safe_text(date_text), right_style)]],
                colWidths=[5.2 * inch, 2.2 * inch],
                style=TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ]),
            )
            story.append(header)
            if entry.item_type == BaseResumeItemType.EDUCATION:
                field = fields.get("field_of_study")
                if field and field != title:
                    story.append(Paragraph(_safe_text(field), detail_style))
                if fields.get("gpa"):
                    story.append(Paragraph(f"GPA: {_safe_text(fields['gpa'])}", detail_style))
            context_text = fields.get("short_factual_summary") or fields.get("description")
            if context_text:
                story.append(Paragraph(_safe_text(context_text), detail_style))
            for bullet_id in entry.bullet_ids:
                text = bullet_text_overrides.get(bullet_id, bullets[bullet_id].text)
                story.append(Paragraph(_safe_text(text), bullet_style, bulletText="-"))
            story.append(Spacer(1, 3))
    return story


def _write_pdf(
    path: Path,
    resume: BaseResumeArtifact,
    bullet_text_overrides: Mapping[str, str],
) -> int:
    reportlab = _load_reportlab()
    SimpleDocTemplate = reportlab["SimpleDocTemplate"]
    canvas_module = reportlab["canvas"]
    inch = reportlab["inch"]
    pages = {"count": 0}

    def on_page(pdf_canvas, _document):
        pages["count"] = max(pages["count"], pdf_canvas.getPageNumber())

    def invariant_canvas(filename, **kwargs):
        kwargs["invariant"] = 1
        kwargs["pageCompression"] = 1
        return canvas_module.Canvas(filename, **kwargs)

    document = SimpleDocTemplate(
        str(path), pagesize=reportlab["letter"],
        leftMargin=0.55 * inch, rightMargin=0.55 * inch,
        topMargin=0.45 * inch, bottomMargin=0.45 * inch,
        title="Resume", author="", subject="Validated final resume",
        creator="Aarvia",
    )
    document.build(
        _story(resume, bullet_text_overrides, reportlab),
        onFirstPage=on_page, onLaterPages=on_page,
        canvasmaker=invariant_canvas,
    )
    return pages["count"]


def export_final_resume_pdf(
    *,
    fact_check: FinalResumeFactCheckArtifact,
    output_path: str | Path,
    **context: Any,
) -> ResumePDFExportResult:
    """Validate the exact final resume and atomically export a display-only PDF."""

    target = Path(output_path)
    if target.exists():
        raise FileExistsError(f"Final Resume PDF already exists: {target}")
    fact_check.validate(**context)
    if (
        fact_check.summary.passed_count != fact_check.summary.bullet_count
        or any(review.status != ResumeSemanticReviewStatus.PASSED for review in fact_check.reviews)
    ):
        raise Phase2ValidationError(
            "Final Resume PDF requires every bullet in the bound Fact Check to be passed"
        )
    checks, resume, _, kind = prepare_final_resume_bullet_checks(**context)
    if kind != fact_check.final_resume_kind:
        raise Phase2ValidationError("Final Resume PDF kind does not match its Fact Check")
    if tuple(item.bullet_check_id for item in checks) != tuple(
        item.bullet_check_id for item in fact_check.bullet_checks
    ):
        raise Phase2ValidationError("Final Resume PDF content is stale")
    overrides = {
        item.bullet_id: item.final_text
        for item in fact_check.bullet_checks
        if item.wording_changed
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=target.parent, suffix=".pdf", delete=False,
        ) as handle:
            temporary = Path(handle.name)
        page_count = _write_pdf(temporary, resume, overrides)
        payload = temporary.read_bytes()
        if page_count < 1 or len(payload) < 512 or not payload.startswith(b"%PDF-"):
            raise Phase2ValidationError("Final Resume PDF generation produced an invalid file")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        # Link the fully written file into place so a concurrently created
        # target is never overwritten. The temporary file lives beside the
        # target, so the hard-link operation is same-filesystem and atomic.
        os.link(temporary, target)
        temporary.unlink()
        temporary = None
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return ResumePDFExportResult(
        target,
        hashlib.sha256(payload).hexdigest(),
        len(payload),
        page_count,
    )
