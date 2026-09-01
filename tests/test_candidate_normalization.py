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
        input_fn=scripted_input(["My background", "y", "y", "y", "y"]),
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
    assert "target_locations" in PROFILE_JSON_SCHEMA["properties"]["constraints"]["required"]


def education_record(**changes) -> dict:
    record = {
        "institution": "Example University",
        "degree": "Bachelor of Science",
        "field_of_study": "Computer Science",
        "start_date": "2022-09",
        "expected_graduation_date": "2026-06",
        "gpa": "3.7 / 4.0",
    }
    record.update(changes)
    return record


def test_education_major_alias_maps_to_field_of_study_and_preserves_gpa() -> None:
    record = education_record()
    record["major"] = record.pop("field_of_study")

    candidate = CandidateProfile.from_extracted(
        normalize_candidate_data({"education": [record]})
    )

    education = candidate.data["education"][0]
    assert education["field_of_study"] == "Computer Science"
    assert education["gpa"] == "3.7 / 4.0"
    assert "major" not in education


def test_matching_major_and_field_of_study_do_not_conflict() -> None:
    record = education_record(major=" Computer Science ")

    normalized = normalize_candidate_data({"education": [record]})

    assert normalized["education"][0]["field_of_study"] == "Computer Science"
    assert "major" not in normalized["education"][0]


def test_conflicting_major_and_field_of_study_are_rejected() -> None:
    record = education_record(major="Economics")

    with pytest.raises(ProfileValidationError, match="major conflicts with field_of_study"):
        normalize_candidate_data({"education": [record]})


def test_other_non_empty_education_field_remains_rejected() -> None:
    record = education_record(honors="summa cum laude")

    with pytest.raises(ProfileValidationError, match="unknown fields: honors"):
        CandidateProfile.from_extracted(
            normalize_candidate_data({"education": [record]})
        )


def test_education_normalization_failure_does_not_modify_profile(tmp_path) -> None:
    path = tmp_path / "profile.json"
    original = create_profile({"basic_profile": {"current_location": "Shanghai"}})
    save_profile(original, path)
    extractor = FakeExtractor(
        {"education": [education_record(major="Economics")]}
    )

    with pytest.raises(ProfileValidationError, match="major conflicts with field_of_study"):
        run_narrative_discovery(
            path,
            extractor,
            input_fn=scripted_input(["My education"]),
            output_fn=lambda _message: None,
        )

    assert load_profile(path) == original


def test_education_schema_uses_gpa_and_not_major() -> None:
    education_schema = PROFILE_JSON_SCHEMA["properties"]["education"]["items"]

    assert education_schema["additionalProperties"] is False
    assert "major" not in education_schema["properties"]
    assert education_schema["properties"]["gpa"] == {"type": ["string", "null"]}
    assert "gpa" in education_schema["required"]


@pytest.mark.parametrize("placeholder", ["YYYY-MM", "YYYY-MM-DD"])
def test_exact_date_placeholders_become_null_only_in_date_fields(placeholder) -> None:
    raw = {
        "education": [
            education_record(
                start_date=placeholder,
                expected_graduation_date=placeholder,
            )
        ],
        "experience_overview": [
            {
                "experience_type": "work",
                "organization_or_project_name": "Example Org",
                "title_or_role": "Analyst",
                "short_factual_summary": placeholder,
                "start_date": placeholder,
                "end_date": placeholder,
            }
        ],
        "constraints": {
            "target_start_date": placeholder,
            "other_constraints": [placeholder],
        },
    }

    normalized = normalize_candidate_data(raw)
    candidate = CandidateProfile.from_extracted(normalized)

    assert candidate.data["education"][0]["start_date"] is None
    assert candidate.data["education"][0]["expected_graduation_date"] is None
    assert candidate.data["experience_overview"][0]["start_date"] is None
    assert candidate.data["experience_overview"][0]["end_date"] is None
    assert candidate.data["constraints"]["target_start_date"] is None
    assert candidate.data["experience_overview"][0]["short_factual_summary"] == placeholder
    assert candidate.data["constraints"]["other_constraints"] == [placeholder]


