from copy import deepcopy

import pytest

from aarvia import ProfileValidationError, create_profile, load_profile, save_profile
from aarvia.candidate_normalization import normalize_candidate_data
from aarvia.candidates import CandidateProfile
from aarvia.confirmation import run_narrative_discovery
from aarvia.narrative_extraction import PROFILE_JSON_SCHEMA

from test_confirmation import FakeExtractor
from test_interview import scripted_input


BAILIAN_ALIAS_FIXTURE = {
    "projects": [
        {
            "organization_or_project_name": "Aarvia",
            "title_or_role": "Developer",
            "short_factual_summary": "Built a career discovery agent.",
            "start_date": "2026-01",
            "end_date": None,
        }
    ],
    "target_employment_type": ["full-time"],
    "target_locations": ["Shanghai", "Singapore"],
    "target_roles": ["AI Engineer", "Product Engineer"],
}


@pytest.mark.parametrize(
    "empty_value",
    [
        None,
        "",
        "   ",
        [],
        {},
        {"email": None, "phone": None},
        {"nested": [{"value": "  "}, None, []]},
    ],
)
def test_empty_contact_info_is_safely_removed(empty_value) -> None:
    normalized = normalize_candidate_data(
        {"contact_info": empty_value, "skills": [{"skill_name": "Python", "category": "language"}]}
    )

    assert "contact_info" not in normalized
    assert normalized["skills"][0]["skill_name"] == "Python"


def test_non_empty_contact_info_is_preserved_for_strict_rejection() -> None:
    normalized = normalize_candidate_data(
        {"contact_info": {"email": "example@example.com"}}
    )

    assert normalized["contact_info"] == {"email": "example@example.com"}
    with pytest.raises(ProfileValidationError, match="unknown fields: contact_info"):
        CandidateProfile.from_extracted(normalized)


def test_other_empty_unknown_field_follows_same_cleanup_rule() -> None:
    normalized = normalize_candidate_data(
        {"provider_metadata": {"notes": [None, " "]}}
    )

    assert normalized == {}


def test_other_non_empty_unknown_field_remains_rejected() -> None:
    normalized = normalize_candidate_data(
        {"provider_metadata": {"notes": [None, "keep this"]}}
    )

    with pytest.raises(ProfileValidationError, match="unknown fields: provider_metadata"):
        CandidateProfile.from_extracted(normalized)


def test_cleanup_runs_before_existing_alias_normalization() -> None:
    raw = {**deepcopy(BAILIAN_ALIAS_FIXTURE), "contact_info": {"email": None, "phone": " "}}

    candidate = CandidateProfile.from_extracted(normalize_candidate_data(raw))

    assert "contact_info" not in candidate.data
    assert candidate.data["experience_overview"][0]["experience_type"] == "project"
    assert candidate.data["career_preferences"]["currently_considered_roles"] == [
        "AI Engineer",
        "Product Engineer",
    ]
    assert candidate.data["constraints"]["target_locations"] == ["Shanghai", "Singapore"]
    assert candidate.data["constraints"]["employment_type_preference"] == "full-time"


def test_bailian_alias_fixture_maps_to_phase_1a_schema() -> None:
    normalized = normalize_candidate_data(BAILIAN_ALIAS_FIXTURE)
    candidate = CandidateProfile.from_extracted(normalized)

    project = candidate.data["experience_overview"][0]
    assert project == {
        "experience_type": "project",
        "organization_or_project_name": "Aarvia",
        "title_or_role": "Developer",
        "short_factual_summary": "Built a career discovery agent.",
        "start_date": "2026-01",
        "end_date": None,
    }
    assert candidate.data["career_preferences"]["currently_considered_roles"] == [
        "AI Engineer",
        "Product Engineer",
    ]
    assert candidate.data["constraints"]["target_locations"] == ["Shanghai", "Singapore"]
    assert candidate.data["constraints"]["employment_type_preference"] == "full-time"


def test_normalization_preserves_explicit_information_without_inventing_fields() -> None:
    normalized = normalize_candidate_data(BAILIAN_ALIAS_FIXTURE)

    assert normalized["experience_overview"][0]["short_factual_summary"] == (
        BAILIAN_ALIAS_FIXTURE["projects"][0]["short_factual_summary"]
    )
    assert "skills" not in normalized
    assert "education" not in normalized
    assert normalized["career_preferences"] == {
        "currently_considered_roles": ["AI Engineer", "Product Engineer"]
    }
    assert normalized["constraints"] == {
        "target_locations": ["Shanghai", "Singapore"],
        "employment_type_preference": "full-time",
    }


