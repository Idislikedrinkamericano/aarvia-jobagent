from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import os

import pytest

from aarvia.capability_rubric import EvidenceClass, production_capability_rubric
from aarvia.evidence_binding_review import (
    BindingReviewDecision,
    BindingReviewerType,
    EvidenceBindingReviewArtifact,
    EvidenceBindingReviewStaleError,
    create_evidence_binding_review_artifact,
    load_evidence_binding_reviews,
    save_evidence_binding_reviews,
)
from aarvia.profile import CareerProfile
from aarvia.profile_dimension_mapping import (
    CurrentMatchStatus,
    ProfileDimensionMappingCandidateSet,
    canonical_evidence_span_inventory,
)
from aarvia.role_catalog import Phase2ValidationError, production_role_catalog
from aarvia.role_recommendation import (
    RecommendationBlockerCode,
    build_role_recommendation,
    load_role_recommendation,
    save_role_recommendation,
)
from recommendation_fixtures import synthetic_profile


NOW = "2026-09-27T12:00:00+00:00"


def _profile() -> CareerProfile:
    data = synthetic_profile().to_dict()
    data["skills"][0]["self_reported_proficiency"] = "intermediate"
    for index in range(1, 70):
        data["experience_overview"].append(
            {
                "experience_type": "project" if index % 2 else "internship",
                "organization_or_project_name": f"Review Example {index}",
                "title_or_role": "Contributor",
                "short_factual_summary": f"Implemented distinct verified behavior {index}.",
                "start_date": "2025-01",
                "end_date": "2025-02",
            }
        )
    return CareerProfile.from_dict(data)


def _span(profile: CareerProfile, path: str):
    values = [item for item in canonical_evidence_span_inventory(profile) if item.path == path]
    assert len(values) == 1
    return values[0]


def _raw(dimension, criterion_id: str, span) -> dict:
    return {
        "role_id": dimension.role_id,
        "dimension_id": dimension.dimension_id,
        "criterion_id": criterion_id,
        "span_id": span.span_id,
        "proposed_binding_type": "direct",
        "provider_confidence": "high",
    }


def _mapping(profile: CareerProfile, bindings: list[dict]) -> ProfileDimensionMappingCandidateSet:
    return ProfileDimensionMappingCandidateSet.from_provider_payload_v3(
        {
            "mappings": bindings,
            "directional_signals": [],
            "constraints": [],
            "conflict_warnings": [],
        },
        profile=profile,
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
        provider_name="fixture",
        provider_model="fixture-model",
        evidence_spans=canonical_evidence_span_inventory(profile),
    )


def _review(mapping, profile, decisions):
    return create_evidence_binding_review_artifact(
        profile=profile,
        rubric=production_capability_rubric(),
        mapping=mapping,
        catalog=production_role_catalog(),
        decisions={
            binding.binding_id: (decision, BindingReviewerType.PROFILE_OWNER, NOW)
            for binding, decision in zip(mapping.mappings, decisions, strict=True)
        },
    )


def _dimension_with_behavior():
    rubric = production_capability_rubric()
    return next(
        item for item in rubric.dimensions
        if EvidenceClass.PROJECT_SUMMARY in item.evidence_support_policy.allowed_evidence_classes
        and len(item.criterion_ids) >= 2
    )


def _two_binding_mapping(profile: CareerProfile):
    dimension = _dimension_with_behavior()
    mapping = _mapping(
        profile,
        [
            _raw(dimension, dimension.criterion_ids[0], _span(profile, "experience_overview[1].short_factual_summary")),
            _raw(dimension, dimension.criterion_ids[1], _span(profile, "experience_overview[3].short_factual_summary")),
        ],
    )
    return dimension, mapping


def _assessment(artifact, dimension_id):
    return next(
        item for role in artifact.role_results
        for item in role.extended_current_fit.dimension_assessments
        if item.dimension_id == dimension_id
    )


