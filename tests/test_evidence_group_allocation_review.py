from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from aarvia.capability_rubric import EvidenceClass, production_capability_rubric
from aarvia.evidence_group_allocation_review import (
    AllocationReviewDecision,
    AllocationReviewerType,
    EvidenceGroupAllocationReviewArtifact,
    create_evidence_group_allocation_review_artifact,
    effective_resolved_group_bindings,
    load_evidence_group_allocation_reviews,
    resolved_group_bindings,
    save_evidence_group_allocation_reviews,
)
from aarvia.evidence_binding_review import (
    BindingReviewDecision,
    BindingReviewerType,
    create_evidence_binding_review_artifact,
)
from aarvia.llm_client import LLMSettings
from aarvia.profile_dimension_mapping import (
    MappingCandidateKind,
    MappingCandidateRejection,
    MappingRejectionReason,
    MappingValidationReport,
    OpenAIProfileDimensionMapper,
    ProfileDimensionMappingCandidateSet,
    _retry_evidence_group_summaries,
    _select_retry_result,
    canonical_evidence_span_inventory,
)
from aarvia.role_catalog import Phase2ValidationError, production_role_catalog
from aarvia.role_recommendation import (
    RoleRecommendationArtifact,
    build_role_recommendation,
)
from recommendation_fixtures import synthetic_profile


def unresolved_mapping(order=(0, 1)):
    profile = synthetic_profile()
    rubric = production_capability_rubric()
    span = next(item for item in canonical_evidence_span_inventory(profile) if item.path.endswith("short_factual_summary"))
    dimensions = [
        item for item in rubric.dimensions
        if item.role_id == "applied_ai_engineer"
        and EvidenceClass.PROJECT_SUMMARY in item.evidence_support_policy.allowed_evidence_classes
        and item.evidence_support_policy.provisional_status_cap.value == "partially_demonstrated"
    ][:2]
    assert len(dimensions) == 2
    rows = [{"role_id": item.role_id, "dimension_id": item.dimension_id, "criterion_id": item.criterion_ids[0], "span_id": span.span_id, "proposed_binding_type": "direct", "provider_confidence": "high"} for item in dimensions]
    payload = {"mappings": [rows[index] for index in order], "directional_signals": [], "constraints": [], "conflict_warnings": []}
    mapping = ProfileDimensionMappingCandidateSet.from_provider_payload_isolated(
        payload, profile=profile, rubric=rubric, catalog=production_role_catalog(),
        provider_name="fixture", provider_model="fixture", attempt_number=1,
        response_hash_reference="sha256:" + "1" * 64,
        evidence_spans=canonical_evidence_span_inventory(profile), mapping_schema_version=5,
    )
    return profile, rubric, mapping


def test_ambiguous_group_is_preserved_with_zero_contribution_and_is_order_independent():
    profile, rubric, first = unresolved_mapping()
    _, _, reversed_value = unresolved_mapping((1, 0))
    assert first.to_dict() == reversed_value.to_dict()
    assert first.mappings == ()
    assert len(first.unresolved_evidence_groups) == 1
    assert len(first.unresolved_evidence_groups[0].members) == 2
    recommendation = build_role_recommendation(profile=profile, mapping_candidates=first, rubric=rubric, catalog=production_role_catalog(), created_at="2026-01-01T00:00:00+00:00")
    assert recommendation.schema_version == 7
    assert recommendation.unresolved_evidence_group_ids == (first.unresolved_evidence_groups[0].evidence_group_id,)
    assert all(result.extended_current_fit.coverage == 0 for result in recommendation.role_results)
    without_group = replace(first, unresolved_evidence_groups=())
    baseline = build_role_recommendation(
        profile=profile,
        mapping_candidates=without_group,
        rubric=rubric,
        catalog=production_role_catalog(),
        created_at="2026-01-01T00:00:00+00:00",
    )
    assert recommendation.role_results == baseline.role_results


