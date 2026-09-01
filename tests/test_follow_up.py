from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from aarvia import create_profile, load_profile, save_profile
from aarvia.discovery_state import (
    DISCOVERY_PATHS,
    DiscoveryState,
    FollowUpStatus,
    load_discovery_state,
    save_profile_and_state,
    state_path_for,
)
from aarvia.follow_up import (
    CANCEL_ANSWER,
    END_SESSION,
    AdaptiveFollowUpWorkflow,
    FOLLOW_UP_QUESTIONS,
    missing_follow_up_topics,
    missing_information_paths,
    project_candidate_to_path,
    run_follow_up_discovery,
    select_next_topic,
)
from aarvia.confirmation import _candidate_diff, _validate_supported_changes
from aarvia.constraint_evidence import apply_constraint_evidence
from aarvia.llm_client import LLMRequestError
from aarvia.profile import ProfileValidationError

from test_interview import scripted_input


class FollowUpExtractor:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []
        self.correction_calls = []

    def extract_follow_up(self, answer, **context):
        self.calls.append((answer, deepcopy(context)))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)

    def extract_correction(self, correction, **context):
        self.correction_calls.append((correction, deepcopy(context)))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)


def existing_profile():
    return create_profile(
        {
            "education": [
                {
                    "institution": "Example University",
                    "degree": "BS",
                    "field_of_study": "Computer Science",
                    "start_date": "2020-09",
                    "expected_graduation_date": "2024-06",
                    "gpa": None,
                }
            ],
            "experience_overview": [
                {
                    "experience_type": "work",
                    "organization_or_project_name": "Example Co",
                    "title_or_role": "Analyst",
                    "short_factual_summary": "Analyzed data.",
                    "start_date": "2024-07",
                    "end_date": None,
                }
            ],
            "skills": [
                {
                    "skill_name": "Python",
                    "category": "language",
                    "self_reported_proficiency": "advanced",
                }
            ],
        }
    )


def basic_candidate(name=None):
    return {
        "basic_profile": {
            "name": name,
            "current_location": "Example City",
            "current_status": "Graduate student",
        }
    }


def constraints_profile():
    data = existing_profile().to_dict()
    data.pop("open_questions")
    data["basic_profile"] = {
        "current_location": "Example City",
        "current_status": "Graduate student",
    }
    data["career_preferences"] = {
        "interested_fields": ["AI"],
        "preferred_work_activities": ["Building products"],
        "preferred_industries": ["Technology"],
        "fields_or_activities_to_avoid": [],
        "currently_considered_roles": ["AI Engineer"],
    }
    return create_profile(data)


def constraints_candidate(**overrides):
    value = {
        "target_locations": [],
        "work_authorization_or_visa_constraints": None,
        "work_arrangement_preference": None,
        "employment_type_preference": "internship",
        "target_start_date": "2027-06",
        "other_constraints": [],
    }
    value.update(overrides)
    return {"constraints": value}


def partial_preferences_profile(*, skill_proficiency="advanced"):
    data = existing_profile().to_dict()
    data.pop("open_questions")
    data["basic_profile"] = {
        "name": "Test User",
        "current_location": "Example City",
        "current_status": "Graduate student",
    }
    data["career_preferences"] = {
        "interested_fields": ["AI"],
        "preferred_work_activities": ["Building products"],
        "preferred_industries": [],
        "fields_or_activities_to_avoid": [],
        "currently_considered_roles": ["AI Engineer"],
    }
    data["constraints"] = {
        "target_locations": ["United States"],
        "work_authorization_or_visa_constraints": "Sponsorship required",
        "work_arrangement_preference": "flexible",
        "employment_type_preference": "internship",
        "target_start_date": "2027-06",
        "other_constraints": [],
    }
    data["skills"][0]["self_reported_proficiency"] = skill_proficiency
    return create_profile(data)


def industries_candidate(*industries):
    return {"career_preferences": {"preferred_industries": list(industries)}}


def experience_record(
    organization, role, experience_type, summary, start_date, end_date
):
    return {
        "experience_type": experience_type,
        "organization_or_project_name": organization,
        "title_or_role": role,
        "short_factual_summary": summary,
        "start_date": start_date,
        "end_date": end_date,
    }


def seven_experience_profile():
    data = partial_preferences_profile().to_dict()
    data.pop("open_questions")
    data["career_preferences"]["preferred_industries"] = ["Technology"]
    data["experience_overview"] = [
        experience_record(
            "Northstar Labs", "AI Analyst and Investment Mentor", "internship",
            "Evaluated AI solutions.", None, None,
        ),
        experience_record(
            "Horizon Internship Program", "LMS System Designer", "internship",
            "Designed LMS structures.", None, None,
        ),
        experience_record(
            "Regional Public Health Lab", "Data Scientist", "work",
            "Processed public-health datasets.", None, None,
        ),
        experience_record(
            "Sample AI Studio", "AI Product Analyst", "work",
            "Supported AI product development.", "2025-05", "2025-08",
        ),
        experience_record(
            "Example State University", "CS124 Course Assistant", "teaching_assistantship",
            "Taught programming concepts.", "2024-07", "2025-05",
        ),
        experience_record(
            "Beacon data platform", "Developer", "project",
            "Developed a data platform.", "2024-09", "2026-01",
        ),
        experience_record(
            "U.S. travel recommendation system", "Developer", "project",
            "Developed a recommendation system.", "2024-09", "2025-01",
        ),
    ]
    return create_profile(data)


def complete_follow_up_profile():
    data = partial_preferences_profile().to_dict()
    data.pop("open_questions")
    data["career_preferences"]["preferred_industries"] = ["Technology"]
    return create_profile(data)


def complete_discovery_state():
    state = DiscoveryState()
    for path in DISCOVERY_PATHS:
        state.mark(path, FollowUpStatus.ANSWERED)
    return state


