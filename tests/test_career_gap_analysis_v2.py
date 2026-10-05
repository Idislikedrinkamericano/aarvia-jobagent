from __future__ import annotations

from dataclasses import replace
import json

import pytest

from aarvia.capability_rubric import (
    DimensionReadiness,
    EvidenceClass,
    capability_rubric_fingerprint,
)
from aarvia.career_direction import (
    DecisionStatus,
    RoleGapAnalysis,
    SelectedRole,
    create_user_role_decision,
)
from aarvia.career_gap_analysis import (
    CareerGapAnalysis,
    GapClassification,
    GapEvidenceState,
    GapPriority,
    GapReviewState,
    RoleSelectionType,
    _CLASSIFICATION_BY_STATUS,
    build_career_gap_analysis,
    generate_career_gap_analysis_id,
    load_career_gap_analysis,
    save_career_gap_analysis,
)
from aarvia.evidence_binding_review import (
    BindingReviewDecision,
    BindingReviewerType,
    create_evidence_binding_review_artifact,
)
from aarvia.profile import CareerProfile
from aarvia.profile_dimension_mapping import (
    CurrentMatchStatus,
    ProfileDimensionMappingCandidateSet,
    canonical_evidence_span_inventory,
)
from aarvia.role_catalog import Phase2ValidationError
from aarvia.role_recommendation import build_role_recommendation
from test_career_direction import gap_data
from test_recommendation_ranking_v7 import _profile_with_project_evidence, _ranking_fixture


NOW = "2026-10-05T00:00:00+00:00"


def _context():
    profile, rubric, catalog, mapping, reviews, recommendation = _ranking_fixture()
    decision = create_user_role_decision(
        status=DecisionStatus.CONFIRMED,
        catalog=catalog,
        rubric=rubric,
        profile=profile,
        recommendation_set=recommendation,
        primary_role=SelectedRole("machine_learning_engineer"),
        secondary_roles=(SelectedRole("applied_ai_engineer"),),
        explore_later_role_ids=("research_engineer",),
        user_reason="Focus on ML systems with applied AI as a secondary direction.",
        created_at=NOW,
        updated_at=NOW,
    )
    gap = build_career_gap_analysis(
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        mapping=mapping,
        recommendation=recommendation,
        decision=decision,
        evidence_reviews=reviews,
        created_at=NOW,
    )
    return profile, rubric, catalog, mapping, reviews, recommendation, decision, gap


def _validate(gap, context):
    profile, rubric, catalog, mapping, reviews, recommendation, decision, _ = context
    gap.validate(
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        mapping=mapping,
        recommendation=recommendation,
        decision=decision,
        evidence_reviews=reviews,
    )


def test_schema_two_round_trip_provenance_and_deterministic_id():
    context = _context()
    profile, rubric, catalog, _, _, recommendation, decision, gap = context

    assert gap.schema_version == 2
    assert gap.decision_id == decision.decision_id
    assert gap.recommendation_set_id == recommendation.recommendation_set_id
    assert gap.recommendation_schema_version == 7
    assert gap.profile_fingerprint == recommendation.profile_fingerprint
    assert gap.rubric_version == rubric.rubric_version
    assert gap.rubric_fingerprint == capability_rubric_fingerprint(rubric)
    assert gap.catalog_version == catalog.catalog_version
    assert gap.analysis_id == generate_career_gap_analysis_id(gap)
    assert CareerGapAnalysis.from_dict(gap.to_dict()) == gap
    _validate(gap, context)


def test_primary_secondary_are_separate_and_explore_later_is_not_analyzed():
    *_, gap = _context()
    assert gap.primary_role_analysis.role_id == "machine_learning_engineer"
    assert gap.primary_role_analysis.selection_type == RoleSelectionType.PRIMARY
    assert tuple(item.role_id for item in gap.secondary_role_analyses) == (
        "applied_ai_engineer",
    )
    assert all(
        item.selection_type == RoleSelectionType.SECONDARY
        for item in gap.secondary_role_analyses
    )
    assert "research_engineer" not in {
        gap.primary_role_analysis.role_id,
        *(item.role_id for item in gap.secondary_role_analyses),
    }


