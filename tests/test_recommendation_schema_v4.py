from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from aarvia.capability_rubric import (
    CapabilityRubric,
    EvidenceClass,
    EvidenceStatusCap,
    production_capability_rubric,
)
from aarvia.profile import CareerProfile
from aarvia.profile_dimension_mapping import (
    CurrentMatchStatus,
    EvidenceStrength,
    EvidenceTrustLevel,
    ProfileDimensionMappingCandidateSet,
    canonical_evidence_span_inventory,
)
from aarvia.role_catalog import Phase2ValidationError, production_role_catalog
from aarvia.role_recommendation import (
    FitBand,
    RecommendationBlockerCode,
    RecommendationConfidence,
    RoleRecommendationArtifact,
    build_role_recommendation,
    load_role_recommendation,
    save_role_recommendation,
)
from recommendation_fixtures import mapping_set, synthetic_profile


NOW = "2026-09-27T12:00:00+00:00"


def _profile() -> CareerProfile:
    data = synthetic_profile().to_dict()
    data["skills"][0]["self_reported_proficiency"] = "intermediate"
    for index in range(1, 45):
        data["experience_overview"].append(
            {
                "experience_type": "project" if index % 2 else "internship",
                "organization_or_project_name": f"Example Evidence {index}",
                "title_or_role": "Contributor",
                "short_factual_summary": f"Implemented verified capability example {index}.",
                "start_date": "2025-01",
                "end_date": "2025-02",
            }
        )
    return CareerProfile.from_dict(data)


def _span(profile: CareerProfile, path: str):
    matches = [
        item for item in canonical_evidence_span_inventory(profile) if item.path == path
    ]
    assert len(matches) == 1
    return matches[0]


def _dimension(
    rubric: CapabilityRubric,
    *,
    evidence_class: EvidenceClass,
    provisional_cap: EvidenceStatusCap | None = None,
):
    for dimension in rubric.dimensions:
        policy = dimension.evidence_support_policy
        assert policy is not None
        if (
            evidence_class in policy.allowed_evidence_classes
            and (
                provisional_cap is None
                or policy.provisional_status_cap == provisional_cap
            )
        ):
            return dimension
    raise AssertionError("no matching Dimension")


def _binding(dimension, span, **overrides):
    value = {
        "role_id": dimension.role_id,
        "dimension_id": dimension.dimension_id,
        "criterion_id": dimension.criterion_ids[0],
        "span_id": span.span_id,
        "proposed_binding_type": "direct",
        "provider_confidence": "high",
    }
    value.update(overrides)
    return value


