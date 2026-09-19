from copy import deepcopy
import json

import pytest

from aarvia.capability_rubric import production_capability_rubric
from aarvia.profile_dimension_mapping import ProfileDimensionMappingCandidateSet
from aarvia.role_catalog import Phase2ValidationError, production_role_catalog
from aarvia.role_recommendation import (
    FitBand,
    RecommendationConfidence,
    RoleRecommendationArtifact,
    build_role_recommendation,
    load_any_recommendation,
    load_role_recommendation,
    migrate_recommendation_v1_to_v2,
    save_role_recommendation,
)
from phase2_fixtures import catalog_fixture, profile_fixture
from test_career_direction import recommendation_data
from aarvia.career_direction import ProfileReference, RecommendationSet, save_recommendation_set
from recommendation_fixtures import mapping_payload, mapping_set, synthetic_profile


NOW = "2026-09-19T12:00:00+00:00"


def parsed(payload: dict) -> ProfileDimensionMappingCandidateSet:
    return ProfileDimensionMappingCandidateSet.from_provider_payload(
        payload,
        profile=synthetic_profile(),
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
        provider_name="fixture",
        provider_model="fixture-model",
    )


def build(payload: dict | None = None):
    return build_role_recommendation(
        profile=synthetic_profile(),
        mapping_candidates=parsed(payload or mapping_payload()),
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
        created_at=NOW,
    )


def result(artifact, role):
    return next(item for item in artifact.role_results if item.role_id == role)


def test_current_fit_uses_approved_status_formula() -> None:
    payload = mapping_payload(all_demonstrated=True)
    ready = [d for d in production_capability_rubric().role_dimensions("machine_learning_engineer") if d.readiness.value == "ready_for_profile_matching"]
    statuses = ["demonstrated", "partially_demonstrated", "adjacent_transferable", "not_demonstrated"]
    for dimension, status in zip(ready, statuses):
        next(x for x in payload["mappings"] if x["dimension_id"] == dimension.dimension_id)["match_status"] = status
    axis = result(build(payload), "machine_learning_engineer").core_current_fit
    assert axis.score == 47.5
    assert axis.coverage == 1.0
    assert axis.band == FitBand.EMERGING


def test_unknown_is_excluded_from_mean_but_reduces_coverage() -> None:
    payload = mapping_payload(all_demonstrated=True)
    ready = [d for d in production_capability_rubric().role_dimensions("machine_learning_engineer") if d.readiness.value == "ready_for_profile_matching"]
    for dimension in ready[1:]:
        item = next(x for x in payload["mappings"] if x["dimension_id"] == dimension.dimension_id)
        item["match_status"] = "unknown"; item["profile_fact_references"] = []
    axis = result(build(payload), "machine_learning_engineer").core_current_fit
    assert axis.score == 100.0
    assert axis.coverage == 0.25
    assert axis.band == FitBand.INSUFFICIENT


def test_not_applicable_is_removed_from_denominator() -> None:
    payload = mapping_payload(all_demonstrated=True)
    ready = [d for d in production_capability_rubric().role_dimensions("machine_learning_engineer") if d.readiness.value == "ready_for_profile_matching"]
    item = next(x for x in payload["mappings"] if x["dimension_id"] == ready[-1].dimension_id)
    item["match_status"] = "not_applicable"; item["profile_fact_references"] = []
    axis = result(build(payload), "machine_learning_engineer").core_current_fit
    assert axis.assessed_count == axis.applicable_count == 3
    assert axis.score == 100.0


@pytest.mark.parametrize(
    ("assessed", "expected"),
    [(1, FitBand.INSUFFICIENT), (2, FitBand.EMERGING), (3, FitBand.STRONG)],
)
def test_coverage_gates_strong_result(assessed, expected) -> None:
    payload = mapping_payload(all_demonstrated=True)
    ready = [d for d in production_capability_rubric().role_dimensions("machine_learning_engineer") if d.readiness.value == "ready_for_profile_matching"]
    for dimension in ready[assessed:]:
        item = next(x for x in payload["mappings"] if x["dimension_id"] == dimension.dimension_id)
        item["match_status"] = "unknown"; item["profile_fact_references"] = []
    assert result(build(payload), "machine_learning_engineer").core_current_fit.band == expected