def test_unknown_partial_and_adjacent_have_distinct_safe_classifications():
    assert _CLASSIFICATION_BY_STATUS[CurrentMatchStatus.UNKNOWN] == (
        GapClassification.UNKNOWN_EVIDENCE
    )
    assert _CLASSIFICATION_BY_STATUS[CurrentMatchStatus.PARTIAL] == (
        GapClassification.DEVELOPING_CAPABILITY
    )
    assert _CLASSIFICATION_BY_STATUS[CurrentMatchStatus.ADJACENT] == (
        GapClassification.TRANSFERABLE_FOUNDATION
    )
    assert _CLASSIFICATION_BY_STATUS[CurrentMatchStatus.NOT_DEMONSTRATED] == (
        GapClassification.CAPABILITY_GAP
    )


def test_real_policy_fixture_never_turns_unknown_into_gap_or_partial_into_strength():
    *_, gap = _context()
    dimensions = (
        gap.primary_role_analysis.dimensions
        + tuple(item for role in gap.secondary_role_analyses for item in role.dimensions)
    )
    assert all(
        item.gap_classification == GapClassification.UNKNOWN_EVIDENCE
        for item in dimensions
        if item.recommendation_status == CurrentMatchStatus.UNKNOWN
    )
    assert all(
        item.gap_classification == GapClassification.DEVELOPING_CAPABILITY
        for item in dimensions
        if item.recommendation_status == CurrentMatchStatus.PARTIAL
    )
    assert not any(
        item.gap_classification == GapClassification.CAPABILITY_GAP
        for item in dimensions
    )


def test_provisional_or_review_required_evidence_is_not_a_confirmed_strength():
    *_, gap = _context()
    for role in (gap.primary_role_analysis, *gap.secondary_role_analyses):
        for item in role.dimensions:
            if item.review_state in {GapReviewState.REVIEW_REQUIRED, GapReviewState.MIXED}:
                assert item.gap_classification != GapClassification.CONFIRMED_STRENGTH
            if item.evidence_state == GapEvidenceState.PROVISIONAL_SEMANTIC:
                assert item.gap_classification != GapClassification.CONFIRMED_STRENGTH


def test_demonstrated_with_confirmed_policy_evidence_becomes_confirmed_strength():
    profile = _profile_with_project_evidence()
    _, rubric, catalog, *_ = _context()
    dimension = next(
        item for item in rubric.role_dimensions("machine_learning_engineer")
        if item.readiness == DimensionReadiness.READY
        and EvidenceClass.PROJECT_SUMMARY
        in item.evidence_support_policy.allowed_evidence_classes
    )
    required = dimension.evidence_support_policy.confirmed_demonstrated_rule.required_criterion_sets[0]
    spans = tuple(
        item for item in canonical_evidence_span_inventory(profile)
        if item.path.endswith("short_factual_summary")
    )
    rows = [
        {
            "role_id": dimension.role_id,
            "dimension_id": dimension.dimension_id,
            "criterion_id": criterion_id,
            "span_id": spans[index].span_id,
            "proposed_binding_type": "direct",
            "provider_confidence": "high",
        }
        for index, criterion_id in enumerate(required)
    ]
    mapping = ProfileDimensionMappingCandidateSet.from_provider_payload_isolated(
        {
            "mappings": rows,
            "directional_signals": [],
            "constraints": [],
            "conflict_warnings": [],
        },
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        provider_name="fixture",
        provider_model="fixture-model",
        attempt_number=1,
        response_hash_reference="sha256:" + "7" * 64,
        evidence_spans=canonical_evidence_span_inventory(profile),
        mapping_schema_version=5,
    )
    reviews = create_evidence_binding_review_artifact(
        profile=profile,
        rubric=rubric,
        mapping=mapping,
        catalog=catalog,
        decisions={
            item.binding_id: (
                BindingReviewDecision.CONFIRMED,
                BindingReviewerType.PROFILE_OWNER,
                NOW,
            )
            for item in mapping.mappings
        },
    )
    recommendation = build_role_recommendation(
        profile=profile,
        mapping_candidates=mapping,
        rubric=rubric,
        catalog=catalog,
        created_at=NOW,
        evidence_reviews=reviews,
    )
    decision = create_user_role_decision(
        status=DecisionStatus.CONFIRMED,
        catalog=catalog,
        rubric=rubric,
        profile=profile,
        recommendation_set=recommendation,
        primary_role=SelectedRole("machine_learning_engineer"),
        created_at=NOW,
        updated_at=NOW,
    )
    gap = build_career_gap_analysis(
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        mapping=mapping,
        recommendation=recommendation,
        decision=decision,
        evidence_reviews=reviews,
        created_at=NOW,
    )
    item = next(
        value for value in gap.primary_role_analysis.dimensions
        if value.dimension_id == dimension.dimension_id
    )
    assert item.recommendation_status == CurrentMatchStatus.DEMONSTRATED
    assert item.gap_classification == GapClassification.CONFIRMED_STRENGTH
    assert item.evidence_state == GapEvidenceState.CONFIRMED_SEMANTIC
    assert item.confirmed_criterion_ids == tuple(sorted(required))


