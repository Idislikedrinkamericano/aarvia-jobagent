from copy import deepcopy

import pytest

from aarvia.career_direction import (
    ProfileReference,
    RecommendationSet,
    RoleGapAnalysis,
    UnmappedRoleDirection,
    UserRoleDecision,
)
from aarvia.role_catalog import Phase2ValidationError
from phase2_fixtures import catalog_fixture, profile_fixture


NOW = "2026-09-01T12:00:00+08:00"


def recommendation_data(reference: ProfileReference) -> dict:
    base = {
        "recommendation_id": "current_applied_ai",
        "role_id": "applied_ai_engineer",
        "fit_type": "current_fit",
        "rank": 1,
        "fit": "moderate",
        "constraint_compatibility": "compatible",
        "recommendation_tier": "primary_candidate",
        "confidence": "medium",
        "supporting_profile_references": [reference.to_dict()],
        "supported_inferences": [],
        "limitations": ["Evidence Bank has not been built."],
        "unknown_information": ["Production experience depth is unknown."],
        "requirement_references": ["applied_ai.python_fixture"],
    }
    directional = deepcopy(base)
    directional.update(
        recommendation_id="directional_applied_ai",
        fit_type="directional_fit",
        fit="strong",
    )
    return {
        "schema": "aarvia.role_recommendations",
        "schema_version": 1,
        "recommendation_set_id": "fixture_recommendations",
        "catalog_version": "1.0.0",
        "created_at": NOW,
        "current_fit": [base],
        "directional_fit": [directional],
    }


def decision_data() -> dict:
    return {
        "schema": "aarvia.user_role_decision",
        "schema_version": 1,
        "decision_id": "fixture_decision",
        "status": "confirmed",
        "catalog_version": "1.0.0",
        "primary_role": {
            "role_id": "applied_ai_engineer",
            "specialization_ids": ["agentic_ai"],
        },
        "secondary_roles": [
            {"role_id": "backend_engineer", "specialization_ids": ["api_services"]}
        ],
        "rejected_role_ids": ["data_scientist"],
        "explore_later_role_ids": ["research_engineer"],
        "unmapped_roles": [
            {
                "user_provided_name": "AI Solutions Builder",
                "created_at": NOW,
                "status": "unmapped",
                "source": "user_provided",
                "analysis_available": False,
            }
        ],
        "user_reason": "I want to build applied AI products.",
        "created_at": NOW,
        "updated_at": NOW,
        "source_recommendation_reference": "fixture_recommendations",
    }


def gap_data(reference: ProfileReference) -> dict:
    return {
        "schema": "aarvia.role_gap_analysis",
        "schema_version": 1,
        "analysis_id": "fixture_gap_analysis",
        "catalog_version": "1.0.0",
        "created_at": NOW,
        "role_ids": ["applied_ai_engineer"],
        "supported_inferences": [],
        "items": [
            {
                "gap_item_id": "fixture_python_gap",
                "role_id": "applied_ai_engineer",
                "requirement_id": "applied_ai.python_fixture",
                "status": "unknown",
                "career_profile_references": [reference.to_dict()],
                "supported_inference_references": [],
                "explanation": "The Profile mentions Python but has not been evidence-audited.",
                "confidence": "low",
                "follow_up_needed": True,
                "follow_up_question": "What have you built with Python?",
            }
        ],
    }


def test_profile_reference_requires_current_path_value_and_fingerprint() -> None:
    profile = profile_fixture()
    reference = ProfileReference.capture(profile, "skills[0].skill_name")

    assert reference.value_snapshot == "Python"
    reference.validate(profile)

    changed = profile_fixture()
    changed.skills[0].skill_name = "Java"
    with pytest.raises(Phase2ValidationError, match="fingerprint"):
        reference.validate(changed)


def test_nonexistent_or_empty_profile_reference_is_rejected() -> None:
    profile = profile_fixture()
    with pytest.raises(Phase2ValidationError, match="does not exist"):
        ProfileReference.capture(profile, "skills[3].skill_name")
    with pytest.raises(Phase2ValidationError, match="not a confirmed fact"):
        ProfileReference.capture(profile, "career_preferences.preferred_industries")


def test_recommendation_contract_keeps_current_and_directional_rankings_separate() -> None:
    profile = profile_fixture()
    catalog = catalog_fixture()
    result = RecommendationSet.from_dict(
        recommendation_data(ProfileReference.capture(profile, "skills[0].skill_name"))
    )

    result.validate(catalog, profile)
    assert result.current_fit[0].fit.value == "moderate"
    assert result.directional_fit[0].fit.value == "strong"
    assert result.to_dict()["current_fit"][0]["fit_type"] == "current_fit"