def test_valid_confirmation_is_typed_deterministic_and_round_trips(tmp_path) -> None:
    profile = _profile(); _, mapping = _two_binding_mapping(profile)
    review = _review(mapping, profile, [BindingReviewDecision.CONFIRMED] * 2)
    path = tmp_path / "reviews.json"
    save_evidence_binding_reviews(
        review, path, profile=profile, rubric=production_capability_rubric(),
        mapping=mapping, catalog=production_role_catalog(),
    )
    loaded = load_evidence_binding_reviews(
        path, profile=profile, rubric=production_capability_rubric(),
        mapping=mapping, catalog=production_role_catalog(),
    )
    assert loaded == review
    assert path.read_bytes() == path.read_bytes()


@pytest.mark.parametrize("decision", [BindingReviewDecision.REJECTED, BindingReviewDecision.DEFERRED])
def test_rejected_and_deferred_have_explicit_scoring_semantics(decision) -> None:
    profile = _profile(); dimension, mapping = _two_binding_mapping(profile)
    review = _review(mapping, profile, [decision, decision])
    artifact = build_role_recommendation(
        profile=profile, mapping_candidates=mapping,
        rubric=production_capability_rubric(), catalog=production_role_catalog(),
        created_at=NOW, evidence_reviews=review,
    )
    assessment = _assessment(artifact, dimension.dimension_id)
    if decision == BindingReviewDecision.REJECTED:
        assert assessment.status == CurrentMatchStatus.UNKNOWN
        assert not assessment.supporting_bindings
        assert assessment.review_required is False
    else:
        assert assessment.status == CurrentMatchStatus.PARTIAL
        assert len(assessment.supporting_bindings) == 2
        assert assessment.review_required is True


def test_review_context_changes_are_stale() -> None:
    profile = _profile(); _, mapping = _two_binding_mapping(profile)
    review = _review(mapping, profile, [BindingReviewDecision.CONFIRMED] * 2)
    changed = CareerProfile.from_dict({**profile.to_dict(), "basic_profile": {"name": "Changed", "current_location": "Test City", "current_status": "Student"}})
    with pytest.raises(EvidenceBindingReviewStaleError, match="Profile is stale"):
        review.validate(profile=changed, rubric=production_capability_rubric(), mapping=mapping, catalog=production_role_catalog())
    with pytest.raises(EvidenceBindingReviewStaleError, match="Rubric is stale"):
        replace(review, rubric_sha256="sha256:" + "0" * 64).validate(profile=profile, rubric=production_capability_rubric(), mapping=mapping, catalog=production_role_catalog())
    with pytest.raises(EvidenceBindingReviewStaleError, match="Mapping is stale"):
        replace(review, mapping_artifact_id="mapping_artifact_stale").validate(profile=profile, rubric=production_capability_rubric(), mapping=mapping, catalog=production_role_catalog())


def test_missing_binding_and_identity_or_id_tampering_are_rejected() -> None:
    profile = _profile(); _, mapping = _two_binding_mapping(profile)
    review = _review(mapping, profile, [BindingReviewDecision.CONFIRMED] * 2)
    raw = review.to_dict()
    raw["reviews"][0]["binding_id"] = "binding_missing"
    tampered = EvidenceBindingReviewArtifact.from_dict(raw)
    with pytest.raises(EvidenceBindingReviewStaleError, match="missing Mapping binding"):
        tampered.validate(profile=profile, rubric=production_capability_rubric(), mapping=mapping, catalog=production_role_catalog())
    raw = review.to_dict(); raw["reviews"][0]["review_id"] = "binding_review_forged"
    with pytest.raises(Phase2ValidationError, match="not deterministic"):
        EvidenceBindingReviewArtifact.from_dict(raw).validate(profile=profile, rubric=production_capability_rubric(), mapping=mapping, catalog=production_role_catalog())
    raw = review.to_dict(); raw["reviews"][0]["span_id"] = "provider_injected"
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        EvidenceBindingReviewArtifact.from_dict(raw)