def _mapping(profile: CareerProfile, *bindings):
    return ProfileDimensionMappingCandidateSet.from_provider_payload_v3(
        {
            "mappings": list(bindings),
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


def _build(profile: CareerProfile, *bindings):
    return build_role_recommendation(
        profile=profile,
        mapping_candidates=_mapping(profile, *bindings),
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
        created_at=NOW,
    )


def _role(artifact, role_id):
    return next(item for item in artifact.role_results if item.role_id == role_id)


def _assessment(artifact, dimension_id):
    return next(
        item
        for role in artifact.role_results
        for item in role.extended_current_fit.dimension_assessments
        if item.dimension_id == dimension_id
    )


def test_schema_four_aggregates_structural_evidence_at_policy_cap() -> None:
    profile = _profile(); rubric = production_capability_rubric()
    dimension = _dimension(rubric, evidence_class=EvidenceClass.SKILL_NAME)
    artifact = _build(
        profile,
        _binding(dimension, _span(profile, "skills[0].skill_name")),
    )
    assessment = _assessment(artifact, dimension.dimension_id)
    expected = dimension.evidence_support_policy.structural_caps[EvidenceClass.SKILL_NAME]
    assert artifact.schema_version == 4
    assert artifact.mapping_schema_version == 3
    assert assessment.status.value == expected.value
    assert assessment.evidence_strength == EvidenceStrength.WEAK
    assert assessment.supporting_bindings[0].trust_level == EvidenceTrustLevel.STRUCTURAL_ONLY
    assert assessment.review_required is False
    assert assessment.status != CurrentMatchStatus.DEMONSTRATED


def test_schema_four_provisional_evidence_is_partial_and_review_required() -> None:
    profile = _profile(); rubric = production_capability_rubric()
    dimension = _dimension(
        rubric,
        evidence_class=EvidenceClass.PROJECT_SUMMARY,
        provisional_cap=EvidenceStatusCap.PARTIALLY_DEMONSTRATED,
    )
    artifact = _build(
        profile,
        _binding(
            dimension,
            _span(profile, "experience_overview[1].short_factual_summary"),
        ),
    )
    assessment = _assessment(artifact, dimension.dimension_id)
    role = _role(artifact, dimension.role_id)
    assert assessment.status == CurrentMatchStatus.PARTIAL
    assert assessment.evidence_strength == EvidenceStrength.SUPPORTING
    assert assessment.review_required is True
    assert assessment.supporting_bindings[0].trust_level == EvidenceTrustLevel.PROVISIONAL_SEMANTIC
    assert role.provisional is True


def test_conservative_dimension_remains_adjacent_with_provisional_evidence() -> None:
    profile = _profile(); rubric = production_capability_rubric()
    dimension = _dimension(
        rubric,
        evidence_class=EvidenceClass.PROJECT_SUMMARY,
        provisional_cap=EvidenceStatusCap.ADJACENT_TRANSFERABLE,
    )
    artifact = _build(
        profile,
        _binding(
            dimension,
            _span(profile, "experience_overview[1].short_factual_summary"),
        ),
    )
    assessment = _assessment(artifact, dimension.dimension_id)
    assert assessment.status == CurrentMatchStatus.ADJACENT
    assert assessment.evidence_strength == EvidenceStrength.WEAK
    assert assessment.review_required is True


def test_no_unconfirmed_binding_can_produce_demonstrated_or_strong() -> None:
    profile = _profile(); rubric = production_capability_rubric()
    bindings = []
    evidence_index = 1
    for dimension in rubric.dimensions:
        policy = dimension.evidence_support_policy
        assert policy is not None
        evidence_class = (
            EvidenceClass.PROJECT_SUMMARY
            if EvidenceClass.PROJECT_SUMMARY in policy.allowed_evidence_classes
            else EvidenceClass.EXPERIENCE_SUMMARY
        )
        if evidence_class not in policy.allowed_evidence_classes:
            bindings.append(
                _binding(dimension, _span(profile, f"skills[{evidence_index}].skill_name"))
            )
        else:
            if evidence_class == EvidenceClass.PROJECT_SUMMARY and evidence_index % 2 == 0:
                evidence_index += 1
            if evidence_class == EvidenceClass.EXPERIENCE_SUMMARY and evidence_index % 2 == 1:
                evidence_index += 1
            bindings.append(
                _binding(
                    dimension,
                    _span(
                        profile,
                        f"experience_overview[{evidence_index}].short_factual_summary",
                    ),
                )
            )
        evidence_index += 1
    artifact = _build(profile, *bindings)
    assessments = [
        item
        for role in artifact.role_results
        for item in role.extended_current_fit.dimension_assessments
    ]
    assert all(item.status != CurrentMatchStatus.DEMONSTRATED for item in assessments)
    assert all(
        role.core_current_fit.band != FitBand.STRONG
        and role.extended_current_fit.band != FitBand.STRONG
        for role in artifact.role_results
    )
    assert all(
        RecommendationBlockerCode.CONFIRMED_EVIDENCE_REQUIRED.value in role.blockers
        for role in artifact.role_results
    )


def test_schema_four_recomputes_coverage_confidence_ranking_and_ties() -> None:
    profile = _profile(); rubric = production_capability_rubric()
    role_id = "machine_learning_engineer"
    ready = [
        item
        for item in rubric.role_dimensions(role_id)
        if item.readiness.value == "ready_for_profile_matching"
    ]
    usable = [
        item
        for item in ready
        if EvidenceClass.PROJECT_SUMMARY
        in item.evidence_support_policy.allowed_evidence_classes
    ]
    bindings = [
        _binding(
            dimension,
            _span(profile, f"experience_overview[{index}].short_factual_summary"),
        )
        for index, dimension in zip((1, 3, 5), usable[:3], strict=False)
    ]
    artifact = _build(profile, *bindings)
    role = _role(artifact, role_id)
    expected_assessed = len(bindings)
    assert role.core_current_fit.assessed_count == expected_assessed
    assert role.core_current_fit.coverage == round(expected_assessed / len(ready), 2)
    artifact.validate(
        profile=profile, rubric=rubric, catalog=production_role_catalog()
    )
    changed_role = replace(role, rank=99)
    tampered = replace(
        artifact,
        role_results=tuple(
            changed_role if item.role_id == role_id else item
            for item in artifact.role_results
        ),
    )
    with pytest.raises(Phase2ValidationError, match="ranks"):
        tampered.validate(
            profile=profile, rubric=rubric, catalog=production_role_catalog()
        )


def test_schema_four_typed_round_trip_and_tampering_rejection(tmp_path: Path) -> None:
    profile = _profile(); rubric = production_capability_rubric(); catalog = production_role_catalog()
    dimension = _dimension(
        rubric,
        evidence_class=EvidenceClass.PROJECT_SUMMARY,
        provisional_cap=EvidenceStatusCap.PARTIALLY_DEMONSTRATED,
    )
    artifact = _build(
        profile,
        _binding(
            dimension,
            _span(profile, "experience_overview[1].short_factual_summary"),
        ),
    )
    first = save_role_recommendation(
        artifact,
        tmp_path / "one.json",
        profile=profile,
        rubric=rubric,
        catalog=catalog,
    )
    second = save_role_recommendation(
        artifact,
        tmp_path / "two.json",
        profile=profile,
        rubric=rubric,
        catalog=catalog,
    )
    assert first.read_bytes() == second.read_bytes()
    assert load_role_recommendation(
        first, profile=profile, rubric=rubric, catalog=catalog
    ) == artifact

    base = json.loads(first.read_text(encoding="utf-8"))
    target = next(
        item
        for role in base["role_results"]
        for item in role["extended_current_fit"]["dimension_assessments"]
        if item["supporting_bindings"]
    )
    mutations = {
        "status": "demonstrated",
        "evidence_strength": "strong",
        "review_required": False,
    }
    for field, value in mutations.items():
        payload = deepcopy(base)
        changed = next(
            item
            for role in payload["role_results"]
            for item in role["extended_current_fit"]["dimension_assessments"]
            if item["supporting_bindings"]
        )
        changed[field] = value
        path = tmp_path / f"tampered-{field}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(Phase2ValidationError):
            load_role_recommendation(
                path, profile=profile, rubric=rubric, catalog=catalog
            )

    payload = deepcopy(base)
    binding = next(
        item
        for role in payload["role_results"]
        for assessment in role["extended_current_fit"]["dimension_assessments"]
        for item in assessment["supporting_bindings"]
    )
    binding["derived_match_status"] = "demonstrated"
    path = tmp_path / "tampered-binding.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(Phase2ValidationError):
        load_role_recommendation(path, profile=profile, rubric=rubric, catalog=catalog)


def test_recommendation_schema_versions_are_explicit_and_cross_mix_is_rejected() -> None:
    profile = _profile(); rubric = production_capability_rubric(); catalog = production_role_catalog()
    dimension = _dimension(rubric, evidence_class=EvidenceClass.SKILL_NAME)
    artifact = _build(
        profile, _binding(dimension, _span(profile, "skills[0].skill_name"))
    )
    assert RoleRecommendationArtifact.from_dict(artifact.to_dict()).to_dict() == artifact.to_dict()
    for schema_version, mapping_version in ((3, 3), (4, 2), (2, 3)):
        with pytest.raises(Phase2ValidationError, match="mapping schema version"):
            RoleRecommendationArtifact.from_dict(
                {
                    **artifact.to_dict(),
                    "schema_version": schema_version,
                    "mapping_schema_version": mapping_version,
                }
            )
    with pytest.raises(Phase2ValidationError, match="schema versions"):
        replace(artifact, mapping_schema_version=2).validate(
            profile=profile, rubric=rubric, catalog=catalog
        )
    rubric_v1 = CapabilityRubric.from_dict(
        json.loads(
            Path("src/aarvia/catalog_data/role-capability-rubric-1.0.0.json").read_text(
                encoding="utf-8"
            )
        )
    )
    with pytest.raises(Phase2ValidationError, match="Rubric schema 2"):
        artifact.validate(profile=profile, rubric=rubric_v1, catalog=catalog)

    legacy = mapping_set()
    legacy_artifact = build_role_recommendation(
        profile=synthetic_profile(),
        mapping_candidates=legacy,
        rubric=rubric,
        catalog=catalog,
        created_at=NOW,
    )
    assert legacy_artifact.schema_version == 3
    assert legacy_artifact.mapping_schema_version == 2


@pytest.mark.parametrize(
    "field",
    [
        "match_status", "evidence_strength", "review_required", "score",
        "confidence", "rank", "blocker", "follow_up", "decision",
    ],
)
def test_provider_final_fields_cannot_enter_schema_four(field: str) -> None:
    profile = _profile(); rubric = production_capability_rubric()
    dimension = _dimension(rubric, evidence_class=EvidenceClass.SKILL_NAME)
    raw = _binding(dimension, _span(profile, "skills[0].skill_name"))
    raw[field] = "injected"
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        _mapping(profile, raw)


def test_confidence_and_blocker_tampering_are_recomputed() -> None:
    profile = _profile(); rubric = production_capability_rubric(); catalog = production_role_catalog()
    dimension = _dimension(rubric, evidence_class=EvidenceClass.SKILL_NAME)
    artifact = _build(
        profile, _binding(dimension, _span(profile, "skills[0].skill_name"))
    )
    role = _role(artifact, dimension.role_id)
    forged_confidence = replace(
        role.recommendation_confidence,
        result=RecommendationConfidence.HIGH,
    )
    forged_role = replace(
        role,
        recommendation_confidence=forged_confidence,
        blockers=(),
    )
    forged = replace(
        artifact,
        role_results=tuple(
            forged_role if item.role_id == role.role_id else item
            for item in artifact.role_results
        ),
        blockers=(),
    )
    with pytest.raises(Phase2ValidationError):
        forged.validate(profile=profile, rubric=rubric, catalog=catalog)

    forged_axis = replace(role.core_current_fit, band=FitBand.STRONG)
    forged_role = replace(role, core_current_fit=forged_axis)
    forged = replace(
        artifact,
        role_results=tuple(
            forged_role if item.role_id == role.role_id else item
            for item in artifact.role_results
        ),
    )
    with pytest.raises(Phase2ValidationError, match="band"):
        forged.validate(profile=profile, rubric=rubric, catalog=catalog)


def test_typed_recommendation_cannot_drop_skill_name_companion() -> None:
    profile = _profile(); rubric = production_capability_rubric(); catalog = production_role_catalog()
    dimension = _dimension(rubric, evidence_class=EvidenceClass.SKILL_PROFICIENCY)
    artifact = _build(
        profile,
        _binding(dimension, _span(profile, "skills[0].skill_name")),
        _binding(
            dimension,
            _span(profile, "skills[0].self_reported_proficiency"),
        ),
    )
    role = _role(artifact, dimension.role_id)

    def alter_axis(axis):
        assessments = []
        for assessment in axis.dimension_assessments:
            if assessment.dimension_id != dimension.dimension_id:
                assessments.append(assessment)
                continue
            proficiency_only = tuple(
                item
                for item in assessment.supporting_bindings
                if item.evidence_class == EvidenceClass.SKILL_PROFICIENCY
            )
            assessments.append(
                replace(assessment, supporting_bindings=proficiency_only)
            )
        return replace(axis, dimension_assessments=tuple(assessments))

    forged_role = replace(
        role,
        core_current_fit=alter_axis(role.core_current_fit),
        extended_current_fit=alter_axis(role.extended_current_fit),
    )
    forged = replace(
        artifact,
        role_results=tuple(
            forged_role if item.role_id == role.role_id else item
            for item in artifact.role_results
        ),
    )
    with pytest.raises(Phase2ValidationError, match="skill-name binding"):
        forged.validate(profile=profile, rubric=rubric, catalog=catalog)