def three_experience_date_enrichments():
    return {
        "experience_overview": [
            experience_record(
                "Northstar Labs", "AI Analyst and Investment Mentor", "internship",
                "Evaluated AI solutions.", "2025-05", "2025-08",
            ),
            experience_record(
                "Horizon Internship Program", "LMS System Designer", "internship",
                "Designed LMS structures.", "2025-05", "2026-01",
            ),
            experience_record(
                "Regional Public Health Lab", "Data Scientist", "work",
                "Processed public-health datasets.", "2024-06", "2024-08",
            ),
        ]
    }


def run_follow_up(path, extractor, answers, *, debug=False, debug_full=False):
    output = []
    result = run_follow_up_discovery(
        path,
        extractor,
        input_fn=scripted_input(answers),
        output_fn=output.append,
        debug_extraction=debug,
        debug_full_profile=debug_full,
    )
    return result, output


def test_existing_profile_loads_and_basic_profile_is_first(tmp_path) -> None:
    path = save_profile(existing_profile(), tmp_path / "profile.json")

    assert load_profile(path) == existing_profile()
    assert select_next_topic(load_profile(path), DiscoveryState()) == "basic_profile.current_location"


def test_missing_profile_has_clear_error(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="Follow-up Profile does not exist"):
        run_follow_up_discovery(tmp_path / "missing.json", FollowUpExtractor())


def test_only_one_topic_question_is_shown_before_quit(tmp_path) -> None:
    path = save_profile(existing_profile(), tmp_path / "profile.json")
    extractor = FollowUpExtractor(basic_candidate())

    result, output = run_follow_up(
        path, extractor, ["Example City graduate student", ".done", "q"]
    )

    assert result is False
    assert any(FOLLOW_UP_QUESTIONS["basic_profile.current_location"] in message for message in output)
    assert not any(
        FOLLOW_UP_QUESTIONS["career_preferences.interested_fields"] in message
        for message in output
    )


def test_follow_up_context_contains_question_profile_and_session_draft(tmp_path) -> None:
    path = save_profile(existing_profile(), tmp_path / "profile.json")
    extractor = FollowUpExtractor(basic_candidate())

    run_follow_up(path, extractor, ["Example City graduate student", ".done", "q"])

    answer, context = extractor.calls[0]
    assert answer == "Example City graduate student"
    assert context["question"] == FOLLOW_UP_QUESTIONS["basic_profile.current_location"]
    assert context["topic"] == "basic_profile"
    assert context["field_path"] == "basic_profile.current_location"
    assert context["formal_profile"]["education"]
    assert context["session_draft"]["experience_overview"]


@pytest.mark.parametrize(
    ("candidate", "forbidden_topic"),
    [
        (
            {
                **basic_candidate(),
                "career_preferences": {"interested_fields": ["AI"]},
            },
            "career_preferences",
        ),
        (
            {
                "career_preferences": {"interested_fields": ["AI"]},
                "education": [{
                    "institution": "Other", "degree": "BS", "field_of_study": "Math",
                    "start_date": None, "expected_graduation_date": None, "gpa": None,
                }],
            },
            "education",
        ),
        (
            {
                "constraints": {"target_locations": ["Example City"]},
                "experience_overview": [{
                    "experience_type": "work", "organization_or_project_name": "Other",
                    "title_or_role": "Engineer", "short_factual_summary": None,
                    "start_date": None, "end_date": None,
                }],
            },
            "experience_overview",
        ),
    ],
)
def test_follow_up_candidate_cannot_modify_another_topic(
    tmp_path, candidate, forbidden_topic
) -> None:
    path = save_profile(existing_profile(), tmp_path / "profile.json")
    original = load_profile(path)
    extractor = FollowUpExtractor(candidate)

    result, output = run_follow_up(
        path, extractor, ["My answer", ".done", "q"]
    )

    assert result is False
    assert load_profile(path) == original
    assert any("must contain only" in message for message in output)
    assert forbidden_topic in candidate


def test_confirmed_none_is_not_selected_again(tmp_path) -> None:
    profile = existing_profile()
    state = DiscoveryState()
    state.mark("basic_profile.current_location", FollowUpStatus.CONFIRMED_NONE)

    assert select_next_topic(profile, state) == "basic_profile.current_status"


def test_empty_candidate_requires_retry_and_none_is_an_explicit_command(tmp_path) -> None:
    path = save_profile(existing_profile(), tmp_path / "profile.json")

    result, output = run_follow_up(
        path,
        FollowUpExtractor({}),
        ["I live in Example City.", ".done", ":none", ":finish", "y"],
    )

    assert result is True
    state = load_discovery_state(state_path_for(path))
    assert state.fields["basic_profile.current_location"] == FollowUpStatus.CONFIRMED_NONE
    joined = "\n".join(output)
    assert "could not extract an answer" in joined
    assert "Confirm that you have nothing" not in joined


def test_skipped_and_declined_can_be_selected_in_a_future_session() -> None:
    profile = existing_profile()
    for status in (FollowUpStatus.SKIPPED, FollowUpStatus.DECLINED):
        state = DiscoveryState()
        state.mark("basic_profile.current_location", status)
        assert select_next_topic(profile, state) == "basic_profile.current_location"
        assert select_next_topic(
            profile, state, {"basic_profile.current_location"}
        ) == "basic_profile.current_status"


def test_declined_does_not_write_false_profile_facts(tmp_path) -> None:
    path = save_profile(existing_profile(), tmp_path / "profile.json")
    original = load_profile(path)

    result, _ = run_follow_up(
        path,
        FollowUpExtractor(),
        [":decline", ":finish", "y"],
    )

    assert result is True
    assert load_profile(path) == original
    assert load_discovery_state(state_path_for(path)).fields["basic_profile.current_location"] == (
        FollowUpStatus.DECLINED
    )