def test_structural_confirmation_does_not_upgrade_to_semantic_status() -> None:
    profile = _profile(); rubric = production_capability_rubric()
    dimension = next(
        item for item in rubric.dimensions
        if EvidenceClass.SKILL_NAME in item.evidence_support_policy.allowed_evidence_classes
    )
    mapping = _mapping(profile, [_raw(dimension, dimension.criterion_ids[0], _span(profile, "skills[0].skill_name"))])
    review = _review(mapping, profile, [BindingReviewDecision.CONFIRMED])
    artifact = build_role_recommendation(profile=profile, mapping_candidates=mapping, rubric=rubric, catalog=production_role_catalog(), created_at=NOW, evidence_reviews=review)
    assessment = _assessment(artifact, dimension.dimension_id)
    assert assessment.status == mapping.mappings[0].derived_match_status
    assert assessment.status not in {CurrentMatchStatus.PARTIAL, CurrentMatchStatus.DEMONSTRATED}


def test_confirmation_applies_policy_thresholds_and_behavior_requirement() -> None:
    profile = _profile(); dimension, mapping = _two_binding_mapping(profile)
    one_confirmed = _review(mapping, profile, [BindingReviewDecision.CONFIRMED, BindingReviewDecision.DEFERRED])
    partial = build_role_recommendation(profile=profile, mapping_candidates=mapping, rubric=production_capability_rubric(), catalog=production_role_catalog(), created_at=NOW, evidence_reviews=one_confirmed)
    assert _assessment(partial, dimension.dimension_id).status == CurrentMatchStatus.PARTIAL
    all_confirmed = _review(mapping, profile, [BindingReviewDecision.CONFIRMED] * 2)
    demonstrated = build_role_recommendation(profile=profile, mapping_candidates=mapping, rubric=production_capability_rubric(), catalog=production_role_catalog(), created_at=NOW, evidence_reviews=all_confirmed)
    assert _assessment(demonstrated, dimension.dimension_id).status == CurrentMatchStatus.DEMONSTRATED


def test_strong_requires_confirmed_policy_evidence_after_provisional_is_removed() -> None:
    profile = _profile(); rubric = production_capability_rubric()
    bindings = []
    project_index = 1
    experience_index = 2
    target_role = "machine_learning_engineer"
    for dimension in rubric.role_dimensions(target_role):
        policy = dimension.evidence_support_policy
        evidence_class = (
            EvidenceClass.PROJECT_SUMMARY
            if EvidenceClass.PROJECT_SUMMARY in policy.allowed_evidence_classes
            else EvidenceClass.EXPERIENCE_SUMMARY
        )
        indexes = []
        for _ in range(2):
            if evidence_class == EvidenceClass.PROJECT_SUMMARY:
                indexes.append(project_index); project_index += 2
            else:
                indexes.append(experience_index); experience_index += 2
        bindings.extend(
            _raw(
                dimension,
                criterion_id,
                _span(profile, f"experience_overview[{index}].short_factual_summary"),
            )
            for criterion_id, index in zip(dimension.criterion_ids[:2], indexes, strict=True)
        )
    mapping = _mapping(profile, bindings)
    review = _review(
        mapping, profile, [BindingReviewDecision.CONFIRMED] * len(mapping.mappings)
    )
    artifact = build_role_recommendation(
        profile=profile, mapping_candidates=mapping, rubric=rubric,
        catalog=production_role_catalog(), created_at=NOW, evidence_reviews=review,
    )
    role = next(item for item in artifact.role_results if item.role_id == target_role)
    assert role.core_current_fit.band.value == "strong"
    assert RecommendationBlockerCode.CONFIRMED_EVIDENCE_REQUIRED.value not in role.blockers