def test_supported_aliases_merge_with_existing_canonical_lists_in_order() -> None:
    raw = {
        "career_preferences": {"currently_considered_roles": ["Engineer"]},
        "constraints": {"target_locations": ["Shanghai"]},
        "target_roles": ["Engineer", "Researcher"],
        "target_locations": ["Singapore", "Shanghai"],
    }

    normalized = normalize_candidate_data(raw)

    assert normalized["career_preferences"]["currently_considered_roles"] == [
        "Engineer",
        "Researcher",
    ]
    assert normalized["constraints"]["target_locations"] == ["Shanghai", "Singapore"]


def test_two_supported_employment_types_map_to_either() -> None:
    normalized = normalize_candidate_data(
        {"target_employment_type": ["internship", "full-time"]}
    )

    assert normalized["constraints"]["employment_type_preference"] == "either"


def test_other_unknown_field_is_not_removed_and_is_rejected() -> None:
    normalized = normalize_candidate_data(
        {**BAILIAN_ALIAS_FIXTURE, "recommended_seniority": "senior"}
    )

    assert normalized["recommended_seniority"] == "senior"
    with pytest.raises(ProfileValidationError, match="unknown fields: recommended_seniority"):
        CandidateProfile.from_extracted(normalized)


def test_unknown_project_field_is_rejected_by_phase_1a() -> None:
    raw = deepcopy(BAILIAN_ALIAS_FIXTURE)
    raw["projects"][0]["inferred_impact"] = "large"

    with pytest.raises(ProfileValidationError, match="unknown fields: inferred_impact"):
        CandidateProfile.from_extracted(normalize_candidate_data(raw))


def test_normalization_failure_does_not_modify_formal_profile(tmp_path) -> None:
    path = tmp_path / "profile.json"
    original = create_profile({"basic_profile": {"current_location": "Shanghai"}})
    save_profile(original, path)
    invalid = {
        **BAILIAN_ALIAS_FIXTURE,
        "contact_info": {"email": "example@example.com"},
    }
    extractor = FakeExtractor(invalid)

    with pytest.raises(ProfileValidationError, match="unknown fields: contact_info"):
        run_narrative_discovery(
            path,
            extractor,
            input_fn=scripted_input(["My background"]),
            output_fn=lambda _message: None,
        )

    assert load_profile(path) == original


def test_normalized_fixture_can_be_confirmed_into_formal_profile(tmp_path) -> None:
    path = tmp_path / "profile.json"
    extractor = FakeExtractor(BAILIAN_ALIAS_FIXTURE)

    completed = run_narrative_discovery(
        path,
        extractor,
        input_fn=scripted_input(["My background", "y", "y", "y"]),
        output_fn=lambda _message: None,
    )

    profile = load_profile(path)
    assert completed is True
    assert profile.experience_overview[0].experience_type == "project"
    assert profile.career_preferences.currently_considered_roles == [
        "AI Engineer",
        "Product Engineer",
    ]
    assert profile.constraints.target_locations == ["Shanghai", "Singapore"]
    assert profile.constraints.employment_type_preference == "full-time"


def test_responses_schema_uses_only_canonical_nested_fields() -> None:
    top_level_fields = set(PROFILE_JSON_SCHEMA["properties"])

    assert PROFILE_JSON_SCHEMA["additionalProperties"] is False
    assert "contact_info" not in top_level_fields
    assert top_level_fields == {
        "basic_profile",
        "education",
        "experience_overview",
        "skills",
        "career_preferences",
        "constraints",
    }
    assert "currently_considered_roles" in (
        PROFILE_JSON_SCHEMA["properties"]["career_preferences"]["properties"]
    )
    assert "target_locations" in PROFILE_JSON_SCHEMA["properties"]["constraints"]["properties"]
    assert "employment_type_preference" in (
        PROFILE_JSON_SCHEMA["properties"]["constraints"]["properties"]
    )
    assert PROFILE_JSON_SCHEMA["properties"]["career_preferences"]["additionalProperties"] is False
    assert PROFILE_JSON_SCHEMA["properties"]["constraints"]["additionalProperties"] is False