def test_multiline_correction_shows_diff_and_can_be_applied(tmp_path) -> None:
    path = save_profile(existing_profile(), tmp_path / "profile.json")
    extractor = FollowUpExtractor(
        basic_candidate(),
        {"basic_profile": {"current_location": "Beijing"}},
    )

    result, output = run_follow_up(
        path,
        extractor,
        [
            "Example City",
            "Graduate student",
            ".done",
            "e",
            "My current location is Beijing.",
            ".done",
            "y",
            "y",
            ":finish",
            "y",
        ],
    )

    assert result is True
    assert load_profile(path).basic_profile.current_location == "Beijing"
    assert "Proposed changes:" in output
    assert extractor.correction_calls[0][0] == "My current location is Beijing."
    assert extractor.correction_calls[0][1]["field_path"] == (
        "basic_profile.current_location"
    )


def test_failed_correction_returns_to_confirmation_menu(tmp_path) -> None:
    path = save_profile(existing_profile(), tmp_path / "profile.json")
    extractor = FollowUpExtractor(
        basic_candidate(),
        LLMRequestError("provider unavailable"),
    )

    result, output = run_follow_up(
        path,
        extractor,
        [
            "Example City graduate student",
            ".done",
            "e",
            "Use Ada",
            ".done",
            "q",
        ],
    )

    assert result is False
    assert any("Correction failed" in message for message in output)
    assert load_profile(path).basic_profile.current_location is None


def test_quit_and_final_no_leave_profile_and_state_unchanged(tmp_path) -> None:
    for suffix, answers in (
        ("quit", ["Example City graduate student", ".done", "q"]),
        ("no", ["Example City graduate student", ".done", "y", ":finish", "n"]),
    ):
        path = save_profile(existing_profile(), tmp_path / f"{suffix}.json")
        original = load_profile(path)
        result, _ = run_follow_up(path, FollowUpExtractor(basic_candidate()), answers)
        assert result is False
        assert load_profile(path) == original
        assert not state_path_for(path).exists()


def test_final_yes_saves_profile_and_state_consistently(tmp_path) -> None:
    path = save_profile(existing_profile(), tmp_path / "profile.json")

    result, _ = run_follow_up(
        path,
        FollowUpExtractor(basic_candidate()),
        ["Example City graduate student", ".done", "y", ":finish", "y"],
    )

    assert result is True
    assert load_profile(path).basic_profile.current_location == "Example City"
    saved_state = load_discovery_state(state_path_for(path))
    assert saved_state.fields["basic_profile.current_location"] == (
        FollowUpStatus.ANSWERED
    )
    assert saved_state.fields["basic_profile.current_status"] == FollowUpStatus.UNANSWERED


def test_second_file_failure_rolls_back_both_files(tmp_path, monkeypatch) -> None:
    import aarvia.discovery_state as state_module

    path = save_profile(existing_profile(), tmp_path / "profile.json")
    state_path = state_path_for(path)
    old_state = DiscoveryState()
    save_profile_and_state(existing_profile(), path, old_state, state_path)
    changed_data = existing_profile().to_dict()
    changed_data.pop("open_questions")
    changed_data["basic_profile"] = {"current_location": "Example City"}
    changed = create_profile(changed_data)
    new_state = DiscoveryState()
    new_state.mark("basic_profile.current_location", FollowUpStatus.ANSWERED)
    real_replace = state_module.os.replace
    failed = False

    def fail_state_once(source, target):
        nonlocal failed
        if not failed and target == state_path:
            failed = True
            raise OSError("simulated state save failure")
        return real_replace(source, target)

    monkeypatch.setattr(state_module.os, "replace", fail_state_once)
    with pytest.raises(OSError, match="simulated"):
        save_profile_and_state(changed, path, new_state, state_path)

    assert load_profile(path) == existing_profile()
    assert load_discovery_state(state_path) == old_state


def test_keyboard_interrupt_during_second_file_save_rolls_back_both_files(
    tmp_path, monkeypatch
) -> None:
    import aarvia.discovery_state as state_module

    path = save_profile(existing_profile(), tmp_path / "profile.json")
    state_path = state_path_for(path)
    old_state = DiscoveryState()
    save_profile_and_state(existing_profile(), path, old_state, state_path)
    changed_data = existing_profile().to_dict()
    changed_data.pop("open_questions")
    changed_data["basic_profile"] = {"current_location": "Example City"}
    changed = create_profile(changed_data)
    new_state = DiscoveryState()
    new_state.mark("basic_profile.current_location", FollowUpStatus.ANSWERED)
    real_replace = state_module.os.replace
    interrupted = False

    def interrupt_state_once(source, target):
        nonlocal interrupted
        if not interrupted and target == state_path:
            interrupted = True
            raise KeyboardInterrupt
        return real_replace(source, target)

    monkeypatch.setattr(state_module.os, "replace", interrupt_state_once)
    with pytest.raises(KeyboardInterrupt):
        save_profile_and_state(changed, path, new_state, state_path)

    assert load_profile(path) == existing_profile()
    assert load_discovery_state(state_path) == old_state


def test_old_profile_without_state_loads_with_default_state(tmp_path) -> None:
    path = save_profile(existing_profile(), tmp_path / "legacy.json")

    assert load_discovery_state(state_path_for(path)) == DiscoveryState()


def test_reload_does_not_repeat_answered_topic(tmp_path) -> None:
    path = save_profile(existing_profile(), tmp_path / "profile.json")
    state = DiscoveryState()
    state.mark("basic_profile.current_location", FollowUpStatus.CONFIRMED_NONE)
    save_profile_and_state(
        existing_profile(),
        path,
        state,
        state_path_for(path),
    )

    reloaded = load_discovery_state(state_path_for(path))
    assert select_next_topic(load_profile(path), reloaded) == "basic_profile.current_status"


def test_missing_topics_are_in_deterministic_priority_order() -> None:
    assert missing_follow_up_topics(existing_profile())[:3] == [
        "basic_profile.current_location",
        "basic_profile.current_status",
        "career_preferences.interested_fields",
    ]