def test_resolved_primary_and_secondary_are_capped_and_typed_round_trip():
    profile, rubric, mapping = unresolved_mapping()
    group = mapping.unresolved_evidence_groups[0]
    primary, secondary = group.allowed_primary_dimension_ids
    reviews = create_evidence_group_allocation_review_artifact(
        profile=profile, rubric=rubric, mapping=mapping, catalog=production_role_catalog(),
        decisions={group.evidence_group_id: (AllocationReviewDecision.RESOLVED, primary, secondary, AllocationReviewerType.PROFILE_OWNER, "2026-01-01T00:00:00+00:00")},
    )
    reviews = EvidenceGroupAllocationReviewArtifact.from_dict(reviews.to_dict())
    reviews.validate(profile=profile, rubric=rubric, mapping=mapping, catalog=production_role_catalog())
    bindings = resolved_group_bindings(mapping, reviews)
    assert sorted({item.contribution_weight for item in bindings}) == [0.3, 1.0]
    recommendation = build_role_recommendation(profile=profile, mapping_candidates=mapping, rubric=rubric, catalog=production_role_catalog(), created_at="2026-01-01T00:00:00+00:00", allocation_reviews=reviews)
    assert recommendation.allocation_review_artifact_id == reviews.review_artifact_id
    assert any(result.extended_current_fit.coverage > 0 for result in recommendation.role_results)
    assert any(result.provisional for result in recommendation.role_results)


@pytest.mark.parametrize("decision", [AllocationReviewDecision.REJECTED, AllocationReviewDecision.DEFERRED])
def test_reject_and_defer_keep_group_at_zero_contribution(decision):
    profile, rubric, mapping = unresolved_mapping()
    group = mapping.unresolved_evidence_groups[0]
    reviews = create_evidence_group_allocation_review_artifact(profile=profile, rubric=rubric, mapping=mapping, catalog=production_role_catalog(), decisions={group.evidence_group_id: (decision, None, None, AllocationReviewerType.PROFILE_OWNER, "2026-01-01T00:00:00+00:00")})
    assert resolved_group_bindings(mapping, reviews) == ()


def test_allocation_review_cannot_change_members_or_choose_unknown_dimension():
    profile, rubric, mapping = unresolved_mapping()
    group = mapping.unresolved_evidence_groups[0]
    with pytest.raises(Phase2ValidationError):
        create_evidence_group_allocation_review_artifact(profile=profile, rubric=rubric, mapping=mapping, catalog=production_role_catalog(), decisions={group.evidence_group_id: (AllocationReviewDecision.RESOLVED, "invented_dimension", None, AllocationReviewerType.PROFILE_OWNER, "2026-01-01T00:00:00+00:00")})
    valid = create_evidence_group_allocation_review_artifact(profile=profile, rubric=rubric, mapping=mapping, catalog=production_role_catalog(), decisions={group.evidence_group_id: (AllocationReviewDecision.DEFERRED, None, None, AllocationReviewerType.PROFILE_OWNER, "2026-01-01T00:00:00+00:00")})
    raw = valid.to_dict(); raw["reviews"][0]["member_binding_ids"] = ["forged"]
    with pytest.raises(Phase2ValidationError):
        EvidenceGroupAllocationReviewArtifact.from_dict(raw).validate(profile=profile, rubric=rubric, mapping=mapping, catalog=production_role_catalog())


def test_mapping_five_rejects_provider_allocation_injection():
    profile, rubric, _ = unresolved_mapping()
    span = next(item for item in canonical_evidence_span_inventory(profile) if item.path.endswith("short_factual_summary"))
    dimension = next(item for item in rubric.dimensions if EvidenceClass.PROJECT_SUMMARY in item.evidence_support_policy.allowed_evidence_classes)
    payload = {"mappings": [{"role_id": dimension.role_id, "dimension_id": dimension.dimension_id, "criterion_id": dimension.criterion_ids[0], "span_id": span.span_id, "proposed_binding_type": "direct", "provider_confidence": "high", "contribution_relationship": "primary"}], "directional_signals": [], "constraints": [], "conflict_warnings": []}
    result = ProfileDimensionMappingCandidateSet.from_provider_payload_isolated(payload, profile=profile, rubric=rubric, catalog=production_role_catalog(), provider_name="fixture", provider_model="fixture", attempt_number=1, response_hash_reference="sha256:" + "2" * 64, evidence_spans=canonical_evidence_span_inventory(profile), mapping_schema_version=5)
    assert not result.mappings
    assert result.validation_report.reason_codes == ("provider_field_injection",)


