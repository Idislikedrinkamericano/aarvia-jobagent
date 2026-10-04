from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from aarvia.capability_rubric import (
    DimensionReadiness,
    EvidenceClass,
    production_capability_rubric,
)
from aarvia.cli import _print_recommendation
from aarvia.evidence_binding_review import (
    BindingReviewDecision,
    BindingReviewerType,
    create_evidence_binding_review_artifact,
)
from aarvia.profile import CareerProfile
from aarvia.profile_dimension_mapping import (
    ProfileDimensionMappingCandidateSet,
    canonical_evidence_span_inventory,
)
from aarvia.role_catalog import Phase2ValidationError, production_role_catalog
from aarvia.role_recommendation import (
    RecommendationRankingTier,
    RoleRecommendationArtifact,
    _final_ranking,
    _global_ranking_v7,
    build_role_recommendation,
    generate_recommendation_id,
)
from recommendation_fixtures import synthetic_profile


NOW = "2026-10-03T00:00:00+00:00"


def _profile_with_project_evidence(count: int = 8) -> CareerProfile:
    data = synthetic_profile().to_dict()
    data["experience_overview"] = [
        {
            "experience_type": "project",
            "organization_or_project_name": f"Synthetic Ranking Project {index}",
            "title_or_role": "Developer",
            "short_factual_summary": (
                f"Implemented and evaluated independent synthetic capability {index}."
            ),
            "start_date": "2025-01",
            "end_date": "2025-02",
        }
        for index in range(count)
    ]
    return CareerProfile.from_dict(data)


def _ranking_fixture(
    *,
    include_research: bool = False,
    reverse: bool = False,
):
    profile = _profile_with_project_evidence()
    rubric = production_capability_rubric()
    catalog = production_role_catalog()
    spans = [
        item for item in canonical_evidence_span_inventory(profile)
        if item.path.endswith("short_factual_summary")
    ]
    applied = [
        item for item in rubric.role_dimensions("applied_ai_engineer")
        if EvidenceClass.PROJECT_SUMMARY
        in item.evidence_support_policy.allowed_evidence_classes
    ][:3]
    machine_learning = [
        item for item in rubric.role_dimensions("machine_learning_engineer")
        if item.readiness == DimensionReadiness.READY
        and EvidenceClass.PROJECT_SUMMARY
        in item.evidence_support_policy.allowed_evidence_classes
    ][:3 if include_research else 2]
    research = [
        item for item in rubric.role_dimensions("research_engineer")
        if item.readiness == DimensionReadiness.READY
        and EvidenceClass.PROJECT_SUMMARY
        in item.evidence_support_policy.allowed_evidence_classes
    ][:2] if include_research else []
    dimensions = [*applied, *machine_learning, *research]
    assert len(dimensions) == (8 if include_research else 5)
    rows = [
        {
            "role_id": dimension.role_id,
            "dimension_id": dimension.dimension_id,
            "criterion_id": dimension.criterion_ids[0],
            "span_id": span.span_id,
            "proposed_binding_type": "direct",
            "provider_confidence": "high",
        }
        for dimension, span in zip(dimensions, spans[:len(dimensions)], strict=True)
    ]
    if reverse:
        rows.reverse()
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
        response_hash_reference="sha256:" + "9" * 64,
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
    artifact = build_role_recommendation(
        profile=profile,
        mapping_candidates=mapping,
        rubric=rubric,
        catalog=catalog,
        created_at=NOW,
        evidence_reviews=reviews,
    )
    return profile, rubric, catalog, mapping, reviews, artifact


def _role(artifact: RoleRecommendationArtifact, role_id: str):
    return next(item for item in artifact.role_results if item.role_id == role_id)