def test_initial_constraints_recover_two_explicit_target_locations(tmp_path) -> None:
    path = save_profile(constraints_profile(), tmp_path / "profile.json")
    extractor = FollowUpExtractor(constraints_candidate())
    answer = (
        "Target locations: United States and China. Employment type: internship. "
        "I can start in June 2027."
    )

    result, output = run_follow_up(
        path, extractor, [answer, ".done", "y", ":finish", "y"]
    )

    assert result is True
    assert load_profile(path).constraints.target_locations == ["United States", "China"]
    assert "Target locations:" in output
    assert "- United States" in output
    assert "- China" in output


def test_correction_can_add_only_target_locations(tmp_path) -> None:
    path = save_profile(constraints_profile(), tmp_path / "profile.json")
    extractor = FollowUpExtractor(constraints_candidate(), constraints_candidate())

    result, output = run_follow_up(
        path,
        extractor,
        [
            "My target location is United States.",
            ".done",
            "e",
            'target_locations = ["United States", "China"]',
            ".done",
            "y",
            "y",
            ":finish",
            "y",
        ],
    )

    assert result is True
    assert load_profile(path).constraints.target_locations == ["United States", "China"]
    assert "Target locations:" in output
    assert "- After: United States, China" in output


def test_target_location_correction_cannot_change_sibling_sponsorship(tmp_path) -> None:
    path = save_profile(constraints_profile(), tmp_path / "profile.json")
    corrected = constraints_candidate(
        target_locations=["United States", "China"],
        work_authorization_or_visa_constraints="sponsorship required",
    )
    extractor = FollowUpExtractor(constraints_candidate(), corrected)

    result, _ = run_follow_up(
        path,
        extractor,
        [
            "My target location is United States.",
            ".done",
            "e",
            'target_locations = ["United States", "China"]',
            "sponsorship required",
            ".done",
            "y",
            "y",
            ":finish",
            "y",
        ],
    )

    constraints = load_profile(path).constraints
    assert result is True
    assert constraints.target_locations == ["United States", "China"]
    assert constraints.work_authorization_or_visa_constraints is None


def test_list_patch_diff_and_evidence_validation() -> None:
    before = constraints_candidate()["constraints"]
    after = constraints_candidate(
        target_locations=["United States", "China"]
    )["constraints"]
    changes = _candidate_diff(before, after, "constraints")

    assert [(path, new) for path, _old, new in changes] == [
        ("constraints.target_locations[0]", "United States"),
        ("constraints.target_locations[1]", "China"),
    ]
    _validate_supported_changes(
        changes,
        "United States and China",
        'target_locations = ["United States", "China"]',
    )


def test_evidence_validator_rejects_unstated_location() -> None:
    changes = [("constraints.target_locations[0]", None, "Atlantis")]

    with pytest.raises(ProfileValidationError, match="not supported"):
        _validate_supported_changes(
            changes,
            "United States and China",
            'target_locations = ["United States", "China"]',
        )


def test_field_projection_prevents_sibling_constraint_changes(tmp_path) -> None:
    path = save_profile(constraints_profile(), tmp_path / "profile.json")
    corrected = constraints_candidate(
        work_authorization_or_visa_constraints="sponsorship required",
        work_arrangement_preference="onsite",
    )
    extractor = FollowUpExtractor(constraints_candidate(), corrected)

    result, _ = run_follow_up(
        path,
        extractor,
        [
            "My target location is United States.",
            ".done",
            "e",
            'target_locations = ["United States", "China"]',
            "sponsorship required",
            ".done",
            "y",
            "y",
            ":finish",
            "y",
        ],
    )

    assert result is True
    saved = load_profile(path)
    assert saved.constraints.target_locations == ["United States", "China"]
    assert saved.constraints.work_authorization_or_visa_constraints is None
    assert saved.constraints.work_arrangement_preference is None


def test_flexible_and_named_month_are_normalized_without_guessing_season() -> None:
    raw = constraints_candidate(
        work_arrangement_preference=None,
        target_start_date=None,
    )
    normalized, patch = apply_constraint_evidence(
        raw,
        "Onsite, hybrid, or remote are all acceptable. I can start in June 2027.",
    )

    assert normalized["constraints"]["work_arrangement_preference"] == "flexible"
    assert normalized["constraints"]["target_start_date"] == "2027-06"
    assert patch == {
        "work_arrangement_preference": "flexible",
        "target_start_date": "2027-06",
    }

    summer, summer_patch = apply_constraint_evidence(
        constraints_candidate(target_start_date="2027-06"),
        "I can start in Summer 2027.",
    )
    assert summer["constraints"]["target_start_date"] is None
    assert summer_patch["target_start_date"] is None


def test_follow_up_debug_shows_pipeline_layers_and_redacts_key(tmp_path) -> None:
    path = save_profile(constraints_profile(), tmp_path / "profile.json")
    extractor = FollowUpExtractor(constraints_candidate(), constraints_candidate())
    extractor.diagnostics = SimpleNamespace(
        raw_output='{"constraints":{"target_locations":[]}} secret-key'
    )
    extractor.settings = SimpleNamespace(api_key="secret-key")

    result, output = run_follow_up(
        path,
        extractor,
        [
            "My target location is United States.",
            ".done",
            "e",
            'target_locations = ["United States", "China"]',
            ".done",
            "y",
            "y",
            ":finish",
            "n",
        ],
        debug=True,
    )

    joined = "\n".join(output)
    assert result is False
    for label in (
        "follow-up extraction stage",
        "raw provider output",
        "selected field path",
        "allowed field paths",
        "normalized topic Candidate",
        "field projection result",
        "constraint evidence patch",
        "correction patch",
        "evidence-validation result",
        "merge result",
    ):
        assert label in joined
    assert "secret-key" not in joined
    assert "[REDACTED]" in joined
    assert "Technical path: constraints.target_locations[1]" in joined
    assert "full Profile merge result" not in joined
    assert "Example University" not in joined