def test_same_dimension_multiple_criteria_are_retained_inside_unresolved_group():
    profile = synthetic_profile()
    rubric = production_capability_rubric()
    span = next(
        item for item in canonical_evidence_span_inventory(profile)
        if item.path.endswith("short_factual_summary")
    )
    dimensions = [
        item for item in rubric.dimensions
        if item.role_id == "applied_ai_engineer"
        and EvidenceClass.PROJECT_SUMMARY
        in item.evidence_support_policy.allowed_evidence_classes
        and item.evidence_support_policy.provisional_status_cap.value
        == "partially_demonstrated"
    ]
    multi = next(item for item in dimensions if len(item.criterion_ids) >= 2)
    other = next(item for item in dimensions if item.dimension_id != multi.dimension_id)
    rows = [
        {
            "role_id": item.role_id,
            "dimension_id": item.dimension_id,
            "criterion_id": criterion_id,
            "span_id": span.span_id,
            "proposed_binding_type": "direct",
            "provider_confidence": "high",
        }
        for item, criterion_id in (
            (multi, multi.criterion_ids[0]),
            (multi, multi.criterion_ids[1]),
            (other, other.criterion_ids[0]),
        )
    ]
    payload = {
        "mappings": rows,
        "directional_signals": [],
        "constraints": [],
        "conflict_warnings": [],
    }
    baseline = None
    for ordered in (rows, list(reversed(rows)), [rows[1], rows[2], rows[0]]):
        payload["mappings"] = ordered
        value = ProfileDimensionMappingCandidateSet.from_provider_payload_isolated(
            payload,
            profile=profile,
            rubric=rubric,
            catalog=production_role_catalog(),
            provider_name="fixture",
            provider_model="fixture",
            attempt_number=1,
            response_hash_reference="sha256:" + "3" * 64,
            evidence_spans=canonical_evidence_span_inventory(profile),
            mapping_schema_version=5,
        )
        assert len(value.unresolved_evidence_groups[0].members) == 3
        assert sum(
            member.dimension_id == multi.dimension_id
            for member in value.unresolved_evidence_groups[0].members
        ) == 2
        baseline = value.to_dict() if baseline is None else baseline
        assert value.to_dict() == baseline