def test_v15_shape_uses_one_global_tiered_ranking():
    _, _, _, _, _, artifact = _ranking_fixture()
    applied = _role(artifact, "applied_ai_engineer")
    machine_learning = _role(artifact, "machine_learning_engineer")
    research = _role(artifact, "research_engineer")

    assert artifact.schema_version == 7
    assert artifact.mapping_schema_version == 5
    assert (
        machine_learning.ranking_tier,
        machine_learning.rank,
        machine_learning.provisional,
    ) == (RecommendationRankingTier.CORE_SUPPORTED, 1, False)
    assert (
        applied.ranking_tier,
        applied.rank,
        applied.provisional,
    ) == (RecommendationRankingTier.EXTENDED_ONLY, 2, True)
    assert (research.ranking_tier, research.rank) == (
        RecommendationRankingTier.UNRANKED,
        None,
    )
    assert artifact.tie_groups == ()
    assert all(not item.tied_role_ids for item in artifact.role_results)
    assert [item.role_id for item in artifact.role_results] == [
        "machine_learning_engineer",
        "applied_ai_engineer",
        "research_engineer",
    ]


def test_global_competition_ranking_ties_only_within_one_tier():
    _, _, _, _, _, artifact = _ranking_fixture(include_research=True)
    machine_learning = _role(artifact, "machine_learning_engineer")
    research = _role(artifact, "research_engineer")
    applied = _role(artifact, "applied_ai_engineer")

    assert machine_learning.ranking_tier == research.ranking_tier == (
        RecommendationRankingTier.CORE_SUPPORTED
    )
    assert machine_learning.rank == research.rank == 1
    assert machine_learning.tied_role_ids == (research.role_id,)
    assert research.tied_role_ids == (machine_learning.role_id,)
    assert artifact.tie_groups == (
        tuple(sorted((machine_learning.role_id, research.role_id))),
    )
    assert applied.ranking_tier == RecommendationRankingTier.EXTENDED_ONLY
    assert applied.rank == 3
    assert applied.tied_role_ids == ()


def test_non_tied_roles_in_same_tier_receive_unique_global_ranks():
    _, _, _, _, _, artifact = _ranking_fixture(include_research=True)
    machine_learning = _role(artifact, "machine_learning_engineer")
    stronger_machine_learning = replace(
        machine_learning,
        core_current_fit=replace(machine_learning.core_current_fit, score=75.0),
    )
    roles = tuple(
        stronger_machine_learning if item.role_id == machine_learning.role_id else item
        for item in artifact.role_results
    )

    ranks, tiers, tie_groups = _global_ranking_v7(roles)
    assert tiers["machine_learning_engineer"] == tiers["research_engineer"] == (
        RecommendationRankingTier.CORE_SUPPORTED
    )
    assert ranks["machine_learning_engineer"] == 1
    assert ranks["research_engineer"] == 2
    assert ranks["applied_ai_engineer"] == 3
    assert tie_groups == ()


def test_schema_seven_is_order_independent_including_artifact_id():
    *_, first = _ranking_fixture()
    *_, second = _ranking_fixture(reverse=True)
    assert first.to_dict() == second.to_dict()
    assert first.recommendation_set_id == second.recommendation_set_id


@pytest.mark.parametrize("field", ["ranking_tier", "rank", "tied_role_ids"])
def test_schema_seven_typed_load_rejects_ranking_tampering(field: str):
    profile, rubric, catalog, mapping, reviews, artifact = _ranking_fixture()
    raw = artifact.to_dict()
    applied = next(
        item for item in raw["role_results"]
        if item["role_id"] == "applied_ai_engineer"
    )
    mutations = {
        "ranking_tier": "core_supported",
        "rank": 1,
        "tied_role_ids": ["machine_learning_engineer"],
    }
    applied[field] = mutations[field]
    value = RoleRecommendationArtifact.from_dict(raw)
    with pytest.raises(Phase2ValidationError):
        value.validate(
            profile=profile,
            rubric=rubric,
            catalog=catalog,
            mapping_candidates=mapping,
            evidence_reviews=reviews,
        )


def test_schema_seven_rejects_cross_tier_tie_and_nondeterministic_order():
    profile, rubric, catalog, mapping, reviews, artifact = _ranking_fixture()
    raw = artifact.to_dict()
    raw["tie_groups"] = [
        ["applied_ai_engineer", "machine_learning_engineer"]
    ]
    with pytest.raises(Phase2ValidationError):
        RoleRecommendationArtifact.from_dict(raw).validate(
            profile=profile,
            rubric=rubric,
            catalog=catalog,
            mapping_candidates=mapping,
            evidence_reviews=reviews,
        )

    reversed_value = replace(
        artifact,
        role_results=tuple(reversed(artifact.role_results)),
    )
    with pytest.raises(Phase2ValidationError, match="rank order"):
        reversed_value.validate(
            profile=profile,
            rubric=rubric,
            catalog=catalog,
            mapping_candidates=mapping,
            evidence_reviews=reviews,
        )