def test_debug_full_profile_is_opt_in(tmp_path) -> None:
    path = save_profile(existing_profile(), tmp_path / "profile.json")
    extractor = FollowUpExtractor(basic_candidate())

    result, output = run_follow_up(
        path,
        extractor,
        ["Example City graduate student", ".done", "y", ":finish", "n"],
        debug=True,
        debug_full=True,
    )

    joined = "\n".join(output)
    assert result is False
    assert "full Profile merge result" in joined
    assert "Example University" in joined


def test_normal_follow_up_output_does_not_expose_internal_schema(tmp_path) -> None:
    path = save_profile(existing_profile(), tmp_path / "profile.json")

    result, output = run_follow_up(
        path,
        FollowUpExtractor(basic_candidate()),
        ["Example City graduate student", ".done", "y", ":finish", "n"],
    )

    joined = "\n".join(output)
    assert result is False
    for internal in ("basic_profile", "open_questions", "current_location", "null", "[0]"):
        assert internal not in joined


def test_partial_career_preferences_asks_preferred_industries_first(tmp_path) -> None:
    path = save_profile(partial_preferences_profile(), tmp_path / "profile.json")
    state_path_for(path).write_text(
        json.dumps({
            "version": 1,
            "topics": {
                "basic_profile": "answered",
                "career_preferences": "answered",
                "constraints": "answered",
                "education_details": "answered",
                "experience_details": "answered",
                "skill_proficiency": "answered",
            },
        }),
        encoding="utf-8",
    )
    output = []

    result = run_follow_up_discovery(
        path,
        FollowUpExtractor(industries_candidate("Healthcare")),
        input_fn=scripted_input(["Healthcare", ".done", "q"]),
        output_fn=output.append,
    )

    assert result is False
    assert any(
        FOLLOW_UP_QUESTIONS["career_preferences.preferred_industries"] in line
        for line in output
    )


def test_parent_answered_does_not_hide_missing_child_during_v1_migration(tmp_path) -> None:
    profile = partial_preferences_profile()
    state_path = tmp_path / "legacy.discovery.json"
    state_path.write_text(
        json.dumps({"version": 1, "topics": {"career_preferences": "answered"}}),
        encoding="utf-8",
    )

    state = load_discovery_state(
        state_path,
        profile=profile,
        missing_paths=set(missing_information_paths(profile)),
    )

    assert state.fields["career_preferences.interested_fields"] == FollowUpStatus.ANSWERED
    assert state.fields["career_preferences.preferred_work_activities"] == FollowUpStatus.ANSWERED
    assert state.fields["career_preferences.currently_considered_roles"] == FollowUpStatus.ANSWERED
    assert state.fields["career_preferences.preferred_industries"] == FollowUpStatus.UNANSWERED
    assert select_next_topic(profile, state) == "career_preferences.preferred_industries"


def test_answering_preferred_industries_saves_value_and_answered_state(tmp_path) -> None:
    path = save_profile(partial_preferences_profile(), tmp_path / "profile.json")

    result, _ = run_follow_up(
        path,
        FollowUpExtractor(industries_candidate("Healthcare", "Technology")),
        ["Healthcare and Technology", ".done", "y", "y"],
    )

    assert result is True
    assert load_profile(path).career_preferences.preferred_industries == [
        "Healthcare", "Technology"
    ]
    state = load_discovery_state(state_path_for(path))
    assert state.fields["career_preferences.preferred_industries"] == FollowUpStatus.ANSWERED


def test_preferred_industries_none_stops_reasking_and_is_summarized(tmp_path) -> None:
    path = save_profile(partial_preferences_profile(), tmp_path / "profile.json")

    result, output = run_follow_up(path, FollowUpExtractor(), [":none", "y"])

    assert result is True
    assert load_profile(path).career_preferences.preferred_industries == []
    state = load_discovery_state(state_path_for(path))
    assert state.fields["career_preferences.preferred_industries"] == (
        FollowUpStatus.CONFIRMED_NONE
    )
    assert select_next_topic(load_profile(path), state) is None
    joined = "\n".join(output)
    assert "Confirmed as not provided:\n- Preferred industries" in joined
    assert "Still missing:\n- None" in joined


def test_preferred_industries_skip_is_reachable_next_session(tmp_path) -> None:
    path = save_profile(partial_preferences_profile(), tmp_path / "profile.json")

    result, _ = run_follow_up(path, FollowUpExtractor(), [":skip", "y"])

    assert result is True
    state = load_discovery_state(state_path_for(path))
    assert state.fields["career_preferences.preferred_industries"] == FollowUpStatus.SKIPPED
    assert select_next_topic(load_profile(path), state) == (
        "career_preferences.preferred_industries"
    )


def test_preferred_industries_decline_does_not_create_false_fact(tmp_path) -> None:
    path = save_profile(partial_preferences_profile(), tmp_path / "profile.json")

    result, output = run_follow_up(path, FollowUpExtractor(), [":decline", "y"])

    assert result is True
    assert load_profile(path).career_preferences.preferred_industries == []
    state = load_discovery_state(state_path_for(path))
    assert state.fields["career_preferences.preferred_industries"] == FollowUpStatus.DECLINED
    assert "Declined:\n- Preferred industries" in "\n".join(output)


def test_skill_proficiency_none_preserves_existing_skills_and_updates_summary(tmp_path) -> None:
    profile = partial_preferences_profile(skill_proficiency=None)
    data = profile.to_dict()
    data.pop("open_questions")
    data["career_preferences"]["preferred_industries"] = ["Technology"]
    path = save_profile(create_profile(data), tmp_path / "profile.json")
    original_skills = load_profile(path).skills

    result, output = run_follow_up(path, FollowUpExtractor(), [":none", "y"])

    assert result is True
    assert load_profile(path).skills == original_skills
    state = load_discovery_state(state_path_for(path))
    assert state.fields["skill_proficiency"] == FollowUpStatus.CONFIRMED_NONE
    assert "Confirmed as not provided:\n- Skill proficiency" in "\n".join(output)