def test_empty_assessed_dimensions_do_not_divide_by_zero() -> None:
    artifact = build({"mappings": [], "directional_signals": mapping_payload()["directional_signals"], "constraints": mapping_payload()["constraints"], "conflict_warnings": []})
    axis = result(artifact, "machine_learning_engineer").core_current_fit
    assert axis.score is None and axis.band == FitBand.INSUFFICIENT


def test_equal_dimension_weighting_normalizes_different_dimension_counts() -> None:
    artifact = build(mapping_payload(all_demonstrated=True))
    mle = result(artifact, "machine_learning_engineer").extended_current_fit
    research = result(artifact, "research_engineer").extended_current_fit
    assert (mle.applicable_count, research.applicable_count) == (7, 6)
    assert mle.score == research.score == 100.0


def test_directional_fit_has_four_equal_signals_and_does_not_change_current_fit() -> None:
    payload = mapping_payload()
    before = result(build(payload), "applied_ai_engineer").extended_current_fit
    for signal in payload["directional_signals"]:
        if signal["role_id"] == "applied_ai_engineer":
            signal["status"] = "misaligned"
    after = result(build(payload), "applied_ai_engineer")
    assert after.directional_fit.assessed_count == 4
    assert after.directional_fit.score == 0.0
    assert after.extended_current_fit == before


def test_directional_unknown_lowers_coverage_without_becoming_misaligned() -> None:
    payload = mapping_payload()
    signals = [x for x in payload["directional_signals"] if x["role_id"] == "research_engineer"]
    for signal in signals[1:]:
        signal["status"] = "unknown"; signal["profile_fact_references"] = []
    axis = result(build(payload), "research_engineer").directional_fit
    assert axis.score == 100.0 and axis.coverage == 0.25 and axis.band == FitBand.INSUFFICIENT


def test_core_and_extended_results_are_separate_and_conditional_strong_is_prevented() -> None:
    applied = result(build(mapping_payload(all_demonstrated=True)), "applied_ai_engineer")
    assert applied.core_current_fit.band == FitBand.INSUFFICIENT
    assert applied.extended_current_fit.score == 100.0
    assert applied.extended_current_fit.band == FitBand.MODERATE
    assert applied.extended_current_fit.band_cap_reason == "conditional_only_strong_prevention"
    assert applied.provisional is True


def test_unknown_constraint_caps_confidence_and_adds_follow_up() -> None:
    artifact = build(mapping_payload(all_demonstrated=True, unknown_constraints=True))
    assert all(item.recommendation_confidence.result in {RecommendationConfidence.LOW, RecommendationConfidence.INSUFFICIENT} for item in artifact.role_results)
    assert artifact.follow_up_questions
    assert artifact.follow_up_questions[0].constraint_id == "work_authorization"


def test_incompatible_role_is_shown_but_unranked() -> None:
    payload = mapping_payload(all_demonstrated=True)
    constraint = next(x for x in payload["constraints"] if x["role_id"] == "research_engineer")
    constraint["status"] = "incompatible"
    artifact = build(payload)
    research = result(artifact, "research_engineer")
    assert len(artifact.role_results) == 3
    assert research.rank is None
    assert "constraint_incompatible" in research.blockers


def test_near_roles_tie_and_role_id_only_stabilizes_display() -> None:
    artifact = build(mapping_payload(all_demonstrated=True))
    mle = result(artifact, "machine_learning_engineer")
    research = result(artifact, "research_engineer")
    assert mle.rank == research.rank
    assert research.role_id in mle.tied_role_ids
    assert [x.role_id for x in artifact.role_results] == sorted([x.role_id for x in artifact.role_results], key=lambda role: (result(artifact, role).rank or 999, role))


