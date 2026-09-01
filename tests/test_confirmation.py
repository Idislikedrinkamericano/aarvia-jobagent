from copy import deepcopy

import pytest

from aarvia import create_profile, load_profile, save_profile
from aarvia.confirmation import (
    CANCEL_REFINEMENT,
    RETRY_REFINEMENT,
    merge_candidate_topic,
    merge_refinement_topic,
    run_narrative_discovery,
)
from aarvia.llm_client import LLMRequestError

from test_interview import scripted_input
from test_profile import complete_profile_data


class FakeExtractor:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def extract(self, narrative: str, *, topic: str | None = None):
        self.calls.append((narrative, topic))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)

    def extract_correction(
        self, correction, *, topic, current_topic, original_narrative, field_path=None
    ):
        self.calls.append((correction, topic, deepcopy(current_topic), original_narrative))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)


def run_workflow(path, extractor, answers):
    output = []
    result = run_narrative_discovery(
        path,
        extractor,
        input_fn=scripted_input(answers),
        output_fn=output.append,
    )
    return result, output


def test_fake_extractor_candidate_is_not_saved_before_confirmation(tmp_path) -> None:
    path = tmp_path / "profile.json"
    original = create_profile({"basic_profile": {"current_location": "Shanghai"}})
    save_profile(original, path)
    extractor = FakeExtractor({"skills": [{"skill_name": "Python", "category": "language"}]})

    run_workflow(path, extractor, ["I use Python.", "q"])

    assert load_profile(path) == original


def test_accepted_topic_is_merged_and_saved(tmp_path) -> None:
    path = tmp_path / "profile.json"
    extractor = FakeExtractor({"skills": [{"skill_name": "Python", "category": "language"}]})

    result, output = run_workflow(path, extractor, ["I use Python.", "y", "y"])

    assert result is True
    assert load_profile(path).skills[0].skill_name == "Python"
    assert "Session draft summary:" in output


def test_rejected_topic_does_not_change_existing_profile(tmp_path) -> None:
    path = tmp_path / "profile.json"
    original = create_profile({"basic_profile": {"current_location": "Shanghai"}})
    save_profile(original, path)
    extractor = FakeExtractor({"skills": [{"skill_name": "Python", "category": "language"}]})

    run_workflow(path, extractor, ["I use Python.", "n"])

    assert load_profile(path) == original


@pytest.mark.parametrize(
    ("topic", "candidate_value"),
    [
        ("education", complete_profile_data()["education"]),
        ("experience_overview", complete_profile_data()["experience_overview"]),
        ("skills", complete_profile_data()["skills"]),
    ],
)
def test_identical_records_are_not_duplicated(topic, candidate_value) -> None:
    profile = create_profile(complete_profile_data())

    merged = merge_candidate_topic(
        profile,
        topic,
        candidate_value,
        input_fn=scripted_input([]),
        output_fn=lambda _message: None,
    )

    assert merged is not None
    assert len(getattr(merged, topic)) == 1


def test_two_degrees_at_same_school_with_different_fields_are_preserved() -> None:
    profile = create_profile(
        {
            "education": [
                {
                    "institution": "Example University",
                    "degree": "Bachelor of Science",
                    "field_of_study": "Computer Science and Advertising",
                    "start_date": "2022-09",
                    "expected_graduation_date": "2026-06",
                    "gpa": None,
                }
            ]
        }
    )
    second_degree = {
        "institution": "Example University",
        "degree": "Bachelor of Science",
        "field_of_study": "Economics",
        "start_date": "2022-09",
        "expected_graduation_date": "2026-06",
        "gpa": None,
    }

    merged = merge_candidate_topic(
        profile,
        "education",
        [second_degree],
        input_fn=scripted_input([]),
        output_fn=lambda _message: None,
    )

    assert merged is not None
    assert [entry.field_of_study for entry in merged.education] == [
        "Computer Science and Advertising",
        "Economics",
    ]


def education_with_dates(start_date=None, end_date=None):
    return {
        "institution": "UIUC",
        "degree": "Bachelor's Degree",
        "field_of_study": "Economics",
        "start_date": start_date,
        "expected_graduation_date": end_date,
        "gpa": None,
    }


