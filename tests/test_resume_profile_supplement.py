from __future__ import annotations

from dataclasses import replace
import json

import pytest

from aarvia.resume_profile_supplement import (
    ContactFieldType, ResumeProfileSupplementArtifact, StructuralUse,
    SupplementReviewDecision, build_resume_profile_supplement,
    create_contact_field, create_structural_selection, create_supplement_review,
    load_resume_profile_supplement, save_resume_profile_supplement,
    select_confirmed_resume_profile_supplement,
)
from aarvia.resume_wording import (
    WordingCandidateType, WordingOrigin, WordingReviewDecision,
    build_resume_wording_review, create_wording_candidate, create_wording_review,
)
from aarvia.role_catalog import Phase2ValidationError
from test_resume_wording import _context


NOW = "2026-10-10T00:00:00+00:00"
LATER = "2026-10-10T01:00:00+00:00"


def _fixture(tmp_path):
    source, context, material = _context(tmp_path)
    selected = material.materials[0]
    candidate_type = (
        WordingCandidateType.STRUCTURAL_ENTRY
        if selected.eligibility.value == "structural_only" else WordingCandidateType.BULLET
    )
    if candidate_type == WordingCandidateType.BULLET:
        selected = next(item for item in material.materials if item.eligibility.value == "achievement_component")
    candidate = create_wording_candidate(
        section_type=selected.section, candidate_type=candidate_type,
        source_record_id=selected.source_record_id, material_ids=[selected.material_id],
        wording_text=(selected.exact_text if candidate_type == WordingCandidateType.STRUCTURAL_ENTRY else "Verified wording"),
        origin=(WordingOrigin.EXACT_MATERIAL if candidate_type == WordingCandidateType.STRUCTURAL_ENTRY else WordingOrigin.USER_AUTHORED),
        created_at=NOW,
    )
    review = create_wording_review(candidate, decision=WordingReviewDecision.CONFIRMED, reviewed_at=NOW)
    wording = build_resume_wording_review(
        resume_material=material, candidates=[candidate], reviews=[review],
        created_at=NOW, **context,
    )
    full_context = dict(
        context, resume_material=material, resume_wording_review=wording,
    )
    return source, material, wording, full_context


def _uncovered_path(source, material, context, prefix):
    used_sources = {item.source_record_id for item in material.materials}
    covered = {
        item.canonical_profile_path for item in context["evidence_bank"].sources
        if item.source_record_id in used_sources
    }
    profile = source["profile_object"]
    count = {
        "education": len(profile.education),
        "skills": len(profile.skills),
        "experience_overview": len(profile.experience_overview),
    }[prefix]
    return next(f"{prefix}[{index}]" for index in range(count) if f"{prefix}[{index}]" not in covered)


def _artifact(tmp_path, decision=SupplementReviewDecision.CONFIRMED):
    source, material, wording, context = _fixture(tmp_path)
    contact = create_contact_field(ContactFieldType.EMAIL, "owner@example.test")
    path = _uncovered_path(source, material, context, "education")
    selection = create_structural_selection(
        source["profile_object"], path, ["institution", "degree", "field_of_study"],
    )
    reviews = [
        create_supplement_review(contact, decision=decision, reviewed_at=NOW),
        create_supplement_review(selection, decision=decision, reviewed_at=NOW),
    ]
    artifact = build_resume_profile_supplement(
        contact_fields=[contact], structural_selections=[selection],
        inclusion_reviews=reviews, created_at=NOW, **context,
    )
    return source, context, contact, selection, artifact


@pytest.mark.parametrize(("field_type", "value"), [
    (ContactFieldType.NAME, "Profile Owner"),
    (ContactFieldType.EMAIL, "owner@example.test"),
    (ContactFieldType.PHONE, "+1 (555) 555-0100"),
    (ContactFieldType.GITHUB_URL, "https://github.com/example"),
    (ContactFieldType.LINKEDIN_URL, "https://www.linkedin.com/in/example"),
    (ContactFieldType.PORTFOLIO_URL, "https://example.test/work"),
])
def test_contact_types_are_strict_and_deterministic(field_type, value):
    first = create_contact_field(field_type, value)
    assert create_contact_field(field_type, value) == first
    assert first.origin.value == "user_provided"