def test_roles_more_than_five_points_apart_do_not_tie() -> None:
    payload = mapping_payload(all_demonstrated=True)
    for item in payload["mappings"]:
        if item["role_id"] == "research_engineer":
            item["match_status"] = "partially_demonstrated"
        if item["role_id"] == "applied_ai_engineer":
            item["match_status"] = "not_demonstrated"
    artifact = build(payload)
    mle = result(artifact, "machine_learning_engineer")
    research = result(artifact, "research_engineer")
    assert mle.rank != research.rank
    assert research.role_id not in mle.tied_role_ids


def test_core_extended_ranking_change_is_provisional_and_low_confidence() -> None:
    payload = mapping_payload(all_demonstrated=True)
    rubric = production_capability_rubric()
    for item in payload["mappings"]:
        dimension = rubric.dimension(item["dimension_id"])
        if item["role_id"] == "machine_learning_engineer" and dimension.readiness.value == "ready_for_profile_matching":
            item["match_status"] = "not_demonstrated"
        if item["role_id"] == "research_engineer" and dimension.readiness.value == "conditional_pending_evidence_review":
            item["match_status"] = "not_demonstrated"
    artifact = build(payload)
    unstable = [item for item in artifact.role_results if item.ranking_unstable]
    assert unstable
    assert all(item.provisional for item in unstable)
    assert all(item.recommendation_confidence.result in {RecommendationConfidence.LOW, RecommendationConfidence.INSUFFICIENT} for item in unstable)


def test_conflict_and_conditional_dependence_reduce_weakest_link_confidence() -> None:
    payload = mapping_payload(all_demonstrated=True)
    payload["conflict_warnings"] = ["A synthetic high-impact conflict remains unresolved."]
    artifact = build(payload)
    assert all("profile_conflict_low" in x.recommendation_confidence.reason_codes for x in artifact.role_results)


def test_follow_up_is_limited_to_three_and_bound_to_role_target() -> None:
    payload = mapping_payload()
    payload["mappings"] = []
    artifact = build(payload)
    assert len(artifact.follow_up_questions) == 3
    assert all((q.dimension_id is None) != (q.constraint_id is None) for q in artifact.follow_up_questions)


def test_recommendation_round_trip_is_deterministic_and_strict(tmp_path) -> None:
    profile=synthetic_profile();rubric=production_capability_rubric();catalog=production_role_catalog();artifact=build(mapping_payload(all_demonstrated=True))
    first=save_role_recommendation(artifact,tmp_path/"first.json",profile=profile,rubric=rubric,catalog=catalog)
    second=save_role_recommendation(artifact,tmp_path/"second.json",profile=profile,rubric=rubric,catalog=catalog)
    assert first.read_bytes()==second.read_bytes()
    assert load_role_recommendation(first,profile=profile,rubric=rubric,catalog=catalog)==artifact
    data=artifact.to_dict();data["invented"]="bad";first.write_text(json.dumps(data))
    with pytest.raises(Phase2ValidationError,match="unknown fields"):
        load_role_recommendation(first,profile=profile,rubric=rubric,catalog=catalog)


def test_direct_construction_and_typed_load_cannot_bypass_fingerprint(tmp_path) -> None:
    profile=synthetic_profile();rubric=production_capability_rubric();catalog=production_role_catalog();data=build().to_dict();data["profile_fingerprint"]="sha256:"+"0"*64
    value=RoleRecommendationArtifact.from_dict(data)
    with pytest.raises(Phase2ValidationError,match="fingerprint"):
        value.validate(profile=profile,rubric=rubric,catalog=catalog)
    path=tmp_path/"tampered.json";path.write_text(json.dumps(data))
    with pytest.raises(Phase2ValidationError,match="fingerprint"):
        load_role_recommendation(path,profile=profile,rubric=rubric,catalog=catalog)