def test_reviewed_multi_criterion_reasoning_is_stably_deduplicated():
    profile = synthetic_profile()
    rubric = production_capability_rubric()
    catalog = production_role_catalog()
    span = next(
        item for item in canonical_evidence_span_inventory(profile)
        if item.path.endswith("short_factual_summary")
    )
    dimensions = [
        item for item in rubric.dimensions
        if item.role_id == "applied_ai_engineer"
        and EvidenceClass.PROJECT_SUMMARY
        in item.evidence_support_policy.allowed_evidence_classes
        and item.evidence_support_policy.provisional_status_cap.value
        == "partially_demonstrated"
    ]
    primary = next(item for item in dimensions if len(item.criterion_ids) >= 2)
    secondary = next(item for item in dimensions if item.dimension_id != primary.dimension_id)
    rows = [
        {
            "role_id": item.role_id,
            "dimension_id": item.dimension_id,
            "criterion_id": criterion_id,
            "span_id": span.span_id,
            "proposed_binding_type": "direct",
            "provider_confidence": "high",
        }
        for item, criterion_id in (
            (primary, primary.criterion_ids[0]),
            (primary, primary.criterion_ids[1]),
            (secondary, secondary.criterion_ids[0]),
        )
    ]

    recommendations = []
    for provider_rows in (rows, list(reversed(rows))):
        mapping = ProfileDimensionMappingCandidateSet.from_provider_payload_isolated(
            {
                "mappings": provider_rows,
                "directional_signals": [],
                "constraints": [],
                "conflict_warnings": [],
            },
            profile=profile,
            rubric=rubric,
            catalog=catalog,
            provider_name="fixture",
            provider_model="fixture",
            attempt_number=1,
            response_hash_reference="sha256:" + "4" * 64,
            evidence_spans=canonical_evidence_span_inventory(profile),
            mapping_schema_version=5,
        )
        group = mapping.unresolved_evidence_groups[0]
        allocation_reviews = create_evidence_group_allocation_review_artifact(
            profile=profile,
            rubric=rubric,
            mapping=mapping,
            catalog=catalog,
            decisions={
                group.evidence_group_id: (
                    AllocationReviewDecision.RESOLVED,
                    primary.dimension_id,
                    secondary.dimension_id,
                    AllocationReviewerType.PROFILE_OWNER,
                    "2026-01-01T00:00:00+00:00",
                )
            },
        )
        allocated = resolved_group_bindings(mapping, allocation_reviews)
        binding_reviews = create_evidence_binding_review_artifact(
            profile=profile,
            rubric=rubric,
            mapping=mapping,
            catalog=catalog,
            decisions={
                item.binding_id: (
                    BindingReviewDecision.CONFIRMED
                    if item.dimension_id == primary.dimension_id
                    else BindingReviewDecision.REJECTED,
                    BindingReviewerType.PROFILE_OWNER,
                    "2026-01-01T00:00:00+00:00",
                )
                for item in allocated
            },
        )
        recommendation = build_role_recommendation(
            profile=profile,
            mapping_candidates=mapping,
            rubric=rubric,
            catalog=catalog,
            created_at="2026-01-01T00:00:00+00:00",
            evidence_reviews=binding_reviews,
            allocation_reviews=allocation_reviews,
        )
        result = next(
            item for item in recommendation.role_results
            if item.role_id == primary.role_id
        )
        assessment = next(
            item for item in result.extended_current_fit.dimension_assessments
            if item.dimension_id == primary.dimension_id
        )
        assert len(assessment.supporting_bindings) == 2
        assert {item.criterion_id for item in assessment.supporting_bindings} == {
            primary.criterion_ids[0],
            primary.criterion_ids[1],
        }
        assert len(assessment.supporting_atomic_evidence) == 2
        assert len(assessment.reasoning) == 1
        assert len(assessment.reasoning) == len(set(assessment.reasoning))

        loaded = RoleRecommendationArtifact.from_dict(recommendation.to_dict())
        loaded.validate(
            profile=profile,
            rubric=rubric,
            catalog=catalog,
            mapping_candidates=mapping,
            evidence_reviews=binding_reviews,
            allocation_reviews=allocation_reviews,
        )
        assert loaded == recommendation

        raw = recommendation.to_dict()
        raw_assessment = next(
            item
            for role in raw["role_results"]
            if role["role_id"] == primary.role_id
            for item in role["extended_current_fit"]["dimension_assessments"]
            if item["dimension_id"] == primary.dimension_id
        )
        raw_assessment["reasoning"].append(raw_assessment["reasoning"][0])
        with pytest.raises(Phase2ValidationError, match="contains duplicate values"):
            RoleRecommendationArtifact.from_dict(raw)
        recommendations.append(recommendation)

    assert recommendations[0].to_dict() == recommendations[1].to_dict()
    assert (
        recommendations[0].recommendation_set_id
        == recommendations[1].recommendation_set_id
    )