@pytest.mark.parametrize(("field_type", "value"), [
    (ContactFieldType.EMAIL, "not-an-email"),
    (ContactFieldType.PHONE, "123"),
    (ContactFieldType.GITHUB_URL, "https://example.test/user"),
    (ContactFieldType.LINKEDIN_URL, "http://linkedin.com/in/user"),
])
def test_invalid_contact_is_rejected_without_network(field_type, value):
    with pytest.raises(Phase2ValidationError):
        create_contact_field(field_type, value)


def test_schema_round_trip_save_load_and_selector(tmp_path):
    _, context, contact, selection, artifact = _artifact(tmp_path)
    assert ResumeProfileSupplementArtifact.from_dict(artifact.to_dict()) == artifact
    path = tmp_path / "supplement.json"
    save_resume_profile_supplement(artifact, path, **context)
    loaded = load_resume_profile_supplement(path, **context)
    assert loaded == artifact
    assert select_confirmed_resume_profile_supplement(loaded, **context) == ((contact,), (selection,))


def test_rejected_and_deferred_are_not_selected(tmp_path):
    for decision in (SupplementReviewDecision.REJECTED, SupplementReviewDecision.DEFERRED):
        _, context, _, _, artifact = _artifact(tmp_path / decision.value, decision)
        assert select_confirmed_resume_profile_supplement(artifact, **context) == ((), ())


def test_rejected_required_contact_keeps_coverage_partial(tmp_path):
    _, _, _, _, artifact = _artifact(tmp_path, SupplementReviewDecision.REJECTED)
    assert artifact.coverage.contact_rejected == 1
    assert artifact.coverage.partial_coverage is True


def test_structural_types_and_absent_values_are_enforced(tmp_path):
    source, material, _, context = _fixture(tmp_path)
    skill = create_structural_selection(
        source["profile_object"], _uncovered_path(source, material, context, "skills"),
        ["skill_name", "category"],
    )
    assert skill.structural_use == StructuralUse.SKILL_ENTRY
    experience = create_structural_selection(
        source["profile_object"], _uncovered_path(source, material, context, "experience_overview"),
        ["organization_or_project_name", "title_or_role"],
    )
    assert experience.structural_use in {StructuralUse.EXPERIENCE_CONTEXT, StructuralUse.PROJECT_CONTEXT}
    with pytest.raises(Phase2ValidationError):
        create_structural_selection(source["profile_object"], skill.profile_reference.path, ["achievement"])


def test_material_covered_profile_record_cannot_be_selected(tmp_path):
    source, material, _, context = _fixture(tmp_path)
    used = {item.source_record_id for item in material.materials}
    covered_path = next(item.canonical_profile_path for item in context["evidence_bank"].sources if item.source_record_id in used)
    fields = (["skill_name"] if covered_path.startswith("skills") else
              ["institution"] if covered_path.startswith("education") else ["title_or_role"])
    selection = create_structural_selection(source["profile_object"], covered_path, fields)
    review = create_supplement_review(selection, decision=SupplementReviewDecision.CONFIRMED, reviewed_at=NOW)
    with pytest.raises(Phase2ValidationError, match="already covers"):
        build_resume_profile_supplement(
            contact_fields=[], structural_selections=[selection], inclusion_reviews=[review],
            created_at=NOW, **context,
        )


def test_input_order_does_not_change_artifact(tmp_path):
    source, material, wording, context = _fixture(tmp_path)
    contacts = [
        create_contact_field(ContactFieldType.NAME, "Owner"),
        create_contact_field(ContactFieldType.EMAIL, "owner@example.test"),
    ]
    reviews = [create_supplement_review(item, decision=SupplementReviewDecision.CONFIRMED, reviewed_at=NOW) for item in contacts]
    first = build_resume_profile_supplement(contact_fields=contacts, structural_selections=[], inclusion_reviews=reviews, created_at=NOW, **context)
    second = build_resume_profile_supplement(contact_fields=list(reversed(contacts)), structural_selections=[], inclusion_reviews=list(reversed(reviews)), created_at=NOW, **context)
    assert first == second
    assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(second.to_dict(), sort_keys=True)