def test_ready_dimensions_use_core_and_conditional_dimensions_use_extended_once():
    *_, gap = _context()
    for role in (gap.primary_role_analysis, *gap.secondary_role_analyses):
        ids = [item.dimension_id for item in role.dimensions]
        assert len(ids) == len(set(ids))
        assert ids == sorted(ids)


def test_primary_and_ready_priority_is_deterministic():
    *_, gap = _context()
    primary_unknown_ready = [
        item for item in gap.primary_role_analysis.dimensions
        if item.gap_classification == GapClassification.UNKNOWN_EVIDENCE
        and item.readiness == DimensionReadiness.READY
    ]
    assert primary_unknown_ready
    assert all(item.priority == GapPriority.HIGH for item in primary_unknown_ready)
    assert all(item.priority_reason_codes for item in gap.primary_role_analysis.dimensions)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("analysis_id", "gap_analysis_forged"),
        ("decision_id", "decision_forged"),
        ("recommendation_set_id", "recommendations_forged"),
        ("recommendation_schema_version", 6),
        ("profile_fingerprint", "sha256:" + "0" * 64),
        ("rubric_version", "9.9.9"),
        ("rubric_fingerprint", "sha256:" + "1" * 64),
        ("catalog_version", "9.9.9"),
    ],
)
def test_typed_load_rejects_provenance_and_id_tampering(field, value):
    *_, gap = _context()
    raw = gap.to_dict()
    raw[field] = value
    with pytest.raises(Phase2ValidationError):
        CareerGapAnalysis.from_dict(raw)


def _reidentified_gap(raw, gap):
    role = type(gap.primary_role_analysis).from_dict(
        raw["primary_role_analysis"], "primary"
    )
    provisional = replace(gap, primary_role_analysis=role)
    raw["analysis_id"] = generate_career_gap_analysis_id(provisional)
    return CareerGapAnalysis.from_dict(raw)


def test_typed_load_and_context_validation_reject_derived_field_tampering():
    context = _context()
    *_, gap = context
    cases = []

    classification = gap.to_dict()
    classification["primary_role_analysis"]["dimensions"][0]["gap_classification"] = (
        "capability_gap"
    )
    classification["primary_role_analysis"]["unknown_evidence_count"] -= 1
    classification["primary_role_analysis"]["capability_gap_count"] += 1
    cases.append(classification)

    priority = gap.to_dict()
    priority["primary_role_analysis"]["dimensions"][0]["priority"] = "low"
    cases.append(priority)

    bound_index = next(
        index for index, item in enumerate(gap.primary_role_analysis.dimensions)
        if item.supporting_binding_ids
    )
    binding = gap.to_dict()
    binding["primary_role_analysis"]["dimensions"][bound_index][
        "supporting_binding_ids"
    ] = []
    cases.append(binding)

    criterion = gap.to_dict()
    criterion["primary_role_analysis"]["dimensions"][bound_index][
        "confirmed_criterion_ids"
    ] = []
    cases.append(criterion)

    for raw in cases:
        forged = _reidentified_gap(raw, gap)
        with pytest.raises(Phase2ValidationError, match="deterministic source"):
            _validate(forged, context)