def test_rejecting_skill_name_prevents_orphan_proficiency_contribution() -> None:
    profile = _profile(); rubric = production_capability_rubric()
    dimension = next(
        item for item in rubric.dimensions
        if EvidenceClass.SKILL_NAME in item.evidence_support_policy.allowed_evidence_classes
        and EvidenceClass.SKILL_PROFICIENCY in item.evidence_support_policy.allowed_evidence_classes
    )
    mapping = _mapping(
        profile,
        [
            _raw(dimension, dimension.criterion_ids[0], _span(profile, "skills[0].skill_name")),
            _raw(dimension, dimension.criterion_ids[0], _span(profile, "skills[0].self_reported_proficiency")),
        ],
    )
    decisions = [
        BindingReviewDecision.REJECTED
        if item.evidence_class == EvidenceClass.SKILL_NAME
        else BindingReviewDecision.DEFERRED
        for item in mapping.mappings
    ]
    review = _review(mapping, profile, decisions)
    artifact = build_role_recommendation(
        profile=profile, mapping_candidates=mapping, rubric=rubric,
        catalog=production_role_catalog(), created_at=NOW, evidence_reviews=review,
    )
    assessment = _assessment(artifact, dimension.dimension_id)
    assert assessment.status == CurrentMatchStatus.UNKNOWN
    assert not assessment.supporting_bindings


def test_reviewed_recommendation_requires_context_and_typed_round_trip(tmp_path) -> None:
    profile = _profile(); _, mapping = _two_binding_mapping(profile)
    review = _review(mapping, profile, [BindingReviewDecision.CONFIRMED] * 2)
    artifact = build_role_recommendation(profile=profile, mapping_candidates=mapping, rubric=production_capability_rubric(), catalog=production_role_catalog(), created_at=NOW, evidence_reviews=review)
    with pytest.raises(Phase2ValidationError, match="requires Mapping and Evidence Review context"):
        artifact.validate(profile=profile, rubric=production_capability_rubric(), catalog=production_role_catalog())
    path = tmp_path / "recommendation.json"
    save_role_recommendation(artifact, path, profile=profile, rubric=production_capability_rubric(), catalog=production_role_catalog(), mapping_candidates=mapping, evidence_reviews=review)
    assert load_role_recommendation(path, profile=profile, rubric=production_capability_rubric(), catalog=production_role_catalog(), mapping_candidates=mapping, evidence_reviews=review) == artifact


def test_review_atomic_replacement_failure_preserves_existing_file(tmp_path, monkeypatch) -> None:
    profile = _profile(); _, mapping = _two_binding_mapping(profile)
    review = _review(mapping, profile, [BindingReviewDecision.CONFIRMED] * 2)
    path = tmp_path / "reviews.json"; path.write_text("original\n", encoding="utf-8")
    monkeypatch.setattr(os, "replace", lambda *_: (_ for _ in ()).throw(OSError("replace failed")))
    with pytest.raises(OSError, match="replace failed"):
        save_evidence_binding_reviews(review, path, profile=profile, rubric=production_capability_rubric(), mapping=mapping, catalog=production_role_catalog())
    assert path.read_text(encoding="utf-8") == "original\n"


def test_no_review_preserves_existing_schema_four_wire_behavior() -> None:
    profile = _profile(); dimension, mapping = _two_binding_mapping(profile)
    artifact = build_role_recommendation(profile=profile, mapping_candidates=mapping, rubric=production_capability_rubric(), catalog=production_role_catalog(), created_at=NOW)
    assert artifact.evidence_review_artifact_id is None
    assert "evidence_review_artifact_id" not in artifact.to_dict()
    assert _assessment(artifact, dimension.dimension_id).status == CurrentMatchStatus.PARTIAL
    assert RecommendationBlockerCode.CONFIRMED_EVIDENCE_REQUIRED.value in next(item for item in artifact.role_results if item.role_id == dimension.role_id).blockers