def test_missing_record_fields_are_filled_without_conflict() -> None:
    profile = create_profile({"education": [education_with_dates()]})

    merged = merge_candidate_topic(
        profile,
        "education",
        [education_with_dates("2022-08", "2026-05")],
        input_fn=lambda _prompt: pytest.fail("safe supplementation must not ask about a conflict"),
        output_fn=lambda _message: None,
    )

    assert merged is not None
    assert len(merged.education) == 1
    assert merged.education[0].start_date == "2022-08"
    assert merged.education[0].expected_graduation_date == "2026-05"


def test_candidate_null_does_not_overwrite_existing_record_value() -> None:
    profile = create_profile(
        {"education": [education_with_dates("2022-08", "2026-05")]}
    )

    merged = merge_candidate_topic(
        profile,
        "education",
        [education_with_dates(None, None)],
        input_fn=lambda _prompt: pytest.fail("empty Candidate values must be ignored"),
        output_fn=lambda _message: None,
    )

    assert merged is not None
    assert merged.education[0].start_date == "2022-08"
    assert merged.education[0].expected_graduation_date == "2026-05"


def test_different_non_empty_record_field_is_a_real_friendly_conflict() -> None:
    profile = create_profile(
        {"education": [education_with_dates("2022-08", "2026-05")]}
    )
    output = []

    merged = merge_candidate_topic(
        profile,
        "education",
        [education_with_dates("2022-08", "2026-06")],
        input_fn=scripted_input(["k"]),
        output_fn=output.append,
    )

    assert merged is not None
    assert merged.education[0].expected_graduation_date == "2026-05"
    assert "End date conflict for:" in output
    assert "UIUC — Bachelor's Degree in Economics" in output
    assert "Current end date: May 2026" in output
    assert "New end date: June 2026" in output


def test_adding_experience_dates_updates_one_record_without_conflict() -> None:
    record = incomplete_experience("Evaluated AI solutions.")
    profile = create_profile({"experience_overview": [record]})
    dated = {**record, "start_date": "2025-05", "end_date": "2025-08"}

    merged = merge_candidate_topic(
        profile,
        "experience_overview",
        [dated],
        input_fn=lambda _prompt: pytest.fail("date supplementation must not conflict"),
        output_fn=lambda _message: None,
    )

    assert merged is not None
    assert len(merged.experience_overview) == 1
    assert merged.experience_overview[0].start_date == "2025-05"
    assert merged.experience_overview[0].end_date == "2025-08"


def test_refinement_identity_ignores_dates_and_normalizes_safe_text() -> None:
    record = incomplete_experience("Evaluated AI solutions.")
    record["organization_or_project_name"] = "MiraclePlus"
    record["title_or_role"] = "AI  Analyst"
    profile = create_profile({"experience_overview": [record]})
    candidate = {
        **record,
        "organization_or_project_name": "  ＭｉｒａｃｌｅＰｌｕｓ  ",
        "title_or_role": "ai analyst",
        "start_date": "2025-05",
        "end_date": "2025-08",
    }

    merged = merge_refinement_topic(
        profile,
        "experience_overview",
        [candidate],
        input_fn=lambda _prompt: pytest.fail("unique normalized identity must match"),
        output_fn=lambda _message: None,
    )

    assert merged is not RETRY_REFINEMENT and merged is not CANCEL_REFINEMENT
    assert len(merged.experience_overview) == 1
    assert merged.experience_overview[0].start_date == "2025-05"


def test_refinement_summary_change_updates_record_without_duplication() -> None:
    record = incomplete_experience("Old factual summary.")
    profile = create_profile({"experience_overview": [record]})
    candidate = {**record, "short_factual_summary": "New factual summary."}

    merged = merge_refinement_topic(
        profile,
        "experience_overview",
        [candidate],
        input_fn=scripted_input(["u"]),
        output_fn=lambda _message: None,
    )

    assert merged is not RETRY_REFINEMENT and merged is not CANCEL_REFINEMENT
    assert len(merged.experience_overview) == 1
    assert merged.experience_overview[0].short_factual_summary == "New factual summary."


def test_refinement_candidate_null_does_not_overwrite_existing_dates() -> None:
    record = incomplete_experience("Evaluated AI solutions.") | {
        "start_date": "2025-05",
        "end_date": "2025-08",
    }
    profile = create_profile({"experience_overview": [record]})
    candidate = {**record, "start_date": None, "end_date": None}

    merged = merge_refinement_topic(
        profile,
        "experience_overview",
        [candidate],
        input_fn=lambda _prompt: pytest.fail("null Candidate values must be ignored"),
        output_fn=lambda _message: None,
    )

    assert merged is not RETRY_REFINEMENT and merged is not CANCEL_REFINEMENT
    assert merged.experience_overview[0].start_date == "2025-05"
    assert merged.experience_overview[0].end_date == "2025-08"