def test_unknown_fields_and_direct_construction_cannot_bypass_validation():
    context = _context()
    *_, gap = context
    raw = gap.to_dict()
    raw["provider_priority"] = "high"
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        CareerGapAnalysis.from_dict(raw)
    forged = replace(gap, analysis_id="gap_analysis_forged")
    with pytest.raises(Phase2ValidationError, match="deterministic source"):
        _validate(forged, context)


def test_stale_profile_rubric_catalog_recommendation_and_decision_are_rejected():
    context = _context()
    profile, rubric, catalog, mapping, reviews, recommendation, decision, gap = context
    changed_profile = CareerProfile.from_dict(profile.to_dict())
    changed_profile.basic_profile.current_status = "Changed"
    with pytest.raises(Phase2ValidationError):
        gap.validate(
            profile=changed_profile, rubric=rubric, catalog=catalog, mapping=mapping,
            recommendation=recommendation, decision=decision, evidence_reviews=reviews,
        )
    with pytest.raises(Phase2ValidationError):
        gap.validate(
            profile=profile, rubric=replace(rubric, rubric_version="9.9.9"),
            catalog=catalog, mapping=mapping, recommendation=recommendation,
            decision=decision, evidence_reviews=reviews,
        )
    with pytest.raises(Phase2ValidationError):
        gap.validate(
            profile=profile, rubric=rubric,
            catalog=replace(catalog, catalog_version="9.9.9"), mapping=mapping,
            recommendation=recommendation, decision=decision, evidence_reviews=reviews,
        )
    with pytest.raises(Phase2ValidationError):
        gap.validate(
            profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
            recommendation=replace(recommendation, recommendation_set_id="recommendations_stale"),
            decision=decision, evidence_reviews=reviews,
        )
    with pytest.raises(Phase2ValidationError):
        gap.validate(
            profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
            recommendation=recommendation,
            decision=replace(decision, decision_id="decision_stale"),
            evidence_reviews=reviews,
        )


def test_missing_required_review_context_is_rejected():
    profile, rubric, catalog, mapping, _, recommendation, decision, gap = _context()
    with pytest.raises(Phase2ValidationError, match="Evidence Review context"):
        gap.validate(
            profile=profile,
            rubric=rubric,
            catalog=catalog,
            mapping=mapping,
            recommendation=recommendation,
            decision=decision,
        )


def test_stale_mapping_and_review_context_are_rejected():
    profile, rubric, catalog, mapping, reviews, recommendation, decision, gap = _context()
    with pytest.raises(Phase2ValidationError):
        gap.validate(
            profile=profile,
            rubric=rubric,
            catalog=catalog,
            mapping=replace(mapping, provider_model="stale-model"),
            recommendation=recommendation,
            decision=decision,
            evidence_reviews=reviews,
        )
    with pytest.raises(Phase2ValidationError):
        gap.validate(
            profile=profile,
            rubric=rubric,
            catalog=catalog,
            mapping=mapping,
            recommendation=recommendation,
            decision=decision,
            evidence_reviews=replace(reviews, review_artifact_id="review_stale"),
        )


def test_file_round_trip_is_strict_immutable_and_deterministic(tmp_path):
    context = _context()
    profile, rubric, catalog, mapping, reviews, recommendation, decision, gap = context
    path = save_career_gap_analysis(
        gap, tmp_path / "gap.json", profile=profile, rubric=rubric, catalog=catalog,
        mapping=mapping, recommendation=recommendation, decision=decision,
        evidence_reviews=reviews,
    )
    assert load_career_gap_analysis(
        path, profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision, evidence_reviews=reviews,
    ) == gap
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        save_career_gap_analysis(
            gap, path, profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
            recommendation=recommendation, decision=decision, evidence_reviews=reviews,
        )
    assert path.read_bytes() == before


def test_schema_one_role_gap_wire_round_trip_is_unchanged():
    profile, *_ = _context()
    from aarvia.career_direction import ProfileReference

    value = RoleGapAnalysis.from_dict(
        gap_data(ProfileReference.capture(profile, "skills[0].skill_name"))
    )
    assert value.schema_version == 1
    assert RoleGapAnalysis.from_dict(value.to_dict()) == value
    assert value.to_dict()["schema"] == "aarvia.role_gap_analysis"