@pytest.mark.parametrize("date_value", ["2026-09", "2026-09-21"])
def test_real_dates_are_preserved(date_value) -> None:
    normalized = normalize_candidate_data(
        {"education": [education_record(start_date=date_value)]}
    )

    assert normalized["education"][0]["start_date"] == date_value


@pytest.mark.parametrize("invalid_date", ["2026-13", "next year", "soon"])
def test_other_invalid_dates_remain_rejected(invalid_date) -> None:
    normalized = normalize_candidate_data(
        {"education": [education_record(start_date=invalid_date)]}
    )

    with pytest.raises(ProfileValidationError, match="start_date"):
        CandidateProfile.from_extracted(normalized)


def test_invalid_date_candidate_does_not_modify_formal_profile(tmp_path) -> None:
    path = tmp_path / "profile.json"
    original = create_profile({"basic_profile": {"current_location": "Shanghai"}})
    save_profile(original, path)
    extractor = FakeExtractor(
        {"education": [education_record(start_date="next year")]}
    )

    with pytest.raises(ProfileValidationError, match="start_date"):
        run_narrative_discovery(
            path,
            extractor,
            input_fn=scripted_input(["My education"]),
            output_fn=lambda _message: None,
        )

    assert load_profile(path) == original


def test_date_schema_is_nullable_without_copyable_placeholders() -> None:
    date_schemas = [
        PROFILE_JSON_SCHEMA["properties"]["education"]["items"]["properties"]["start_date"],
        PROFILE_JSON_SCHEMA["properties"]["education"]["items"]["properties"][
            "expected_graduation_date"
        ],
        PROFILE_JSON_SCHEMA["properties"]["experience_overview"]["items"]["properties"][
            "start_date"
        ],
        PROFILE_JSON_SCHEMA["properties"]["experience_overview"]["items"]["properties"][
            "end_date"
        ],
        PROFILE_JSON_SCHEMA["properties"]["constraints"]["properties"]["target_start_date"],
    ]

    for schema in date_schemas:
        assert schema["type"] == ["string", "null"]
        assert "YYYY-MM" not in schema["description"]
        assert "example" not in schema
        assert "default" not in schema


def experience_record(**changes) -> dict:
    record = {
        "experience_type": "work",
        "organization_or_project_name": "MiraclePlus",
        "title_or_role": "AI Analyst",
        "short_factual_summary": "Evaluated AI solutions.",
        "start_date": None,
        "end_date": None,
    }
    record.update(changes)
    return record


@pytest.mark.parametrize("summary", ["", "   "])
def test_blank_experience_summary_becomes_null(summary) -> None:
    candidate = CandidateProfile.from_extracted(
        normalize_candidate_data(
            {"experience_overview": [experience_record(short_factual_summary=summary)]}
        )
    )

    assert candidate.data["experience_overview"][0]["short_factual_summary"] is None


def test_missing_or_null_experience_summary_does_not_reject_candidate() -> None:
    missing = experience_record()
    missing.pop("short_factual_summary")

    for record in (missing, experience_record(short_factual_summary=None)):
        candidate = CandidateProfile.from_extracted(
            normalize_candidate_data({"experience_overview": [record]})
        )
        assert candidate.data["experience_overview"][0]["short_factual_summary"] is None


def test_normal_experience_summary_is_preserved() -> None:
    normalized = normalize_candidate_data(
        {"experience_overview": [experience_record()]}
    )

    assert normalized["experience_overview"][0]["short_factual_summary"] == (
        "Evaluated AI solutions."
    )


@pytest.mark.parametrize("summary", [123, []])
def test_non_string_experience_summary_is_still_rejected(summary) -> None:
    normalized = normalize_candidate_data(
        {"experience_overview": [experience_record(short_factual_summary=summary)]}
    )

    with pytest.raises(ProfileValidationError, match="short_factual_summary"):
        CandidateProfile.from_extracted(normalized)


def test_experience_summary_schema_is_required_but_nullable() -> None:
    experience_schema = PROFILE_JSON_SCHEMA["properties"]["experience_overview"]["items"]

    assert experience_schema["properties"]["short_factual_summary"] == {
        "type": ["string", "null"]
    }
    assert "short_factual_summary" in experience_schema["required"]
