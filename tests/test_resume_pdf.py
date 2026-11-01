from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from pypdf import PdfReader

from aarvia.base_resume import (
    BaseResumeEntry,
    BaseResumeItemType,
    BaseResumeSection,
    BaseResumeTemporalState,
)
from aarvia.resume_fact_check import (
    ResumeSemanticReviewStatus,
    build_final_resume_fact_check,
    prepare_final_resume_bullet_checks,
)
from aarvia.resume_pdf import (
    _write_pdf,
    export_final_resume_pdf,
    skill_group_display_name,
)
from aarvia.resume_material import ResumeSectionType
from aarvia.role_catalog import Phase2ValidationError
from test_resume_fact_check import NOW, final_context


def _fact_check(context, *, statuses=None):
    checks, _, _, _ = prepare_final_resume_bullet_checks(**context)
    statuses = statuses or [ResumeSemanticReviewStatus.PASSED] * len(checks)
    return build_final_resume_fact_check(
        decisions={item.bullet_check_id: status for item, status in zip(checks, statuses)},
        reasons={
            item.bullet_check_id: "Needs review."
            for item, status in zip(checks, statuses)
            if status != ResumeSemanticReviewStatus.PASSED
        },
        created_at=NOW,
        **context,
    )


def _text(path: Path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


def test_wording_overlay_and_ordering_only_export_are_valid_and_deterministic(
    final_context, tmp_path,
):
    for label, context in (
        ("overlay", final_context),
        (
            "ordering",
            dict(
                final_context,
                wording_proposal=None,
                wording_review=None,
                wording_tailored_resume=None,
            ),
        ),
    ):
        fact_check = _fact_check(context)
        first = export_final_resume_pdf(
            fact_check=fact_check, output_path=tmp_path / f"{label}-1.pdf", **context,
        )
        second = export_final_resume_pdf(
            fact_check=fact_check, output_path=tmp_path / f"{label}-2.pdf", **context,
        )
        assert first.page_count >= 1
        assert first.byte_length > 512
        assert first.sha256 == second.sha256
        reader = PdfReader(first.path)
        assert len(reader.pages) == first.page_count
        assert tuple(float(value) for value in reader.pages[0].mediabox[2:]) == (612.0, 792.0)
        rendered = _text(first.path)
        for check in fact_check.bullet_checks:
            assert check.final_text.split()[0] in rendered


def test_special_characters_https_link_and_long_text_render_without_loss(
    final_context, tmp_path,
):
    checks, resume, _, _ = prepare_final_resume_bullet_checks(**final_context)
    contacts = (
        BaseResumeEntry(
            "entry_name", "subject_name", BaseResumeItemType.CONTACT,
            None, "contact_name", "review_name", None, None, (), (), (), None,
            (("field_type", "name"), ("value", "Example <User> & Co.")),
            (), BaseResumeTemporalState.UNSPECIFIED,
        ),
        BaseResumeEntry(
            "entry_email", "subject_email", BaseResumeItemType.CONTACT,
            None, "contact_email", "review_email", None, None, (), (), (), None,
            (("field_type", "email"), ("value", "example.user@example.test")),
            (), BaseResumeTemporalState.UNSPECIFIED,
        ),
        BaseResumeEntry(
            "entry_link", "subject_link", BaseResumeItemType.CONTACT,
            None, "contact_link", "review_link", None, None, (), (), (), None,
            (("field_type", "github_url"), ("value", "https://example.test/profile?a=1&b=2")),
            (), BaseResumeTemporalState.UNSPECIFIED,
        ),
    )
    education = BaseResumeEntry(
        "entry_education", "subject_education", BaseResumeItemType.EDUCATION,
        None, "education_selection", "education_review", None, None, (), (), (),
        "education[0]",
        (
            ("degree", "Master of Science"),
            ("expected_graduation_date", "2027-12"),
            ("field_of_study", "Artificial Intelligence & Systems"),
            ("gpa", "3.8/4.0"),
            ("institution", "Example University"),
            ("start_date", "2026-09"),
        ),
        (), BaseResumeTemporalState.UNSPECIFIED,
    )
    experience = BaseResumeEntry(
        "entry_experience", "subject_experience", BaseResumeItemType.EXPERIENCE,
        None, "experience_selection", "experience_review", None, None, (), (), (),
        "experience_overview[0]",
        (
            ("end_date", "2025-08"),
            ("organization_or_project_name", "Example Lab & Partners"),
            ("short_factual_summary", "Built <validated> local tools & tests."),
            ("start_date", "2025-06"),
            ("title_or_role", "Engineering Intern"),
        ),
        (), BaseResumeTemporalState.COMPLETED,
    )
    skills = (
        BaseResumeEntry(
            "entry_skill_python", "subject_skill_python", BaseResumeItemType.SKILL,
            None, "skill_python", "skill_review_python", None, None, (), (), (),
            "skills[0]",
            (("category", "programming_languages"), ("skill_name", "Python"),
             ("self_reported_proficiency", "intermediate")),
            (), BaseResumeTemporalState.UNSPECIFIED, "programming_languages",
        ),
        BaseResumeEntry(
            "entry_skill_agent", "subject_skill_agent", BaseResumeItemType.SKILL,
            None, "skill_agent", "skill_review_agent", None, None, (), (), (),
            "skills[1]",
            (("category", "ai_application_development"), ("skill_name", "Agent prototypes")),
            (), BaseResumeTemporalState.UNSPECIFIED, "ai_application_development",
        ),
    )
    modified = replace(
        resume,
        entries=(*resume.entries, *contacts, education, experience, *skills),
        sections=(
            BaseResumeSection(ResumeSectionType.EDUCATION, (education.entry_id,)),
            BaseResumeSection(ResumeSectionType.EXPERIENCE, (experience.entry_id,)),
            *resume.sections,
            BaseResumeSection(
                ResumeSectionType.SKILLS,
                tuple(item.entry_id for item in skills),
            ),
        ),
    )
    long_text = (
        'AT&T <safe> & "quoted" content remains exact. ' * 180
        + "END-OF-LONG-BULLET"
    )
    path = tmp_path / "long.pdf"
    pages = _write_pdf(path, modified, {checks[0].bullet_id: long_text})
    reader = PdfReader(path)
    text = _text(path)
    assert pages == len(reader.pages) and pages > 1
    assert "Projects" in (reader.pages[0].extract_text() or "")
    assert "Synthetic Ranking Project" in (reader.pages[0].extract_text() or "")
    assert "Example <User> & Co." in text
    assert "Expected Dec 2027" in text
    assert "Artificial Intelligence & Systems" in text
    assert "Example Lab & Partners" in text
    assert "Programming Languages: Python (intermediate)" in text
    assert "AI Application Development: Agent prototypes" in text
    assert "AT&T <safe> &" in text
    assert "END-OF-LONG-BULLET" in text
    uris = []
    for page in reader.pages:
        for annotation in page.get("/Annots", ()):
            action = annotation.get_object().get("/A")
            if action and action.get("/URI"):
                uris.append(action["/URI"])
    assert "mailto:example.user@example.test" in uris
    assert "https://example.test/profile?a=1&b=2" in uris


def test_fact_check_must_pass_every_bullet_and_output_is_atomic(
    final_context, tmp_path, monkeypatch,
):
    checks, _, _, _ = prepare_final_resume_bullet_checks(**final_context)
    statuses = [ResumeSemanticReviewStatus.ISSUE_FOUND] + [
        ResumeSemanticReviewStatus.PASSED
    ] * (len(checks) - 1)
    with pytest.raises(Phase2ValidationError, match="every bullet"):
        export_final_resume_pdf(
            fact_check=_fact_check(final_context, statuses=statuses),
            output_path=tmp_path / "blocked.pdf",
            **final_context,
        )
    assert not (tmp_path / "blocked.pdf").exists()

    def fail(path, resume, overrides):
        path.write_bytes(b"%PDF-partial")
        raise OSError("synthetic PDF failure")

    monkeypatch.setattr("aarvia.resume_pdf._write_pdf", fail)
    target = tmp_path / "failed.pdf"
    with pytest.raises(OSError, match="synthetic PDF failure"):
        export_final_resume_pdf(
            fact_check=_fact_check(final_context),
            output_path=target,
            **final_context,
        )
    assert not target.exists()
    assert not list(tmp_path.glob("*.pdf"))

    existing = tmp_path / "existing.pdf"
    existing.write_bytes(b"existing private output")
    with pytest.raises(FileExistsError, match="already exists"):
        export_final_resume_pdf(
            fact_check=_fact_check(final_context),
            output_path=existing,
            **final_context,
        )
    assert existing.read_bytes() == b"existing private output"


def test_link_protocol_and_skill_headings_are_strict(final_context, tmp_path):
    _, resume, _, _ = prepare_final_resume_bullet_checks(**final_context)
    unsafe = BaseResumeEntry(
        "entry_link", "subject_link", BaseResumeItemType.CONTACT,
        None, "contact_link", "review_link", None, None, (), (), (), None,
        (("field_type", "github_url"), ("value", "javascript:alert(1)")),
        (), BaseResumeTemporalState.UNSPECIFIED,
    )
    with pytest.raises(Phase2ValidationError, match="safe HTTPS"):
        _write_pdf(tmp_path / "unsafe.pdf", replace(resume, entries=(*resume.entries, unsafe)), {})
    assert skill_group_display_name("ai_application_development") == "AI Application Development"
    assert skill_group_display_name("machine_learning") == "Machine Learning"
    assert skill_group_display_name("data_engineering") == "Data Engineering"