def test_recommendation_rejects_nonexistent_requirement_reference() -> None:
    profile = profile_fixture()
    data = recommendation_data(ProfileReference.capture(profile, "skills[0].skill_name"))
    data["current_fit"][0]["requirement_references"] = ["missing_requirement"]
    result = RecommendationSet.from_dict(data)

    with pytest.raises(Phase2ValidationError, match="unknown requirement"):
        result.validate(catalog_fixture(), profile)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda data: data["secondary_roles"].extend(
                [
                    {"role_id": "data_engineer", "specialization_ids": []},
                    {"role_id": "research_engineer", "specialization_ids": []},
                ]
            ),
            "at most two",
        ),
        (
            lambda data: data["secondary_roles"].append(
                {"role_id": "applied_ai_engineer", "specialization_ids": []}
            ),
            "cannot also be Secondary",
        ),
        (
            lambda data: data["rejected_role_ids"].append("backend_engineer"),
            "rejected roles cannot be selected",
        ),
    ],
)
def test_decision_selection_conflicts_are_rejected(change, message) -> None:
    data = decision_data()
    change(data)
    with pytest.raises(Phase2ValidationError, match=message):
        UserRoleDecision.from_dict(data)


def test_confirmed_decision_requires_primary_but_draft_allows_none() -> None:
    data = decision_data()
    data["primary_role"] = None
    with pytest.raises(Phase2ValidationError, match="requires exactly one"):
        UserRoleDecision.from_dict(data)

    data["status"] = "draft"
    decision = UserRoleDecision.from_dict(data)
    assert decision.primary_role is None


def test_specialization_must_belong_to_selected_role() -> None:
    data = decision_data()
    data["primary_role"]["specialization_ids"] = ["api_services"]
    decision = UserRoleDecision.from_dict(data)
    with pytest.raises(Phase2ValidationError, match="unknown specializations"):
        decision.validate(catalog_fixture())


def test_unmapped_role_cannot_claim_analysis_is_available() -> None:
    value = decision_data()["unmapped_roles"][0]
    valid = UnmappedRoleDirection.from_dict(value)
    assert valid.status == "unmapped"
    assert valid.analysis_available is False

    value["analysis_available"] = True
    with pytest.raises(Phase2ValidationError, match="unavailable for analysis"):
        UnmappedRoleDirection.from_dict(value)


def test_decision_validates_source_recommendation_without_being_overwritten() -> None:
    profile = profile_fixture()
    recommendations = RecommendationSet.from_dict(
        recommendation_data(ProfileReference.capture(profile, "skills[0].skill_name"))
    )
    decision = UserRoleDecision.from_dict(decision_data())

    decision.validate(catalog_fixture(), recommendations, profile)
    assert decision.primary_role.role_id == "applied_ai_engineer"
    assert recommendations.current_fit[0].recommendation_id == "current_applied_ai"


def test_decision_requires_complete_recommendation_provenance_context() -> None:
    profile = profile_fixture()
    catalog = catalog_fixture()
    reference = ProfileReference.capture(profile, "skills[0].skill_name")
    recommendations = RecommendationSet.from_dict(recommendation_data(reference))
    decision = UserRoleDecision.from_dict(decision_data())

    with pytest.raises(Phase2ValidationError, match="without its set"):
        decision.validate(catalog, profile=profile)
    with pytest.raises(Phase2ValidationError, match="without its Career Profile"):
        decision.validate(catalog, recommendations)


def test_decision_rejects_mismatched_recommendation_identity_and_catalog() -> None:
    profile = profile_fixture()
    catalog = catalog_fixture()
    reference = ProfileReference.capture(profile, "skills[0].skill_name")
    data = recommendation_data(reference)
    data["recommendation_set_id"] = "different_recommendations"
    different = RecommendationSet.from_dict(data)
    decision = UserRoleDecision.from_dict(decision_data())
    with pytest.raises(Phase2ValidationError, match="different Recommendation Set"):
        decision.validate(catalog, different, profile)

    data = recommendation_data(reference)
    data["catalog_version"] = "9.9.9"
    wrong_catalog = RecommendationSet.from_dict(data)
    with pytest.raises(Phase2ValidationError, match="catalog version"):
        decision.validate(catalog, wrong_catalog, profile)


