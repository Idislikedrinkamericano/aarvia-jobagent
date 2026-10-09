from __future__ import annotations

from dataclasses import replace

import pytest

from aarvia.evidence_bank import ResumeEvidenceUse
from aarvia.evidence_enrichment import ClaimReviewDecision
from aarvia.resume_material import (
    ResumeCoverageReason,
    ResumeMaterialArtifact,
    ResumeMaterialEligibility,
    ResumeMaterialReason,
    ResumeMaterialType,
    ResumeSectionType,
    build_resume_material,
    generate_resume_material_artifact_id,
    load_resume_material,
    save_resume_material,
)
from aarvia.role_catalog import Phase2ValidationError
from test_evidence_enrichment import _artifact_context


NOW = "2026-10-08T00:00:00+00:00"
LATER = "2026-10-08T01:00:00+00:00"


def _context():
    values = _artifact_context()
    (
        profile, rubric, catalog, mapping, reviews, recommendation,
        decision, gap, bank, claim, claim_review, enrichment,
    ) = values
    context = dict(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision, gap_analysis=gap,
        evidence_bank=bank, evidence_enrichment=enrichment,
        evidence_reviews=reviews,
    )
    return values, context


def _artifact():
    values, context = _context()
    return values, context, build_resume_material(**context, created_at=NOW)


def _reidentify(value, **changes):
    provisional = replace(
        value, **changes, resume_material_id="resume_material_artifact_pending"
    )
    return replace(
        provisional,
        resume_material_id=generate_resume_material_artifact_id(provisional),
    )


def test_schema_one_round_trip_and_complete_provenance():
    values, _, artifact = _artifact()
    *_, bank, _, _, enrichment = values
    assert artifact.schema == "aarvia.resume_material"
    assert artifact.schema_version == 1
    assert artifact.evidence_bank_id == bank.evidence_bank_id
    assert artifact.enrichment_artifact_id == enrichment.enrichment_artifact_id
    assert artifact.recommendation_schema_version == 7
    assert ResumeMaterialArtifact.from_dict(artifact.to_dict()) == artifact


def test_builder_keeps_only_allowed_bank_items_and_confirmed_claims():
    values, _, artifact = _artifact()
    *_, bank, claim, _, _ = values
    bank_item_ids = {item.evidence_item_id for item in bank.items}
    for material in artifact.materials:
        assert set(material.evidence_item_ids) <= bank_item_ids
        assert material.source_record_id in {item.source_record_id for item in bank.sources}
    claim_material = next(
        item for item in artifact.materials
        if item.material_type == ResumeMaterialType.ENRICHMENT_CLAIM
    )
    assert claim_material.enrichment_claim_ids == (claim.claim_id,)
    assert claim_material.eligibility == ResumeMaterialEligibility.ACHIEVEMENT_COMPONENT
    assert ResumeMaterialReason.CONFIRMED_ENRICHMENT_CLAIM in claim_material.reason_codes
    selected_item_ids = {
        item.evidence_item_id for item in bank.items
        if item.resume_use != ResumeEvidenceUse.NOT_ELIGIBLE
    }
    profile_material_ids = {
        item.evidence_item_ids[0] for item in artifact.materials
        if item.material_type == ResumeMaterialType.PROFILE_EVIDENCE
    }
    assert profile_material_ids == selected_item_ids


def test_rejected_or_deferred_claim_is_not_resume_material():
    for decision in (ClaimReviewDecision.REJECTED, ClaimReviewDecision.DEFERRED):
        values = _artifact_context(decision=decision)
        (
            profile, rubric, catalog, mapping, reviews, recommendation,
            role_decision, gap, bank, claim, _, enrichment,
        ) = values
        artifact = build_resume_material(
            profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
            recommendation=recommendation, decision=role_decision,
            gap_analysis=gap, evidence_bank=bank,
            evidence_enrichment=enrichment, evidence_reviews=reviews,
            created_at=NOW,
        )
        assert all(claim.claim_id not in item.enrichment_claim_ids for item in artifact.materials)


def test_structural_material_stays_structural_and_out_of_achievement_sections():
    values, _, artifact = _artifact()
    *_, bank, _, _, _ = values
    structural_item_ids = {
        item.evidence_item_id for item in bank.items
        if item.resume_use == ResumeEvidenceUse.STRUCTURAL_INFORMATION
    }
    structural = [
        item for item in artifact.materials
        if set(item.evidence_item_ids).intersection(structural_item_ids)
        and item.material_type == ResumeMaterialType.PROFILE_EVIDENCE
    ]
    assert all(item.eligibility == ResumeMaterialEligibility.STRUCTURAL_ONLY for item in structural)
    assert all(item.section in {ResumeSectionType.SKILLS, ResumeSectionType.EDUCATION} for item in structural)
    assert all(
        item.section in {ResumeSectionType.SKILLS, ResumeSectionType.EDUCATION}
        for item in artifact.materials
        if item.eligibility == ResumeMaterialEligibility.STRUCTURAL_ONLY
    )