def test_typed_load_recomputes_score_and_rank(tmp_path) -> None:
    profile=synthetic_profile();rubric=production_capability_rubric();catalog=production_role_catalog();data=build(mapping_payload(all_demonstrated=True)).to_dict()
    data["role_results"][0]["extended_current_fit"]["score"] = 12.0
    data["role_results"][0]["extended_current_fit"]["band"] = "emerging"
    data["role_results"][0]["extended_current_fit"]["band_cap_reason"] = None
    path=tmp_path/"score.json";path.write_text(json.dumps(data))
    with pytest.raises(Phase2ValidationError,match="score or coverage"):
        load_role_recommendation(path,profile=profile,rubric=rubric,catalog=catalog)

    data=build(mapping_payload(all_demonstrated=True)).to_dict()
    ranked=next(item for item in data["role_results"] if item["rank"] is not None)
    ranked["rank"] = 99
    path.write_text(json.dumps(data))
    with pytest.raises(Phase2ValidationError,match="rank|tied"):
        load_role_recommendation(path,profile=profile,rubric=rubric,catalog=catalog)


def test_typed_load_recomputes_confidence_blockers_and_followups(tmp_path) -> None:
    profile=synthetic_profile();rubric=production_capability_rubric();catalog=production_role_catalog();path=tmp_path/"tampered.json"

    data=build().to_dict()
    component=data["role_results"][0]["recommendation_confidence"]["component_results"][0]
    component["confidence"]="low" if component["confidence"]!="low" else "medium"
    path.write_text(json.dumps(data))
    with pytest.raises(Phase2ValidationError,match="confidence"):
        load_role_recommendation(path,profile=profile,rubric=rubric,catalog=catalog)

    data=build().to_dict();data["role_results"][0]["blockers"].append("invented_blocker")
    path.write_text(json.dumps(data))
    with pytest.raises(Phase2ValidationError,match="blockers"):
        load_role_recommendation(path,profile=profile,rubric=rubric,catalog=catalog)

    data=build().to_dict();data["follow_up_questions"][0]["question"]="A forged question?"
    path.write_text(json.dumps(data))
    with pytest.raises(Phase2ValidationError,match="follow-up"):
        load_role_recommendation(path,profile=profile,rubric=rubric,catalog=catalog)


def test_recommendation_rejects_rubric_version_mismatch() -> None:
    from dataclasses import replace
    artifact=build();rubric=replace(production_capability_rubric(),rubric_version="2.0.0")
    with pytest.raises(Phase2ValidationError,match="Rubric version"):
        artifact.validate(profile=synthetic_profile(),rubric=rubric,catalog=production_role_catalog())


def test_atomic_replacement_failure_preserves_existing_file(tmp_path, monkeypatch) -> None:
    import aarvia.phase2_storage as storage
    profile=synthetic_profile();rubric=production_capability_rubric();catalog=production_role_catalog();artifact=build();path=tmp_path/"result.json";path.write_text("original")
    def fail_replace(source, target): raise OSError("replace failed")
    monkeypatch.setattr(storage.os,"replace",fail_replace)
    with pytest.raises(OSError,match="replace failed"):
        save_role_recommendation(artifact,path,profile=profile,rubric=rubric,catalog=catalog)
    assert path.read_text()=="original"


def test_schema_one_remains_explicitly_loadable_but_not_auto_migratable(tmp_path) -> None:
    profile=profile_fixture();catalog=catalog_fixture();reference=ProfileReference.capture(profile,"skills[0].skill_name");legacy=RecommendationSet.from_dict(recommendation_data(reference));path=save_recommendation_set(legacy,tmp_path/"legacy.json",catalog=catalog,profile=profile)
    # Generic v2-aware loader still preserves explicit schema-1 validation.
    loaded=load_any_recommendation(path,profile=profile,rubric=production_capability_rubric(),catalog=catalog)
    assert loaded==legacy
    with pytest.raises(Phase2ValidationError,match="cannot be auto-upgraded"):
        migrate_recommendation_v1_to_v2(legacy)