@pytest.mark.parametrize("field", ["supplement_id", "coverage", "summary", "profile_fingerprint", "resume_material_artifact_id", "resume_wording_review_id"])
def test_typed_load_rejects_tampering(tmp_path, field):
    _, _, _, _, artifact = _artifact(tmp_path)
    data = artifact.to_dict()
    if field in {"coverage", "summary"}:
        key = next(iter(data[field])); data[field][key] = 999
    else:
        data[field] = "tampered"
    with pytest.raises(Phase2ValidationError):
        ResumeProfileSupplementArtifact.from_dict(data)


def test_snapshot_path_and_unknown_field_tampering_are_rejected(tmp_path):
    _, _, _, _, artifact = _artifact(tmp_path)
    for mutate in ("path", "snapshot", "unknown"):
        data = artifact.to_dict()
        if mutate == "path": data["structural_selections"][0]["profile_reference"]["path"] = "education[99]"
        elif mutate == "snapshot": data["structural_selections"][0]["profile_reference"]["value_snapshot"]["degree"] = "Invented"
        else: data["unexpected"] = True
        with pytest.raises(Phase2ValidationError):
            ResumeProfileSupplementArtifact.from_dict(data)


def test_revision_is_immutable_and_requires_increasing_time(tmp_path):
    _, context, _, _, original = _artifact(tmp_path)
    revised = build_resume_profile_supplement(
        contact_fields=[], structural_selections=[], inclusion_reviews=[], created_at=LATER,
        superseded_supplement=original, **context,
    )
    assert revised.supersedes_supplement_id == original.supplement_id
    assert revised.created_at == original.created_at
    assert revised.updated_at == LATER
    with pytest.raises(Phase2ValidationError):
        build_resume_profile_supplement(
            contact_fields=[], structural_selections=[], inclusion_reviews=[], created_at=NOW,
            superseded_supplement=original, **context,
        )


def test_stale_profile_material_and_wording_are_rejected(tmp_path):
    source, context, _, _, artifact = _artifact(tmp_path)
    stale_profile = replace(
        source["profile_object"],
        basic_profile=replace(source["profile_object"].basic_profile, current_location="Changed"),
    )
    with pytest.raises(Phase2ValidationError):
        artifact.validate(**{**context, "profile": stale_profile})
    with pytest.raises(Phase2ValidationError):
        artifact.validate(**{**context, "resume_material": replace(context["resume_material"], resume_material_id="resume_material_stale")})
    with pytest.raises(Phase2ValidationError):
        artifact.validate(**{**context, "resume_wording_review": replace(context["resume_wording_review"], wording_review_id="resume_wording_stale")})


def test_save_refuses_overwrite_and_preserves_existing_file(tmp_path):
    _, context, _, _, artifact = _artifact(tmp_path)
    path = tmp_path / "supplement.json"
    path.write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError):
        save_resume_profile_supplement(artifact, path, **context)
    assert path.read_text(encoding="utf-8") == "existing"


def test_direct_construction_cannot_bypass_id_or_review_validation(tmp_path):
    _, context, _, _, artifact = _artifact(tmp_path)
    with pytest.raises(Phase2ValidationError):
        replace(artifact, supplement_id="supplement_forged").validate(**context)
    forged_contact = replace(artifact.contact_fields[0], value="changed@example.test")
    with pytest.raises(Phase2ValidationError):
        replace(artifact, contact_fields=(forged_contact,)).validate(**context)


def test_atomic_writer_failure_leaves_no_partial_file(tmp_path, monkeypatch):
    import aarvia.resume_profile_supplement as module

    _, context, _, _, artifact = _artifact(tmp_path)
    path = tmp_path / "supplement.json"

    def fail_write(*_args, **_kwargs):
        raise OSError("simulated atomic replacement failure")

    monkeypatch.setattr(module, "save_phase2_json", fail_write)
    with pytest.raises(OSError, match="simulated"):
        save_resume_profile_supplement(artifact, path, **context)
    assert not path.exists()
