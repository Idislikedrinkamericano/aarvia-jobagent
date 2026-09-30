from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from itertools import permutations
import hashlib
import json
from types import SimpleNamespace

import pytest

from aarvia.capability_rubric import EvidenceClass, production_capability_rubric
from aarvia.evidence_binding_review import (
    BindingReviewDecision,
    BindingReviewerType,
    create_evidence_binding_review_artifact,
)
from aarvia.profile_dimension_mapping import (
    AllocatedProfileCriterionEvidenceBinding,
    ContributionRelationship,
    CurrentMatchStatus,
    MappingRejectionReason,
    ProfileDimensionMappingCandidateSet,
    OpenAIProfileDimensionMapper,
    canonical_evidence_span_inventory,
    generate_mapping_artifact_id,
    provider_mapping_schema,
    CanonicalEvidenceSpan,
    _canonical_evidence_span_id,
)
from aarvia.profile import CareerProfile
from aarvia.llm_client import LLMSettings
from aarvia.role_catalog import Phase2ValidationError, production_role_catalog
from aarvia.role_recommendation import (
    RoleRecommendationArtifact,
    build_role_recommendation,
)
from recommendation_fixtures import synthetic_profile


NOW = "2026-09-29T12:00:00+00:00"


def _context():
    profile = synthetic_profile()
    rubric = production_capability_rubric()
    catalog = production_role_catalog()
    spans = canonical_evidence_span_inventory(profile)
    span = next(
        item for item in spans
        if item.path == "experience_overview[0].short_factual_summary"
    )
    dimensions = [
        item for item in rubric.role_dimensions("applied_ai_engineer")
        if EvidenceClass.PROJECT_SUMMARY
        in item.evidence_support_policy.allowed_evidence_classes
    ]
    return profile, rubric, catalog, spans, span, dimensions


def _raw(dimension, span, binding_type="direct", criterion_index=0):
    return {
        "role_id": dimension.role_id,
        "dimension_id": dimension.dimension_id,
        "criterion_id": dimension.criterion_ids[criterion_index],
        "span_id": span.span_id,
        "proposed_binding_type": binding_type,
        "provider_confidence": "high",
    }


