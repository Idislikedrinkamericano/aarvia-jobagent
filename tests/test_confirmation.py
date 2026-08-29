from copy import deepcopy

import pytest

from aarvia import create_profile, load_profile, save_profile
from aarvia.confirmation import merge_candidate_topic, run_narrative_discovery
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

    result, output = run_workflow(path, extractor, ["I use Python.", "y"])

    assert result is True
    assert load_profile(path).skills[0].skill_name == "Python"
    assert "Profile updated successfully." in output


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

    _, output = run_workflow(path, extractor, ["I moved to Beijing.", "y", "e"])

    assert load_profile(path).basic_profile.current_location == "Shanghai"
    assert "Existing value: \"Shanghai\"" in output
    assert "Candidate value: \"Beijing\"" in output


def test_unresolved_conflict_is_not_written(tmp_path) -> None:
    path = tmp_path / "profile.json"
    original = create_profile({"basic_profile": {"current_location": "Shanghai"}})
    save_profile(original, path)
    extractor = FakeExtractor({"basic_profile": {"current_location": "Beijing"}})

    result, output = run_workflow(path, extractor, ["I moved.", "y", "q"])

    assert result is False
    assert load_profile(path) == original
    assert "Conflict unresolved. Candidate information was not saved." in output


def test_correction_reextracts_only_topic_and_reshows_it(tmp_path) -> None:
    path = tmp_path / "profile.json"
    extractor = FakeExtractor(
        {"skills": [{"skill_name": "Python", "category": "language"}]},
        {"skills": [{"skill_name": "SQL", "category": "database"}]},
    )

    _, output = run_workflow(
        path,
        extractor,
        ["I use Python.", "e", "I meant SQL.", "y"],
    )

    assert extractor.calls[1] == ("I meant SQL.", "skills")
    assert load_profile(path).skills[0].skill_name == "SQL"
    assert sum(message == "\nSkills" for message in output) == 2


def test_accepted_merge_round_trip_remains_equal(tmp_path) -> None:
    path = tmp_path / "profile.json"
    extractor = FakeExtractor({"skills": [{"skill_name": "Python", "category": "language"}]})

    run_workflow(path, extractor, ["I use Python.", "y"])
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