def test_refinement_different_non_empty_date_is_a_conflict() -> None:
    record = incomplete_experience("Evaluated AI solutions.") | {
        "start_date": "2025-05",
        "end_date": "2025-08",
    }
    profile = create_profile({"experience_overview": [record]})
    output = []

    merged = merge_refinement_topic(
        profile,
        "experience_overview",
        [{**record, "end_date": "2025-09"}],
        input_fn=scripted_input(["k"]),
        output_fn=output.append,
    )

    assert merged is not RETRY_REFINEMENT and merged is not CANCEL_REFINEMENT
    assert merged.experience_overview[0].end_date == "2025-08"
    assert "End date conflict for:" in output


def test_unmatched_refinement_requires_explicit_add() -> None:
    profile = create_profile({"experience_overview": [incomplete_experience()]})
    new_record = incomplete_experience("Built a product.") | {
        "organization_or_project_name": "New Company"
    }

    retry = merge_refinement_topic(
        profile,
        "experience_overview",
        [new_record],
        input_fn=scripted_input(["r"]),
        output_fn=lambda _message: None,
    )
    added = merge_refinement_topic(
        profile,
        "experience_overview",
        [new_record],
        input_fn=scripted_input(["a"]),
        output_fn=lambda _message: None,
    )

    assert retry is RETRY_REFINEMENT
    assert len(profile.experience_overview) == 1
    assert added is not RETRY_REFINEMENT and added is not CANCEL_REFINEMENT
    assert len(added.experience_overview) == 2


def test_multiple_refinement_matches_require_disambiguation() -> None:
    first = incomplete_experience("First period.") | {
        "start_date": "2024-01",
        "end_date": "2024-06",
    }
    second = incomplete_experience("Second period.") | {
        "start_date": "2025-01",
        "end_date": None,
    }
    profile = create_profile({"experience_overview": [first, second]})
    candidate = incomplete_experience("Second period.") | {"end_date": "2025-08"}
    output = []

    retried = merge_refinement_topic(
        profile,
        "experience_overview",
        [candidate],
        input_fn=scripted_input(["r"]),
        output_fn=output.append,
    )

    assert retried is RETRY_REFINEMENT
    assert "More than one existing record matches:" in output
    assert "1. MiraclePlus — AI Analyst" in output
    assert "2. MiraclePlus — AI Analyst" in output


def test_refinement_plan_is_all_or_nothing_before_record_updates() -> None:
    record = incomplete_experience("Evaluated AI solutions.")
    profile = create_profile({"experience_overview": [record]})
    dated = {**record, "start_date": "2025-05"}
    unmatched = {**record, "organization_or_project_name": "Other Company"}

    result = merge_refinement_topic(
        profile,
        "experience_overview",
        [dated, unmatched],
        input_fn=scripted_input(["r"]),
        output_fn=lambda _message: None,
    )

    assert result is RETRY_REFINEMENT
    assert profile.experience_overview[0].start_date is None


def test_education_and_skill_refinement_update_without_duplication() -> None:
    education = education_with_dates()
    skill = {
        "skill_name": "Python",
        "category": "Programming language",
        "self_reported_proficiency": None,
    }
    profile = create_profile({"education": [education], "skills": [skill]})

    educated = merge_refinement_topic(
        profile,
        "education",
        [{**education, "start_date": "2022-08", "expected_graduation_date": "2026-05"}],
        input_fn=lambda _prompt: pytest.fail("education identity should match"),
        output_fn=lambda _message: None,
    )
    skilled = merge_refinement_topic(
        educated,
        "skills",
        [{**skill, "self_reported_proficiency": "Advanced"}],
        input_fn=lambda _prompt: pytest.fail("skill identity should match"),
        output_fn=lambda _message: None,
    )

    assert len(skilled.education) == 1
    assert skilled.education[0].start_date == "2022-08"
    assert len(skilled.skills) == 1
    assert skilled.skills[0].self_reported_proficiency == "Advanced"