def test_every_still_missing_path_has_a_reachable_question() -> None:
    profile = partial_preferences_profile()
    state = DiscoveryState()
    missing = missing_information_paths(profile)

    assert missing == ["career_preferences.preferred_industries"]
    assert all(path in FOLLOW_UP_QUESTIONS for path in missing)
    assert select_next_topic(profile, state) == missing[0]


def test_v1_state_saves_as_versioned_v2_fields(tmp_path) -> None:
    profile = partial_preferences_profile()
    path = save_profile(profile, tmp_path / "profile.json")
    state_path = state_path_for(path)
    state_path.write_text(
        json.dumps({"version": 1, "topics": {"career_preferences": "answered"}}),
        encoding="utf-8",
    )
    migrated = load_discovery_state(
        state_path,
        profile=profile,
        missing_paths=set(missing_information_paths(profile)),
    )

    save_profile_and_state(profile, path, migrated, state_path)

    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert raw["schema"] == "aarvia.discovery_state"
    assert raw["version"] == 2
    assert set(raw["fields"]) == set(DISCOVERY_PATHS)
    assert raw["fields"]["career_preferences.preferred_industries"] == "unanswered"


@pytest.mark.parametrize(
    ("answer", "industries"),
    [
        (
            "I am most interested in AI and software technology companies, especially "
            "companies building AI products, developer tools, or enterprise AI systems.",
            [
                "Artificial Intelligence",
                "Software Technology",
                "Developer Tools",
                "Enterprise Software",
            ],
        ),
        (
            "My preferred industries are artificial intelligence, software technology, "
            "developer tools, and enterprise software.",
            [
                "Artificial Intelligence",
                "Software Technology",
                "Developer Tools",
                "Enterprise Software",
            ],
        ),
    ],
)
def test_preferred_industries_natural_answers_are_saved(
    tmp_path, answer, industries
) -> None:
    path = save_profile(partial_preferences_profile(), tmp_path / "profile.json")
    extractor = FollowUpExtractor(industries_candidate(*industries))

    result, _ = run_follow_up(
        path, extractor, [answer, ".done", "y", "y"]
    )

    assert result is True
    assert load_profile(path).career_preferences.preferred_industries == industries
    assert extractor.calls[0][1]["topic"] == "career_preferences"
    assert extractor.calls[0][1]["field_path"] == (
        "career_preferences.preferred_industries"
    )


def test_preferred_industries_projection_preserves_sibling_preferences(tmp_path) -> None:
    original = partial_preferences_profile()
    path = save_profile(original, tmp_path / "profile.json")
    provider_result = {
        "career_preferences": {
            "interested_fields": ["Unrequested field"],
            "preferred_work_activities": ["Unrequested activity"],
            "preferred_industries": ["Artificial Intelligence"],
            "fields_or_activities_to_avoid": ["Unrequested avoidance"],
            "currently_considered_roles": ["Unrequested role"],
        }
    }

    result, _ = run_follow_up(
        path,
        FollowUpExtractor(provider_result),
        ["Artificial intelligence companies", ".done", "y", "y"],
    )

    saved = load_profile(path)
    assert result is True
    assert saved.career_preferences.preferred_industries == ["Artificial Intelligence"]
    assert saved.career_preferences.interested_fields == (
        original.career_preferences.interested_fields
    )
    assert saved.career_preferences.preferred_work_activities == (
        original.career_preferences.preferred_work_activities
    )
    assert saved.career_preferences.currently_considered_roles == (
        original.career_preferences.currently_considered_roles
    )


def test_provider_industry_survives_field_projection() -> None:
    projected = project_candidate_to_path(
        industries_candidate("Artificial Intelligence", "Developer Tools"),
        "career_preferences.preferred_industries",
    )

    assert projected == industries_candidate("Artificial Intelligence", "Developer Tools")


def test_explicit_answer_with_empty_provider_result_is_retryable_not_none(tmp_path) -> None:
    path = save_profile(partial_preferences_profile(), tmp_path / "profile.json")
    extractor = FollowUpExtractor(
        industries_candidate(),
        industries_candidate("Artificial Intelligence"),
    )

    result, output = run_follow_up(
        path,
        extractor,
        [
            "I prefer artificial intelligence companies.",
            ".done",
            "Artificial intelligence.",
            ".done",
            "y",
            "y",
        ],
    )

    assert result is True
    assert load_profile(path).career_preferences.preferred_industries == [
        "Artificial Intelligence"
    ]
    joined = "\n".join(output)
    assert "could not extract an answer" in joined
    assert "Confirm that you have nothing" not in joined


def test_preferred_industries_correction_is_path_scoped(tmp_path) -> None:
    original = partial_preferences_profile()
    path = save_profile(original, tmp_path / "profile.json")
    extractor = FollowUpExtractor(
        industries_candidate("Artificial Intelligence"),
        {
            "career_preferences": {
                "interested_fields": ["Do not overwrite"],
                "preferred_industries": ["Developer Tools"],
            }
        },
    )

    result, _ = run_follow_up(
        path,
        extractor,
        [
            "Artificial Intelligence",
            ".done",
            "e",
            "Use Developer Tools instead.",
            ".done",
            "y",
            "y",
            "y",
        ],
    )

    saved = load_profile(path)
    assert result is True
    assert saved.career_preferences.preferred_industries == ["Developer Tools"]
    assert saved.career_preferences.interested_fields == (
        original.career_preferences.interested_fields
    )
    assert extractor.correction_calls[0][1]["field_path"] == (
        "career_preferences.preferred_industries"
    )


def test_preferred_industries_quit_keeps_profile_and_state_unchanged(tmp_path) -> None:
    original = partial_preferences_profile()
    path = save_profile(original, tmp_path / "profile.json")

    result, _ = run_follow_up(
        path,
        FollowUpExtractor(industries_candidate("Artificial Intelligence")),
        ["Artificial Intelligence", ".done", "q"],
    )

    assert result is False
    assert load_profile(path) == original
    assert not state_path_for(path).exists()