def test_schema_seven_artifact_id_covers_ranking_tier():
    profile, rubric, catalog, mapping, reviews, artifact = _ranking_fixture()
    applied = _role(artifact, "applied_ai_engineer")
    tampered_role = replace(
        applied, ranking_tier=RecommendationRankingTier.CORE_SUPPORTED
    )
    tampered = replace(
        artifact,
        role_results=tuple(
            tampered_role if item.role_id == applied.role_id else item
            for item in artifact.role_results
        ),
    )
    with pytest.raises(Phase2ValidationError):
        tampered.validate(
            profile=profile,
            rubric=rubric,
            catalog=catalog,
            mapping_candidates=mapping,
            evidence_reviews=reviews,
        )


def test_schema_six_keeps_legacy_wire_and_duplicate_fallback_ranks():
    profile, rubric, catalog, mapping, reviews, current = _ranking_fixture()
    legacy_roles = tuple(replace(item, ranking_tier=None) for item in current.role_results)
    legacy_ranks, legacy_groups = _final_ranking(legacy_roles)
    legacy_roles = tuple(
        sorted(
            (
                replace(
                    item,
                    rank=legacy_ranks[item.role_id],
                    tied_role_ids=tuple(
                        sorted(
                            role
                            for group in legacy_groups
                            if item.role_id in group
                            for role in group
                            if role != item.role_id
                        )
                    ),
                )
                for item in legacy_roles
            ),
            key=lambda item: ((item.rank or 999), item.role_id),
        )
    )
    legacy_id = generate_recommendation_id(
        profile_fingerprint=current.profile_fingerprint,
        rubric_version=current.rubric_version,
        catalog_version=current.catalog_version,
        provider_name=current.provider_name,
        provider_model=current.provider_model,
        role_results=legacy_roles,
        schema_version=6,
        evidence_review_artifact_id=current.evidence_review_artifact_id,
        allocation_review_artifact_id=current.allocation_review_artifact_id,
        unresolved_evidence_group_ids=current.unresolved_evidence_group_ids,
    )
    legacy = replace(
        current,
        recommendation_set_id=legacy_id,
        role_results=legacy_roles,
        tie_groups=legacy_groups,
        schema_version=6,
    )
    legacy.validate(
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        mapping_candidates=mapping,
        evidence_reviews=reviews,
    )
    raw = legacy.to_dict()
    assert all("ranking_tier" not in item for item in raw["role_results"])
    loaded = RoleRecommendationArtifact.from_dict(raw)
    assert loaded.to_dict() == raw
    assert _role(loaded, "applied_ai_engineer").rank == 1
    assert _role(loaded, "machine_learning_engineer").rank == 1
    assert loaded.tie_groups == ()

    injected = legacy.to_dict()
    injected["role_results"][0]["ranking_tier"] = "core_supported"
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        RoleRecommendationArtifact.from_dict(injected)


def test_schema_seven_requires_ranking_tier_and_does_not_accept_schema_six_mix():
    _, _, _, _, _, artifact = _ranking_fixture()
    raw = artifact.to_dict()
    del raw["role_results"][0]["ranking_tier"]
    with pytest.raises(Phase2ValidationError):
        RoleRecommendationArtifact.from_dict(raw)

    wrong_version = artifact.to_dict()
    wrong_version["schema_version"] = 6
    with pytest.raises(Phase2ValidationError):
        RoleRecommendationArtifact.from_dict(wrong_version)


def test_cli_displays_ranking_tier_and_tie_context():
    _, _, _, mapping, _, artifact = _ranking_fixture(include_research=True)
    output: list[str] = []
    _print_recommendation(
        artifact,
        mapping,
        output_path=Path("recommendation.json"),
        output_fn=output.append,
    )
    assert any("Rank 1: Machine Learning Engineer - Core evidence" in line for line in output)
    assert any("Rank 3: Applied Ai Engineer (provisional) - Extended evidence only" in line for line in output)
    assert any(line.startswith("  Tied with:") for line in output)