def test_decision_rejects_recommendation_bound_to_another_profile() -> None:
    profile = profile_fixture()
    reference = ProfileReference.capture(profile, "skills[0].skill_name")
    recommendations = RecommendationSet.from_dict(recommendation_data(reference))
    decision = UserRoleDecision.from_dict(decision_data())
    changed_profile = profile_fixture()
    changed_profile.skills[0].skill_name = "Java"

    with pytest.raises(Phase2ValidationError, match="fingerprint"):
        decision.validate(catalog_fixture(), recommendations, changed_profile)


def test_direct_user_decision_without_recommendation_provenance_is_valid() -> None:
    data = decision_data()
    data["source_recommendation_reference"] = None
    decision = UserRoleDecision.from_dict(data)

    decision.validate(catalog_fixture())
    assert decision.source_recommendation_reference is None


def test_gap_unknown_is_preserved_and_not_treated_as_missing() -> None:
    profile = profile_fixture()
    analysis = RoleGapAnalysis.from_dict(
        gap_data(ProfileReference.capture(profile, "skills[0].skill_name"))
    )
    decision = UserRoleDecision.from_dict(decision_data())

    analysis.validate(catalog_fixture(), profile, decision)
    assert analysis.items[0].status.value == "unknown"
    assert analysis.to_dict()["items"][0]["status"] == "unknown"


def test_gap_requires_confirmed_decision_and_selected_roles() -> None:
    profile = profile_fixture()
    reference = ProfileReference.capture(profile, "skills[0].skill_name")
    analysis = RoleGapAnalysis.from_dict(gap_data(reference))
    catalog = catalog_fixture()

    with pytest.raises(Phase2ValidationError, match="confirmed User Decision"):
        analysis.validate(catalog, profile, None)  # type: ignore[arg-type]

    draft_data = decision_data()
    draft_data["status"] = "draft"
    draft = UserRoleDecision.from_dict(draft_data)
    with pytest.raises(Phase2ValidationError, match="confirmed User Decision"):
        analysis.validate(catalog, profile, draft)

    confirmed = UserRoleDecision.from_dict(decision_data())
    analysis.validate(catalog, profile, confirmed)

    unselected_data = gap_data(reference)
    unselected_data["role_ids"].append("data_engineer")
    unselected = RoleGapAnalysis.from_dict(unselected_data)
    with pytest.raises(Phase2ValidationError, match="not selected"):
        unselected.validate(catalog, profile, confirmed)

    rejected_data = gap_data(reference)
    rejected_data["role_ids"] = ["data_scientist"]
    rejected_data["items"] = []
    rejected = RoleGapAnalysis.from_dict(rejected_data)
    with pytest.raises(Phase2ValidationError, match="not selected"):
        rejected.validate(catalog, profile, confirmed)


def test_gap_rejects_decision_from_another_catalog_version() -> None:
    profile = profile_fixture()
    reference = ProfileReference.capture(profile, "skills[0].skill_name")
    analysis = RoleGapAnalysis.from_dict(gap_data(reference))
    data = decision_data()
    data["catalog_version"] = "9.9.9"
    decision = UserRoleDecision.from_dict(data)

    with pytest.raises(Phase2ValidationError, match="catalog version"):
        analysis.validate(catalog_fixture(), profile, decision)


def test_gap_rejects_nonexistent_requirement_and_unsupported_satisfied_claim() -> None:
    profile = profile_fixture()
    reference = ProfileReference.capture(profile, "skills[0].skill_name")
    data = gap_data(reference)
    data["items"][0]["requirement_id"] = "missing_requirement"
    analysis = RoleGapAnalysis.from_dict(data)
    decision = UserRoleDecision.from_dict(decision_data())
    with pytest.raises(Phase2ValidationError, match="unknown requirement"):
        analysis.validate(catalog_fixture(), profile, decision)

    data = gap_data(reference)
    data["items"][0]["status"] = "satisfied"
    data["items"][0]["career_profile_references"] = []
    analysis = RoleGapAnalysis.from_dict(data)
    with pytest.raises(Phase2ValidationError, match="without evidence"):
        analysis.validate(catalog_fixture(), profile, decision)


def test_contract_serialization_round_trips_deterministically() -> None:
    profile = profile_fixture()
    reference = ProfileReference.capture(profile, "basic_profile.current_location")
    recommendation = RecommendationSet.from_dict(recommendation_data(reference))
    decision = UserRoleDecision.from_dict(decision_data())
    analysis = RoleGapAnalysis.from_dict(gap_data(reference))

    assert RecommendationSet.from_dict(recommendation.to_dict()) == recommendation
    assert UserRoleDecision.from_dict(decision.to_dict()) == decision
    assert RoleGapAnalysis.from_dict(analysis.to_dict()) == analysis