def test_coverage_reports_missing_records_without_materializing_them():
    values, _, artifact = _artifact()
    profile = values[0]
    uncovered = {
        issue.profile_path for issue in artifact.coverage.issues
        if issue.reason == ResumeCoverageReason.UNCOVERED_PROFILE_RECORD
    }
    assert artifact.coverage.partial_coverage
    assert artifact.coverage.profile_education_count == len(profile.education)
    assert artifact.coverage.profile_experience_count == len(profile.experience_overview)
    assert artifact.coverage.profile_skill_count == len(profile.skills)
    assert all(not item.section == ResumeSectionType.CONTACT for item in artifact.materials)
    source_paths = {item.canonical_profile_path for item in values[8].sources}
    for path in uncovered:
        assert path not in source_paths


def test_contact_coverage_is_report_only_and_never_material():
    _, _, artifact = _artifact()
    missing = {
        issue.profile_path for issue in artifact.coverage.issues
        if issue.reason == ResumeCoverageReason.MISSING_CONTACT_FIELD
    }
    assert {"contact.email", "contact.phone"} <= missing
    assert not any(item.section == ResumeSectionType.CONTACT for item in artifact.materials)


def test_build_is_deterministic():
    _, context = _context()
    first = build_resume_material(**context, created_at=NOW)
    second = build_resume_material(**context, created_at=NOW)
    assert first == second
    assert first.to_dict() == second.to_dict()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("eligibility", "achievement_component"),
        ("source_record_id", "source_record_unknown"),
        ("evidence_item_ids", ["evidence_item_unknown"]),
        ("exact_text", "Invented 900 percent result"),
    ],
)
def test_typed_load_rejects_material_tampering(tmp_path, field, replacement):
    _, context, artifact = _artifact()
    raw = artifact.to_dict()
    raw["materials"][0][field] = replacement
    path = tmp_path / "tampered.json"
    import json
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(Phase2ValidationError):
        load_resume_material(path, **context)


def test_typed_load_rejects_coverage_and_summary_tampering(tmp_path):
    _, context, artifact = _artifact()
    for mutate in (
        lambda raw: raw["coverage"].__setitem__("partial_coverage", False),
        lambda raw: raw["summary"].__setitem__("material_count", 999),
    ):
        raw = artifact.to_dict()
        mutate(raw)
        raw["resume_material_id"] = generate_resume_material_artifact_id(
            ResumeMaterialArtifact.from_dict(artifact.to_dict())
        )
        path = tmp_path / f"tampered-{len(list(tmp_path.iterdir()))}.json"
        import json
        path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(Phase2ValidationError):
            load_resume_material(path, **context)


def test_unknown_fields_are_rejected():
    _, _, artifact = _artifact()
    raw = artifact.to_dict()
    raw["provider_wording"] = "not allowed"
    with pytest.raises(Phase2ValidationError, match="unknown"):
        ResumeMaterialArtifact.from_dict(raw)


def test_save_load_round_trip_and_existing_output_protection(tmp_path):
    _, context, artifact = _artifact()
    path = save_resume_material(artifact, tmp_path / "materials.json", **context)
    assert load_resume_material(path, **context) == artifact
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        save_resume_material(artifact, path, **context)
    assert path.read_bytes() == original


def test_atomic_replacement_failure_leaves_no_output(tmp_path, monkeypatch):
    _, context, artifact = _artifact()
    path = tmp_path / "materials.json"
    monkeypatch.setattr(
        "aarvia.phase2_storage.os.replace",
        lambda *_: (_ for _ in ()).throw(OSError("replacement failed")),
    )
    with pytest.raises(OSError, match="replacement failed"):
        save_resume_material(artifact, path, **context)
    assert not path.exists()


def test_explicit_revision_uses_new_identity_and_requires_source():
    _, context = _context()
    first = build_resume_material(**context, created_at=NOW)
    second = build_resume_material(
        **context, created_at=LATER, superseded_resume_material=first
    )
    assert second.supersedes_resume_material_id == first.resume_material_id
    assert second.resume_material_id != first.resume_material_id
    with pytest.raises(Phase2ValidationError, match="requires"):
        second.validate(**context)