def test_list_fields_are_deduplicated_in_stable_order() -> None:
    profile = create_profile(
        {"career_preferences": {"interested_fields": ["AI", "NLP"]}}
    )

    merged = merge_candidate_topic(
        profile,
        "career_preferences",
        {
            "interested_fields": ["NLP", "ML", "AI"],
            "preferred_work_activities": [],
            "preferred_industries": [],
            "fields_or_activities_to_avoid": [],
            "currently_considered_roles": [],
        },
        input_fn=scripted_input([]),
        output_fn=lambda _message: None,
    )

    assert merged is not None
    assert merged.career_preferences.interested_fields == ["AI", "NLP", "ML"]


def test_conflict_requires_choice_and_can_keep_existing(tmp_path) -> None:
    path = tmp_path / "profile.json"
    save_profile(create_profile({"basic_profile": {"current_location": "Shanghai"}}), path)
    extractor = FakeExtractor({"basic_profile": {"current_location": "Beijing"}})

    _, output = run_workflow(path, extractor, ["I moved to Beijing.", "y", "e", "y"])

    assert load_profile(path).basic_profile.current_location == "Shanghai"
    assert "Current location conflict for:" in output
    assert "Current location: Shanghai" in output
    assert "New location: Beijing" in output


def test_unresolved_conflict_is_not_written(tmp_path) -> None:
    path = tmp_path / "profile.json"
    original = create_profile({"basic_profile": {"current_location": "Shanghai"}})
    save_profile(original, path)
    extractor = FakeExtractor({"basic_profile": {"current_location": "Beijing"}})

    result, output = run_workflow(path, extractor, ["I moved.", "y", "q", "q"])

    assert result is False
    assert load_profile(path) == original
    assert "Conflict unresolved. The session draft was not changed." in output


def test_multiline_correction_reextracts_with_context_and_reshows_topic(tmp_path) -> None:
    path = tmp_path / "profile.json"
    extractor = FakeExtractor(
        {"skills": [{"skill_name": "Python", "category": "language"}]},
        {"skills": [{"skill_name": "SQL", "category": "database"}]},
    )

    _, output = run_workflow(
        path,
        extractor,
        ["I use Python.", "e", "Please update this skill:", "Use SQL database", ".done", "y", "y", "y"],
    )

    assert extractor.calls[1] == (
        "Please update this skill:\nUse SQL database",
        "skills",
        [{"skill_name": "Python", "category": "language", "self_reported_proficiency": None}],
        "I use Python.",
    )
    assert load_profile(path).skills[0].skill_name == "SQL"
    assert sum(message == "\nSkills" for message in output) == 2


def test_accepted_merge_round_trip_remains_equal(tmp_path) -> None:
    path = tmp_path / "profile.json"
    extractor = FakeExtractor({"skills": [{"skill_name": "Python", "category": "language"}]})

    run_workflow(path, extractor, ["I use Python.", "y", "y"])
    loaded = load_profile(path)

    assert load_profile(save_profile(loaded, path)) == loaded


def test_extractor_error_does_not_damage_existing_profile(tmp_path) -> None:
    path = tmp_path / "profile.json"
    original = create_profile({"basic_profile": {"current_location": "Shanghai"}})
    save_profile(original, path)
    extractor = FakeExtractor(LLMRequestError("API unavailable"))

    with pytest.raises(LLMRequestError, match="API unavailable"):
        run_workflow(path, extractor, ["My background"])

    assert load_profile(path) == original


def incomplete_experience(summary=None) -> dict:
    return {
        "experience_type": "work",
        "organization_or_project_name": "MiraclePlus",
        "title_or_role": "AI Analyst",
        "short_factual_summary": summary,
        "start_date": None,
        "end_date": None,
    }


def test_null_summary_is_displayed_and_can_be_confirmed(tmp_path) -> None:
    path = tmp_path / "profile.json"
    extractor = FakeExtractor({"experience_overview": [incomplete_experience()]})

    result, output = run_workflow(path, extractor, ["My experience", "y", "y"])

    assert result is True
    assert "Summary: Not provided" in output
    profile = load_profile(path)
    assert profile.experience_overview[0].short_factual_summary is None
    assert "What did you do or accomplish as AI Analyst at MiraclePlus?" in profile.open_questions