def _isolated(raw, response_hash="sha256:" + "a" * 64):
    profile, rubric, catalog, spans, _, _ = _context()
    return ProfileDimensionMappingCandidateSet.from_provider_payload_isolated(
        {
            "mappings": list(raw),
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
        response_hash_reference=response_hash,
        evidence_spans=spans,
        mapping_schema_version=4,
    )


def test_mapping_four_is_permutation_invariant_and_allocates_primary_secondary():
    _, _, _, _, span, dimensions = _context()
    raw = (
        _raw(dimensions[0], span, "direct"),
        _raw(dimensions[1], span, "adjacent_transfer"),
    )
    artifacts = [_isolated(order) for order in permutations(raw)]
    assert len({generate_mapping_artifact_id(item) for item in artifacts}) == 1
    assert len({str(item.to_dict()) for item in artifacts}) == 1
    allocations = {
        item.contribution_relationship: item for item in artifacts[0].mappings
    }
    assert allocations[ContributionRelationship.PRIMARY].contribution_weight == 1.0
    secondary = allocations[ContributionRelationship.SECONDARY]
    assert secondary.contribution_weight == 0.3
    assert secondary.allocated_match_status == CurrentMatchStatus.ADJACENT
    assert secondary.allocated_review_required is True
    assert sum(item.contribution_weight for item in allocations.values()) <= 1.35


def test_exact_duplicate_is_kept_once_with_stable_rejection():
    _, _, _, _, span, dimensions = _context()
    candidate = _raw(dimensions[0], span)
    artifact = _isolated((candidate, deepcopy(candidate)))
    assert len(artifact.mappings) == 1
    assert artifact.validation_report is not None
    assert [item.reason_code for item in artifact.validation_report.rejections] == [
        MappingRejectionReason.EXACT_DUPLICATE_BINDING
    ]
    reversed_artifact = _isolated(
        (deepcopy(candidate), candidate), response_hash="sha256:" + "f" * 64
    )
    assert reversed_artifact.to_dict() == artifact.to_dict()
    assert generate_mapping_artifact_id(reversed_artifact) == generate_mapping_artifact_id(artifact)


def test_same_dimension_different_criteria_are_retained_but_contribute_once():
    _, _, _, _, span, dimensions = _context()
    dimension = next(item for item in dimensions if len(item.criterion_ids) >= 2)
    artifact = _isolated(
        (_raw(dimension, span, criterion_index=0), _raw(dimension, span, criterion_index=1))
    )
    assert len(artifact.mappings) == 2
    assert {item.contribution_relationship for item in artifact.mappings} == {
        ContributionRelationship.PRIMARY
    }
    recommendation = build_role_recommendation(
        profile=_context()[0], mapping_candidates=artifact,
        rubric=_context()[1], catalog=_context()[2], created_at=NOW,
    )
    role = next(item for item in recommendation.role_results if item.role_id == dimension.role_id)
    assessment = next(
        item for item in role.extended_current_fit.dimension_assessments
        if item.dimension_id == dimension.dimension_id
    )
    assert len(assessment.supporting_bindings) == 2
    assert role.extended_current_fit.assessed_count == 1


def test_ambiguous_primary_rejects_whole_group_and_three_dimensions_are_capped():
    _, _, _, _, span, dimensions = _context()
    ambiguous = _isolated((_raw(dimensions[0], span), _raw(dimensions[2], span)))
    assert not ambiguous.mappings
    assert {item.reason_code for item in ambiguous.validation_report.rejections} == {
        MappingRejectionReason.AMBIGUOUS_PRIMARY_ALLOCATION
    }

    limited_raw = (
        _raw(dimensions[0], span, "direct"),
        _raw(dimensions[1], span, "bounded_semantic"),
        _raw(dimensions[2], span, "adjacent_transfer"),
    )
    variants = [
        _isolated(order, response_hash="sha256:" + f"{index:064x}")
        for index, order in enumerate(permutations(limited_raw), 1)
    ]
    limited = variants[0]
    assert all(item.to_dict() == limited.to_dict() for item in variants[1:])
    assert len({item.dimension_id for item in limited.mappings}) <= 2
    assert any(
        item.reason_code == MappingRejectionReason.EVIDENCE_GROUP_DIMENSION_LIMIT
        for item in limited.validation_report.rejections
    )


def test_nonoverlapping_spans_are_independent_and_overlapping_spans_are_rejected():
    profile_data = synthetic_profile().to_dict()
    summary = "Built agent workflows; evaluated model quality."
    profile_data["experience_overview"][0]["short_factual_summary"] = summary
    profile = CareerProfile.from_dict(profile_data)
    rubric = production_capability_rubric()
    catalog = production_role_catalog()
    spans = tuple(
        item for item in canonical_evidence_span_inventory(profile)
        if item.path == "experience_overview[0].short_factual_summary"
    )
    assert len(spans) == 2
    dimensions = [
        item for item in rubric.role_dimensions("applied_ai_engineer")
        if EvidenceClass.PROJECT_SUMMARY in item.evidence_support_policy.allowed_evidence_classes
    ]
    payload = {
        "mappings": [_raw(dimensions[0], spans[0]), _raw(dimensions[2], spans[1])],
        "directional_signals": [], "constraints": [], "conflict_warnings": [],
    }
    independent = ProfileDimensionMappingCandidateSet.from_provider_payload_isolated(
        payload, profile=profile, rubric=rubric, catalog=catalog,
        provider_name="fixture", provider_model="fixture", attempt_number=1,
        response_hash_reference="sha256:" + "b" * 64,
        evidence_spans=spans, mapping_schema_version=4,
    )
    assert len(independent.mappings) == 2
    assert len({item.evidence_group_id for item in independent.mappings}) == 2

    overlapping = CanonicalEvidenceSpan(
        span_id=_canonical_evidence_span_id(
            profile, spans[0].path, 6, 30
        ),
        path=spans[0].path,
        start_offset=6,
        end_offset=30,
        text=summary[6:30],
    )
    overlap_payload = deepcopy(payload)
    overlap_payload["mappings"][1]["span_id"] = overlapping.span_id
    rejected = ProfileDimensionMappingCandidateSet.from_provider_payload_isolated(
        overlap_payload, profile=profile, rubric=rubric, catalog=catalog,
        provider_name="fixture", provider_model="fixture", attempt_number=1,
        response_hash_reference="sha256:" + "c" * 64,
        evidence_spans=(*spans, overlapping), mapping_schema_version=4,
    )
    assert not rejected.mappings
    assert {item.reason_code for item in rejected.validation_report.rejections} == {
        MappingRejectionReason.OVERLAPPING_ATOMIC_EVIDENCE
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("evidence_group_id", "evidence_group_fake"),
        ("contribution_relationship", "secondary"),
        ("contribution_weight", 0.9),
    ],
)
def test_mapping_four_typed_load_rejects_allocation_tampering(field, value):
    _, _, _, _, span, dimensions = _context()
    artifact = _isolated((_raw(dimensions[0], span),))
    payload = artifact.to_dict()
    payload["mappings"][0][field] = value
    with pytest.raises(Phase2ValidationError):
        ProfileDimensionMappingCandidateSet.from_dict(payload).validate(
            profile=_context()[0], rubric=_context()[1], catalog=_context()[2]
        )


def test_provider_cannot_inject_allocation_fields():
    _, _, _, _, span, dimensions = _context()
    raw = _raw(dimensions[0], span)
    raw["contribution_weight"] = 1.0
    artifact = _isolated((raw,))
    assert not artifact.mappings
    assert artifact.validation_report.rejections[0].reason_code == (
        MappingRejectionReason.PROVIDER_FIELD_INJECTION
    )