def test_allocation_review_staleness_save_load_and_effective_unresolved_summary(tmp_path):
    profile, rubric, mapping = unresolved_mapping()
    group = mapping.unresolved_evidence_groups[0]
    primary, secondary = group.allowed_primary_dimension_ids
    reviews = create_evidence_group_allocation_review_artifact(
        profile=profile,
        rubric=rubric,
        mapping=mapping,
        catalog=production_role_catalog(),
        decisions={
            group.evidence_group_id: (
                AllocationReviewDecision.RESOLVED,
                primary,
                secondary,
                AllocationReviewerType.PROFILE_OWNER,
                "2026-01-01T00:00:00+00:00",
            )
        },
    )
    path = save_evidence_group_allocation_reviews(
        reviews,
        tmp_path / "allocation.json",
        profile=profile,
        rubric=rubric,
        mapping=mapping,
        catalog=production_role_catalog(),
    )
    assert load_evidence_group_allocation_reviews(
        path,
        profile=profile,
        rubric=rubric,
        mapping=mapping,
        catalog=production_role_catalog(),
    ) == reviews
    recommendation = build_role_recommendation(
        profile=profile,
        mapping_candidates=mapping,
        rubric=rubric,
        catalog=production_role_catalog(),
        created_at="2026-01-01T00:00:00+00:00",
        allocation_reviews=reviews,
    )
    assert recommendation.unresolved_evidence_group_ids == ()
    stale = replace(mapping, provider_model="changed")
    with pytest.raises(Phase2ValidationError, match="stale"):
        reviews.validate(
            profile=profile,
            rubric=rubric,
            mapping=stale,
            catalog=production_role_catalog(),
        )


def test_deferred_group_remains_unresolved_but_rejected_group_does_not():
    profile, rubric, mapping = unresolved_mapping()
    group = mapping.unresolved_evidence_groups[0]
    for decision, expected in (
        (AllocationReviewDecision.DEFERRED, (group.evidence_group_id,)),
        (AllocationReviewDecision.REJECTED, ()),
    ):
        reviews = create_evidence_group_allocation_review_artifact(
            profile=profile,
            rubric=rubric,
            mapping=mapping,
            catalog=production_role_catalog(),
            decisions={
                group.evidence_group_id: (
                    decision,
                    None,
                    None,
                    AllocationReviewerType.AUTHORIZED_REVIEWER,
                    "2026-01-01T00:00:00+00:00",
                )
            },
        )
        recommendation = build_role_recommendation(
            profile=profile,
            mapping_candidates=mapping,
            rubric=rubric,
            catalog=production_role_catalog(),
            created_at="2026-01-01T00:00:00+00:00",
            allocation_reviews=reviews,
        )
        assert recommendation.unresolved_evidence_group_ids == expected


def test_rejected_primary_promotes_only_review_selected_secondary():
    profile, rubric, mapping = unresolved_mapping()
    group = mapping.unresolved_evidence_groups[0]
    primary, secondary = group.allowed_primary_dimension_ids
    allocation_reviews = create_evidence_group_allocation_review_artifact(
        profile=profile,
        rubric=rubric,
        mapping=mapping,
        catalog=production_role_catalog(),
        decisions={
            group.evidence_group_id: (
                AllocationReviewDecision.RESOLVED,
                primary,
                secondary,
                AllocationReviewerType.PROFILE_OWNER,
                "2026-01-01T00:00:00+00:00",
            )
        },
    )
    primary_ids = {
        member.binding_id for member in group.members
        if member.dimension_id == primary
    }
    effective = effective_resolved_group_bindings(
        mapping,
        allocation_reviews,
        excluded_binding_ids=frozenset(primary_ids),
    )
    assert {item.dimension_id for item in effective} == {secondary}
    assert {item.contribution_weight for item in effective} == {1.0}


def test_binding_review_cannot_replace_missing_allocation_review():
    profile, rubric, mapping = unresolved_mapping()
    group = mapping.unresolved_evidence_groups[0]
    member = group.members[0]
    binding_reviews = create_evidence_binding_review_artifact(
        profile=profile,
        rubric=rubric,
        mapping=mapping,
        catalog=production_role_catalog(),
        decisions={
            member.binding_id: (
                BindingReviewDecision.CONFIRMED,
                BindingReviewerType.PROFILE_OWNER,
                "2026-01-01T00:00:00+00:00",
            )
        },
    )
    with pytest.raises(Phase2ValidationError, match="not selected by allocation"):
        build_role_recommendation(
            profile=profile,
            mapping_candidates=mapping,
            rubric=rubric,
            catalog=production_role_catalog(),
            created_at="2026-01-01T00:00:00+00:00",
            evidence_reviews=binding_reviews,
        )