def test_user_can_edit_and_supply_missing_summary(tmp_path) -> None:
    path = tmp_path / "profile.json"
    extractor = FakeExtractor(
        {"experience_overview": [incomplete_experience()]},
        {"experience_overview": [incomplete_experience("Evaluated AI solutions.")]},
    )

    result, output = run_workflow(
        path,
        extractor,
        ["My experience", "e", "I evaluated AI solutions.", ".done", "y", "y", "y"],
    )

    assert result is True
    assert "Summary: Not provided" in output
    assert "Summary: Evaluated AI solutions." in output
    assert load_profile(path).experience_overview[0].short_factual_summary == (
        "Evaluated AI solutions."
    )


def education_entries(gpa=None):
    return [
        {
            "institution": "UIUC",
            "degree": "Bachelor of Science",
            "field_of_study": "Computer Science and Advertising",
            "start_date": None,
            "expected_graduation_date": None,
            "gpa": gpa,
        },
        {
            "institution": "UIUC",
            "degree": "Bachelor of Science",
            "field_of_study": "Economics",
            "start_date": None,
            "expected_graduation_date": None,
            "gpa": gpa,
        },
        {
            "institution": "UCLA",
            "degree": "Master of Engineering",
            "field_of_study": "Artificial Intelligence",
            "start_date": None,
            "expected_graduation_date": None,
            "gpa": None,
        },
    ]


def test_gpa_only_correction_preserves_every_other_education_fact(tmp_path) -> None:
    path = tmp_path / "profile.json"
    corrected = education_entries()
    corrected[0]["gpa"] = "3.7"
    corrected[1]["gpa"] = "3.7"
    extractor = FakeExtractor({"education": education_entries()}, {"education": corrected})

    result, output = run_workflow(
        path,
        extractor,
        [
            "UIUC dual degrees and UCLA AI master's.",
            "e",
            "Set the UIUC GPA to 3.7.",
            ".done",
            "y",
            "y",
            "y",
        ],
    )

    assert result is True
    saved = load_profile(path).education
    assert [(item.institution, item.degree, item.field_of_study) for item in saved] == [
        (item["institution"], item["degree"], item["field_of_study"])
        for item in education_entries()
    ]
    assert [item.gpa for item in saved] == ["3.7", "3.7", None]
    assert "Proposed changes:" in output


def test_unsupported_correction_values_are_rejected_and_candidate_is_unchanged(tmp_path) -> None:
    path = tmp_path / "profile.json"
    invented = education_entries()
    invented[0]["field_of_study"] = "Mathematics"
    invented[2]["field_of_study"] = "Data Science"
    extractor = FakeExtractor({"education": education_entries()}, {"education": invented})

    result, output = run_workflow(
        path,
        extractor,
        ["UIUC and UCLA education", "e", "Only add GPA 3.7", ".done", "q"],
    )

    assert result is False
    assert not path.exists()
    assert any("not supported" in message for message in output)


def test_failed_correction_returns_to_menu_and_can_retry(tmp_path) -> None:
    path = tmp_path / "profile.json"
    extractor = FakeExtractor(
        {"skills": [{"skill_name": "Python", "category": "language"}]},
        {},
        {"skills": [{"skill_name": "SQL", "category": "database"}]},
    )

    result, output = run_workflow(
        path,
        extractor,
        [
            "I use Python.",
            "e",
            "Please update the skill as follows:",
            ".done",
            "e",
            "Replace Python with SQL database",
            ".done",
            "y",
            "y",
            "y",
        ],
    )

    assert result is True
    assert load_profile(path).skills[0].skill_name == "SQL"
    assert any("correction did not contain skills" in message for message in output)


def test_empty_multiline_correction_can_be_cancelled_without_extraction(tmp_path) -> None:
    path = tmp_path / "profile.json"
    extractor = FakeExtractor({"skills": [{"skill_name": "Python", "category": "language"}]})

    result, output = run_workflow(
        path,
        extractor,
        ["I use Python.", "e", "", ".done", ".cancel", "q"],
    )

    assert result is False
    assert len(extractor.calls) == 1
    assert "Correction cannot be empty. Enter text, then finish with .done." in output


def test_final_cancel_discards_accepted_session_draft(tmp_path) -> None:
    path = tmp_path / "profile.json"
    original = create_profile({"basic_profile": {"current_location": "Shanghai"}})
    save_profile(original, path)
    extractor = FakeExtractor({"skills": [{"skill_name": "Python", "category": "language"}]})

    result, output = run_workflow(path, extractor, ["I use Python.", "y", "n"])

    assert result is False
    assert load_profile(path) == original
    assert "Session cancelled. The original Profile was not changed." in output