def test_saved_preferred_industries_is_not_asked_after_reload(tmp_path) -> None:
    path = save_profile(partial_preferences_profile(), tmp_path / "profile.json")
    first = FollowUpExtractor(industries_candidate("Artificial Intelligence"))
    result, _ = run_follow_up(
        path, first, ["Artificial Intelligence", ".done", "y", "y"]
    )
    assert result is True

    second = FollowUpExtractor()
    result, output = run_follow_up(path, second, [])

    assert result is True
    assert second.calls == []
    assert not any("What industries" in line for line in output)
    assert output == [
        "Career Profile is already complete.",
        "No changes were made.",
    ]


@pytest.mark.parametrize(
    ("discovery_path", "candidate", "expected"),
    [
        (
            "basic_profile.name",
            {"basic_profile": {"name": "Sample User", "current_location": "Example City"}},
            {"basic_profile": {"name": "Sample User"}},
        ),
        (
            "basic_profile.current_location",
            {"basic_profile": {"name": "Sample User", "current_location": "Example City"}},
            {"basic_profile": {"current_location": "Example City"}},
        ),
        (
            "constraints.target_locations",
            {"constraints": {"target_locations": ["United States", "China"], "target_start_date": "2027-06"}},
            {"constraints": {"target_locations": ["United States", "China"]}},
        ),
        (
            "constraints.target_start_date",
            {"constraints": {"target_locations": ["China"], "target_start_date": "2027-06"}},
            {"constraints": {"target_start_date": "2027-06"}},
        ),
        (
            "skill_proficiency",
            {"skills": [{"skill_name": "Python", "category": "language", "self_reported_proficiency": "advanced"}]},
            {"skills": [{"skill_name": "Python", "category": "language", "self_reported_proficiency": "advanced"}]},
        ),
    ],
)
def test_field_projection_covers_other_path_level_questions(
    discovery_path, candidate, expected
) -> None:
    assert project_candidate_to_path(candidate, discovery_path) == expected


def test_industry_debug_explains_projection_and_empty_decision(tmp_path) -> None:
    path = save_profile(partial_preferences_profile(), tmp_path / "profile.json")
    extractor = FollowUpExtractor(industries_candidate())
    extractor.diagnostics = SimpleNamespace(
        raw_output='{"career_preferences":{"preferred_industries":[]}}'
    )

    result, output = run_follow_up(
        path,
        extractor,
        ["Artificial intelligence companies", ".done", "q"],
        debug=True,
    )

    assert result is False
    joined = "\n".join(output)
    for label in (
        "selected field path",
        "raw provider output",
        "normalized topic Candidate",
        "allowed field paths",
        "field projection result",
        "empty-result decision reason",
    ):
        assert label in joined
    assert "career_preferences.preferred_industries" in joined


def test_seven_experiences_enriched_with_three_date_ranges_stay_seven(tmp_path) -> None:
    path = save_profile(seven_experience_profile(), tmp_path / "profile.json")

    result, _ = run_follow_up(
        path,
        FollowUpExtractor(three_experience_date_enrichments()),
        ["Here are the dates for three existing experiences.", ".done", "y", "y"],
    )

    assert result is True
    saved = load_profile(path)
    assert len(saved.experience_overview) == 7
    dates = {
        item.organization_or_project_name: (item.start_date, item.end_date)
        for item in saved.experience_overview
    }
    assert dates["Northstar Labs"] == ("2025-05", "2025-08")
    assert dates["Horizon Internship Program"] == ("2025-05", "2026-01")
    assert dates["Regional Public Health Lab"] == ("2024-06", "2024-08")
    assert len(load_profile(path).experience_overview) == 7
    state = load_discovery_state(state_path_for(path))
    assert state.fields["experience_details"] == FollowUpStatus.ANSWERED


def test_unmatched_follow_up_record_cannot_silently_append_and_cancel_is_atomic(
    tmp_path,
) -> None:
    original = seven_experience_profile()
    path = save_profile(original, tmp_path / "profile.json")
    unmatched = {
        "experience_overview": [
            experience_record(
                "New Company", "Engineer", "work", "Built a system.",
                "2026-01", "2026-06",
            )
        ]
    }

    result, output = run_follow_up(
        path,
        FollowUpExtractor(unmatched),
        ["A new record appeared.", ".done", "y", "q"],
    )

    assert result is False
    assert load_profile(path) == original
    assert not state_path_for(path).exists()
    assert any("will not be added automatically" in line for line in output)


def test_unmatched_follow_up_record_is_added_only_after_explicit_choice(tmp_path) -> None:
    path = save_profile(seven_experience_profile(), tmp_path / "profile.json")
    unmatched = {
        "experience_overview": [
            experience_record(
                "New Company", "Engineer", "work", "Built a system.",
                "2026-01", "2026-06",
            )
        ]
    }

    result, _ = run_follow_up(
        path,
        FollowUpExtractor(unmatched),
        ["This is a new experience.", ".done", "y", "a", "y"],
    )

    assert result is True
    saved = load_profile(path)
    assert len(saved.experience_overview) == 8
    assert saved.experience_overview[-1].organization_or_project_name == "New Company"


def test_experience_none_means_no_additional_details_not_no_dates(tmp_path) -> None:
    profile = seven_experience_profile()
    path = save_profile(profile, tmp_path / "profile.json")

    result, output = run_follow_up(path, FollowUpExtractor(), [":none", "y"])

    assert result is True
    assert load_profile(path) == profile
    joined = "\n".join(output)
    assert "No additional information provided:\n- Remaining experience details" in joined
    assert "Confirmed as not provided:\n- Experience dates" not in joined


def test_preferred_industries_confirmation_hides_sibling_fields(tmp_path) -> None:
    path = save_profile(partial_preferences_profile(), tmp_path / "profile.json")

    result, output = run_follow_up(
        path,
        FollowUpExtractor(industries_candidate("Artificial Intelligence")),
        ["Artificial Intelligence", ".done", "q"],
    )

    assert result is False
    joined = "\n".join(output)
    assert "Preferred industries:\n- Artificial Intelligence" in joined
    assert "Fields of interest: Not provided" not in joined
    assert "Preferred work activities: Not provided" not in joined
    assert "Roles under consideration: Not provided" not in joined


