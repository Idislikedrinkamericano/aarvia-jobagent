import json

import pytest

from aarvia.career_direction import (
    ProfileReference,
    RecommendationSet,
    RoleGapAnalysis,
    UserRoleDecision,
    load_recommendation_set,
    load_role_gap_analysis,
    load_user_role_decision,
    save_recommendation_set,
    save_role_gap_analysis,
    save_user_role_decision,
)
from aarvia.live_jobs import (
    LiveJobCollection,
    load_live_job_collection,
    save_live_job_collection,
)
from aarvia.role_catalog import Phase2ValidationError
from phase2_fixtures import catalog_fixture, live_job_fixture_data, profile_fixture
from test_career_direction import decision_data, gap_data, recommendation_data


def phase2_artifacts():
    profile = profile_fixture()
    catalog = catalog_fixture()
    reference = ProfileReference.capture(profile, "skills[0].skill_name")
    recommendations = RecommendationSet.from_dict(recommendation_data(reference))
    decision = UserRoleDecision.from_dict(decision_data())
    gap = RoleGapAnalysis.from_dict(gap_data(reference))
    jobs = LiveJobCollection.from_dict(live_job_fixture_data())
    return profile, catalog, recommendations, decision, gap, jobs


def test_recommendation_set_file_round_trip_is_deterministic(tmp_path) -> None:
    profile, catalog, value, _, _, _ = phase2_artifacts()
    first = save_recommendation_set(
        value, tmp_path / "first.json", catalog=catalog, profile=profile
    )
    second = save_recommendation_set(
        value, tmp_path / "second.json", catalog=catalog, profile=profile
    )

    loaded = load_recommendation_set(first, catalog=catalog, profile=profile)
    assert loaded == value
    assert first.read_bytes() == second.read_bytes()


def test_user_decision_file_round_trip_is_strict(tmp_path) -> None:
    profile, catalog, recommendations, value, _, _ = phase2_artifacts()
    recommendations.validate(catalog, profile)
    path = save_user_role_decision(
        value,
        tmp_path / "decision.json",
        catalog=catalog,
        recommendation_set=recommendations,
        profile=profile,
    )
    duplicate = save_user_role_decision(
        value,
        tmp_path / "decision-copy.json",
        catalog=catalog,
        recommendation_set=recommendations,
        profile=profile,
    )

    assert load_user_role_decision(
        path, catalog=catalog, recommendation_set=recommendations, profile=profile
    ) == value
    assert path.read_bytes() == duplicate.read_bytes()


def test_gap_analysis_file_round_trip_is_strict(tmp_path) -> None:
    profile, catalog, _, decision, value, _ = phase2_artifacts()
    path = save_role_gap_analysis(
        value, tmp_path / "gap.json", catalog=catalog, profile=profile, decision=decision
    )
    duplicate = save_role_gap_analysis(
        value,
        tmp_path / "gap-copy.json",
        catalog=catalog,
        profile=profile,
        decision=decision,
    )

    assert load_role_gap_analysis(
        path, catalog=catalog, profile=profile, decision=decision
    ) == value
    assert path.read_bytes() == duplicate.read_bytes()


def test_gap_file_operations_cannot_bypass_confirmed_decision(tmp_path) -> None:
    profile, catalog, _, decision, value, _ = phase2_artifacts()
    path = tmp_path / "gap.json"

    with pytest.raises(TypeError, match="decision"):
        save_role_gap_analysis(value, path, catalog=catalog, profile=profile)

    save_role_gap_analysis(
        value, path, catalog=catalog, profile=profile, decision=decision
    )
    draft_data = decision.to_dict()
    draft_data["status"] = "draft"
    draft = UserRoleDecision.from_dict(draft_data)
    with pytest.raises(Phase2ValidationError, match="confirmed User Decision"):
        load_role_gap_analysis(path, catalog=catalog, profile=profile, decision=draft)

    wrong_data = decision.to_dict()
    wrong_data["catalog_version"] = "9.9.9"
    wrong = UserRoleDecision.from_dict(wrong_data)
    with pytest.raises(Phase2ValidationError, match="catalog version"):
        load_role_gap_analysis(path, catalog=catalog, profile=profile, decision=wrong)


def test_decision_file_requires_valid_recommendation_profile_context(tmp_path) -> None:
    profile, catalog, recommendations, decision, _, _ = phase2_artifacts()
    path = tmp_path / "decision.json"

    with pytest.raises(Phase2ValidationError, match="without its Career Profile"):
        save_user_role_decision(
            decision,
            path,
            catalog=catalog,
            recommendation_set=recommendations,
        )

    save_user_role_decision(
        decision,
        path,
        catalog=catalog,
        recommendation_set=recommendations,
        profile=profile,
    )
    changed_profile = profile_fixture()
    changed_profile.skills[0].skill_name = "Java"
    with pytest.raises(Phase2ValidationError, match="fingerprint"):
        load_user_role_decision(
            path,
            catalog=catalog,
            recommendation_set=recommendations,
            profile=changed_profile,
        )


def test_live_job_collection_file_round_trip_is_strict(tmp_path) -> None:
    _, catalog, _, _, _, value = phase2_artifacts()
    path = save_live_job_collection(value, tmp_path / "jobs.json", catalog=catalog)
    duplicate = save_live_job_collection(value, tmp_path / "jobs-copy.json", catalog=catalog)

    assert load_live_job_collection(path, catalog=catalog) == value
    assert path.read_bytes() == duplicate.read_bytes()


def test_typed_load_rejects_unknown_fields_and_invalid_cross_references(tmp_path) -> None:
    profile, catalog, recommendations, _, _, _ = phase2_artifacts()
    data = recommendations.to_dict()
    data["future_score"] = 92
    path = tmp_path / "unknown.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        load_recommendation_set(path, catalog=catalog, profile=profile)

    data = recommendations.to_dict()
    data["current_fit"][0]["requirement_references"] = ["missing_requirement"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(Phase2ValidationError, match="unknown requirement"):
        load_recommendation_set(path, catalog=catalog, profile=profile)


def test_atomic_write_failure_preserves_existing_artifact(tmp_path, monkeypatch) -> None:
    profile, catalog, value, _, _, _ = phase2_artifacts()
    path = tmp_path / "recommendations.json"
    original = b'{"existing": true}\n'
    path.write_bytes(original)

    def fail_replace(source, target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("aarvia.phase2_storage.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        save_recommendation_set(value, path, catalog=catalog, profile=profile)

    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