def test_diagnostics_report_candidate_and_group_counts_without_evidence_text():
    _, _, mapping = unresolved_mapping()
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "fixture"),
        client=SimpleNamespace(),
        mapping_schema_version=5,
    )
    report = MappingValidationReport(
        attempt_number=1,
        total_candidate_count=1,
        response_hash_reference="sha256:" + "8" * 64,
        rejections=(
            MappingCandidateRejection(
                candidate_kind=MappingCandidateKind.CURRENT_FIT,
                candidate_index=0,
                role_id="applied_ai_engineer",
                dimension_id="fixture_dimension",
                canonical_profile_path="experience_overview[0].short_factual_summary",
                reason_code=MappingRejectionReason.EVIDENCE_GROUP_DIMENSION_LIMIT,
                attempt_number=1,
                recoverable=True,
                response_hash_reference="sha256:" + "8" * 64,
                evidence_group_id="evidence_group_fixture",
            ),
        ),
    )
    mapper._write_diagnostics(
        attempt=1,
        response=None,
        error=None,
        validation_report=report,
        mapping_result=mapping,
    )
    diagnostics = mapper.attempt_diagnostics[0]
    assert diagnostics["candidate_rejection_count"] == 1
    assert diagnostics["rejected_evidence_group_count"] == 1
    assert diagnostics["unresolved_candidate_count"] == 2
    assert diagnostics["unresolved_evidence_group_count"] == 1
    encoded = str(diagnostics)
    assert "Built and evaluated" not in encoded
    assert "secret" not in encoded


def test_retry_selector_cannot_drop_unresolved_accepted_candidates():
    _, _, first = unresolved_mapping()
    report = MappingValidationReport(
        attempt_number=1,
        total_candidate_count=3,
        response_hash_reference="sha256:" + "9" * 64,
        rejections=(
            MappingCandidateRejection(
                candidate_kind=MappingCandidateKind.CONSTRAINT,
                candidate_index=0,
                role_id="applied_ai_engineer",
                dimension_id=None,
                canonical_profile_path=None,
                reason_code=MappingRejectionReason.INVALID_CONSTRAINT,
                attempt_number=1,
                recoverable=True,
                response_hash_reference="sha256:" + "9" * 64,
            ),
        ),
    )
    first = replace(first, validation_report=report)
    second = replace(
        first,
        unresolved_evidence_groups=(),
        conflict_warnings=(),
        validation_report=None,
    )
    selected, reason, attempt = _select_retry_result(first, second)
    assert selected is first
    assert reason.startswith("attempt_1_retained_")
    assert attempt == 1


def test_retry_group_summary_contains_only_safe_transport_identifiers():
    payload = {
        "mappings": [{
            "role_id": "applied_ai_engineer",
            "dimension_id": "dimension_fixture",
            "criterion_id": "criterion_fixture",
            "span_id": "span_fixture",
            "proposed_binding_type": "direct",
            "provider_confidence": "high",
        }]
    }
    report = MappingValidationReport(
        attempt_number=1,
        total_candidate_count=1,
        response_hash_reference="sha256:" + "a" * 64,
        rejections=(
            MappingCandidateRejection(
                candidate_kind=MappingCandidateKind.CURRENT_FIT,
                candidate_index=0,
                role_id="applied_ai_engineer",
                dimension_id="dimension_fixture",
                canonical_profile_path="experience_overview[0].short_factual_summary",
                reason_code=MappingRejectionReason.EVIDENCE_GROUP_DIMENSION_LIMIT,
                attempt_number=1,
                recoverable=True,
                response_hash_reference="sha256:" + "a" * 64,
            ),
        ),
    )
    assert _retry_evidence_group_summaries(
        payload, report, mapping_schema_version=5
    ) == ({
        "role_id": "applied_ai_engineer",
        "span_id": "span_fixture",
        "dimension_id": "dimension_fixture",
        "criterion_id": "criterion_fixture",
        "reason_code": "evidence_group_dimension_limit",
    },)
