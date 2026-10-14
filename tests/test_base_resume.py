from __future__ import annotations

from dataclasses import replace

import pytest

from aarvia.base_resume import (
    BaseResumeArtifact,
    BaseResumeItemType,
    BaseResumeTemporalState,
    build_base_resume,
    load_base_resume,
    render_base_resume_markdown,
    save_base_resume,
    save_base_resume_markdown,
    _date,
    _escape,
)
from aarvia.role_catalog import Phase2ValidationError
from test_resume_profile_supplement import _artifact, NOW


def _base(tmp_path, *, omitted=()):
    source, context, _, _, supplement = _artifact(tmp_path)
    kwargs = dict(context, resume_profile_supplement=supplement)
    artifact = build_base_resume(omitted_subject_ids=omitted, created_at=NOW, **kwargs)
    return source, kwargs, artifact


def test_round_trip_and_complete_subject_partition(tmp_path):
    _, context, artifact = _base(tmp_path)
    assert BaseResumeArtifact.from_dict(artifact.to_dict()) == artifact
    assert artifact.coverage.included_subject_count == artifact.coverage.eligible_subject_count
    assert artifact.coverage.omitted_subject_count == 0
    path = tmp_path / "base.json"
    save_base_resume(artifact, path, **context)
    assert load_base_resume(path, **context) == artifact


def test_omission_is_deterministic_and_never_in_body(tmp_path):
    _, context, artifact = _base(tmp_path)
    subject = next(item.subject_id for item in artifact.entries if item.subject_id)
    _, _, omitted = _base(tmp_path / "other", omitted=[subject])
    assert [item.subject_id for item in omitted.omissions] == [subject]
    assert subject not in {item.subject_id for item in omitted.entries}
    assert omitted.coverage.omitted_subject_count == 1


def test_tampered_partition_and_unknown_field_are_rejected(tmp_path):
    _, context, artifact = _base(tmp_path)
    data = artifact.to_dict(); data["invented"] = True
    with pytest.raises(Phase2ValidationError):
        BaseResumeArtifact.from_dict(data)
    data = artifact.to_dict(); data["omissions"] = [artifact.omissions[0].to_dict()] if artifact.omissions else []
    if artifact.entries:
        entry = artifact.entries[0]
        if entry.subject_id:
            from aarvia.base_resume import BaseResumeOmission, BaseResumeOmissionReason, generate_base_resume_omission_id
            draft = BaseResumeOmission("pending", entry.subject_id, "structural_selection", BaseResumeOmissionReason.USER_EXCLUDED)
            data["omissions"] = [{**draft.to_dict(), "omission_id": generate_base_resume_omission_id(draft)}]
            data["base_resume_id"] = artifact.base_resume_id
            with pytest.raises(Phase2ValidationError):
                BaseResumeArtifact.from_dict(data).validate(**context)


def test_structural_entries_never_become_bullets(tmp_path):
    _, _, artifact = _base(tmp_path)
    structural = [item for item in artifact.entries if item.item_type in {BaseResumeItemType.EDUCATION, BaseResumeItemType.SKILL}]
    assert structural
    assert all(not item.bullet_ids for item in structural)


def test_renderer_is_deterministic_private_and_safe(tmp_path):
    _, context, artifact = _base(tmp_path)
    first = render_base_resume_markdown(artifact, **context)
    assert first == render_base_resume_markdown(artifact, **context)
    assert first.endswith("\n") and not first.endswith("\n\n")
    path = tmp_path / "resume.md"
    _, digest = save_base_resume_markdown(artifact, path, **context)
    assert len(digest) == 64 and path.read_text() == first
    with pytest.raises(FileExistsError):
        save_base_resume_markdown(artifact, path, **context)


def test_tampered_layout_and_stale_upstream_are_rejected(tmp_path):
    _, context, artifact = _base(tmp_path)
    if artifact.sections[0].entry_ids:
        bad = replace(artifact, sections=artifact.sections[::-1])
        with pytest.raises(Phase2ValidationError):
            bad.validate(**context)
    bad = replace(artifact, supplement_id="supplement_fake")
    with pytest.raises(Phase2ValidationError):
        bad.validate(**context)


def test_revision_requires_new_file_and_lineage(tmp_path):
    _, context, original = _base(tmp_path)
    revised = build_base_resume(
        created_at="2026-10-10T01:00:00+00:00",
        superseded_base_resume=original, **context,
    )
    assert revised.supersedes_base_resume_id == original.base_resume_id
    assert revised.created_at == original.created_at
    path = tmp_path / "revision.json"
    save_base_resume(revised, path, superseded_base_resume=original, **context)
    with pytest.raises(FileExistsError):
        save_base_resume(revised, path, superseded_base_resume=original, **context)


def test_fixed_english_dates_expected_marker_and_commonmark_escape():
    assert _date("2027-12", expected=True) == "Expected Dec 2027"
    assert _date("2025-01") == "Jan 2025"
    assert _escape("Profile Owner(Preferred)") == "Profile Owner(Preferred)"
    assert _escape("A *verified* [fact]") == r"A \*verified\* \[fact\]"


def test_input_subject_order_does_not_change_artifact(tmp_path):
    _, context, artifact = _base(tmp_path)
    subjects = [item.subject_id for item in artifact.entries if item.subject_id]
    first = build_base_resume(omitted_subject_ids=subjects[:2], created_at=NOW, **context)
    second = build_base_resume(omitted_subject_ids=reversed(subjects[:2]), created_at=NOW, **context)
    assert first == second


def test_renderer_atomic_failure_preserves_existing_state(tmp_path, monkeypatch):
    _, context, artifact = _base(tmp_path)
    target = tmp_path / "resume.md"
    import aarvia.base_resume as module
    monkeypatch.setattr(module.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError, match="disk"):
        save_base_resume_markdown(artifact, target, **context)
    assert not target.exists()


def test_schema_has_no_job_tailoring_authority(tmp_path):
    _, _, artifact = _base(tmp_path)
    for forbidden in ("job_id", "company", "jd", "tailoring_instruction", "target_role"):
        data = artifact.to_dict(); data[forbidden] = "injected"
        with pytest.raises(Phase2ValidationError):
            BaseResumeArtifact.from_dict(data)