def test_mapping_four_provider_transport_does_not_expose_allocation_authority():
    profile, rubric, _, _, _, _ = _context()
    schema = provider_mapping_schema(rubric, profile, mapping_schema_version=4)
    properties = schema["properties"]["mappings"]["items"]["properties"]
    assert set(properties) == {
        "role_id", "dimension_id", "criterion_id", "span_id",
        "proposed_binding_type", "provider_confidence",
    }
    assert not {
        "evidence_group_id", "contribution_relationship", "contribution_weight",
        "allocated_match_status", "allocated_evidence_strength", "score", "rank",
    } & set(properties)


def test_recommendation_five_reallocates_after_primary_review_rejection():
    profile, rubric, catalog, _, span, dimensions = _context()
    mapping = _isolated(
        (
            _raw(dimensions[0], span, "direct"),
            _raw(dimensions[1], span, "adjacent_transfer"),
        )
    )
    primary = next(
        item for item in mapping.mappings
        if item.contribution_relationship == ContributionRelationship.PRIMARY
    )
    secondary = next(
        item for item in mapping.mappings
        if item.contribution_relationship == ContributionRelationship.SECONDARY
    )
    review = create_evidence_binding_review_artifact(
        profile=profile, rubric=rubric, mapping=mapping, catalog=catalog,
        decisions={
            primary.binding_id: (
                BindingReviewDecision.REJECTED,
                BindingReviewerType.PROFILE_OWNER,
                NOW,
            )
        },
    )
    recommendation = build_role_recommendation(
        profile=profile, mapping_candidates=mapping, rubric=rubric, catalog=catalog,
        created_at=NOW, evidence_reviews=review,
    )
    assert recommendation.schema_version == 5
    bindings = [
        binding
        for role in recommendation.role_results
        for assessment in role.extended_current_fit.dimension_assessments
        for binding in assessment.supporting_bindings
    ]
    promoted = next(item for item in bindings if item.binding_id == secondary.binding_id)
    assert isinstance(promoted, AllocatedProfileCriterionEvidenceBinding)
    assert promoted.contribution_relationship == ContributionRelationship.PRIMARY
    assert all(item.binding_id != primary.binding_id for item in bindings)
    recommendation.validate(
        profile=profile, rubric=rubric, catalog=catalog,
        mapping_candidates=mapping, evidence_reviews=review,
    )


def test_recommendation_five_typed_load_requires_mapping_four_context():
    profile, rubric, catalog, _, span, dimensions = _context()
    mapping = _isolated((_raw(dimensions[0], span),))
    artifact = build_role_recommendation(
        profile=profile, mapping_candidates=mapping, rubric=rubric, catalog=catalog,
        created_at=NOW,
    )
    assert artifact.schema_version == 5
    assert RoleRecommendationArtifact.from_dict(artifact.to_dict()) == artifact
    with pytest.raises(Phase2ValidationError, match="requires Mapping schema 4"):
        artifact.validate(profile=profile, rubric=rubric, catalog=catalog)
    artifact.validate(
        profile=profile, rubric=rubric, catalog=catalog, mapping_candidates=mapping
    )


def test_direct_construction_with_noncanonical_allocation_is_rejected():
    profile, rubric, catalog, _, span, dimensions = _context()
    artifact = _isolated((_raw(dimensions[0], span),))
    binding = artifact.mappings[0]
    forged = replace(binding, contribution_weight=0.3)
    forged_artifact = replace(artifact, mappings=(forged,))
    with pytest.raises(Phase2ValidationError):
        forged_artifact.validate(profile=profile, rubric=rubric, catalog=catalog)


class _SequentialClient:
    def __init__(self, payloads):
        self.calls = []
        self.payloads = iter(payloads)
        self.responses = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=next(self.payloads))


def test_ambiguous_allocation_retry_is_precise_private_and_limited_to_two_calls():
    profile, rubric, catalog, _, span, dimensions = _context()
    conflicting = [_raw(dimensions[0], span), _raw(dimensions[2], span)]
    corrected = [conflicting[0]]
    payload = lambda mappings: json.dumps(
        {"mappings": mappings, "directional_signals": [], "constraints": [], "conflict_warnings": []}
    )
    client = _SequentialClient([payload(conflicting), payload(corrected)])
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings(api_key="secret-key", model="fixture", base_url=None),
        client=client,
        max_attempts=2,
        mapping_schema_version=4,
    )
    result = mapper.map(profile, rubric, catalog)
    assert len(client.calls) == 2
    assert len(result.mappings) == 1
    retry_prompt = client.calls[1]["instructions"]
    assert "Keep only the single Dimension" in retry_prompt
    diagnostics = json.dumps(mapper.attempt_diagnostics)
    assert "secret-key" not in diagnostics
    assert span.text not in diagnostics