def test_multiline_follow_up_uses_clear_continuation_prompt(tmp_path) -> None:
    path = save_profile(existing_profile(), tmp_path / "profile.json")
    prompts = []
    answers = iter(["Example City", "Graduate student", ".done", "q"])

    def input_fn(prompt):
        prompts.append(prompt)
        return next(answers)

    result = run_follow_up_discovery(
        path,
        FollowUpExtractor(basic_candidate()),
        input_fn=input_fn,
        output_fn=lambda _message: None,
    )

    assert result is False
    assert prompts[:3] == [
        "> ",
        "Add another line, or type .done to submit: ",
        "Add another line, or type .done to submit: ",
    ]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("q", "q"),
        (":skip", ":skip"),
        (":decline", ":decline"),
        (":finish", END_SESSION),
    ],
)
def test_follow_up_commands_discard_buffered_unsubmitted_text(
    tmp_path, command, expected
) -> None:
    path = save_profile(existing_profile(), tmp_path / "profile.json")
    workflow = AdaptiveFollowUpWorkflow(
        path,
        FollowUpExtractor(),
        input_fn=scripted_input(["This text is not submitted", command]),
        output_fn=lambda _message: None,
    )

    result = workflow._read_follow_up_answer()

    if expected is END_SESSION:
        assert result is END_SESSION
    else:
        assert result == expected


def test_cancel_discards_current_answer_and_reasks_same_question(tmp_path) -> None:
    path = save_profile(existing_profile(), tmp_path / "profile.json")
    extractor = FollowUpExtractor(basic_candidate())

    result, output = run_follow_up(
        path,
        extractor,
        ["Unsubmitted draft", ".cancel", "Example City", ".done", "q"],
    )

    assert result is False
    assert len(extractor.calls) == 1
    assert extractor.calls[0][0] == "Example City"
    assert sum(
        FOLLOW_UP_QUESTIONS["basic_profile.current_location"] in line
        for line in output
    ) == 2
    assert "Current answer cancelled. Let's try this question again." in output
    assert load_profile(path) == existing_profile()


def test_provider_progress_message_is_shown_before_follow_up_call(tmp_path) -> None:
    path = save_profile(existing_profile(), tmp_path / "profile.json")
    output = []

    class ProgressCheckingExtractor(FollowUpExtractor):
        def extract_follow_up(self, answer, **context):
            assert output[-1] == "Extracting information..."
            return super().extract_follow_up(answer, **context)

    result = run_follow_up_discovery(
        path,
        ProgressCheckingExtractor(basic_candidate()),
        input_fn=scripted_input(["Example City", ".done", "q"]),
        output_fn=output.append,
    )

    assert result is False
    assert "Extracting information..." in output


def test_confirmation_menu_ignores_pasted_blank_lines(tmp_path) -> None:
    path = save_profile(existing_profile(), tmp_path / "profile.json")

    result, output = run_follow_up(
        path,
        FollowUpExtractor(basic_candidate()),
        ["Example City", ".done", "", "   ", "q"],
    )

    assert result is False
    assert not any("Invalid input" in line for line in output)
    assert load_profile(path) == existing_profile()


def test_complete_profile_and_state_exit_as_noop_without_touching_files(tmp_path) -> None:
    profile = complete_follow_up_profile()
    path = tmp_path / "profile.json"
    state_path = state_path_for(path)
    save_profile_and_state(profile, path, complete_discovery_state(), state_path)
    profile_before = path.read_bytes()
    state_before = state_path.read_bytes()
    profile_mtime = path.stat().st_mtime_ns
    state_mtime = state_path.stat().st_mtime_ns
    output = []

    class NeverCalledExtractor:
        def extract_follow_up(self, *_args, **_kwargs):
            pytest.fail("a no-op follow-up must not call the Provider")

    result = run_follow_up_discovery(
        path,
        NeverCalledExtractor(),
        input_fn=lambda _prompt: pytest.fail("a no-op follow-up must not ask for input"),
        output_fn=output.append,
    )

    assert result is True
    assert output == [
        "Career Profile is already complete.",
        "No changes were made.",
    ]
    assert path.read_bytes() == profile_before
    assert state_path.read_bytes() == state_before
    assert path.stat().st_mtime_ns == profile_mtime
    assert state_path.stat().st_mtime_ns == state_mtime


def test_state_only_command_still_reaches_save_confirmation(tmp_path) -> None:
    path = save_profile(existing_profile(), tmp_path / "profile.json")
    answers = iter([":none", ":finish", "n"])
    prompts = []

    def input_fn(prompt):
        prompts.append(prompt)
        return next(answers)

    result = run_follow_up_discovery(
        path,
        FollowUpExtractor(),
        input_fn=input_fn,
        output_fn=lambda _message: None,
    )

    assert result is False
    assert "Save all confirmed follow-up changes? [y/n] " in prompts


def test_v1_state_migration_is_not_treated_as_noop(tmp_path) -> None:
    profile = complete_follow_up_profile()
    path = save_profile(profile, tmp_path / "profile.json")
    state_path = state_path_for(path)
    state_path.write_text(
        json.dumps({
            "version": 1,
            "topics": {
                "basic_profile": "answered",
                "career_preferences": "answered",
                "constraints": "answered",
                "education_details": "answered",
                "experience_details": "answered",
                "skill_proficiency": "answered",
            },
        }),
        encoding="utf-8",
    )
    answers = iter(["n"])
    prompts = []
    output = []

    def input_fn(prompt):
        prompts.append(prompt)
        return next(answers)

    result = run_follow_up_discovery(
        path,
        FollowUpExtractor(),
        input_fn=input_fn,
        output_fn=output.append,
    )

    assert result is False
    assert "Save all confirmed follow-up changes? [y/n] " in prompts
    assert "Career Profile is already complete." not in output
