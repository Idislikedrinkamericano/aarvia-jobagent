from aarvia import create_profile, generate_open_questions

from test_profile import complete_profile_data


def test_missing_information_generates_deterministic_questions() -> None:
    profile = create_profile()

    assert profile.open_questions == generate_open_questions(profile)
    assert "What is your education background?" in profile.open_questions
    assert "What skills do you currently have?" in profile.open_questions
    assert "Which career fields interest you?" in profile.open_questions
    assert "Which locations are you targeting?" in profile.open_questions


def test_completed_information_removes_corresponding_questions() -> None:
    incomplete = create_profile()
    complete = create_profile(complete_profile_data())

    assert "What is your education background?" in incomplete.open_questions
    assert "What skills do you currently have?" in incomplete.open_questions
    assert complete.open_questions == []


def test_partially_completed_sections_only_keep_unanswered_questions() -> None:
    data = complete_profile_data()
    data["career_preferences"]["currently_considered_roles"] = []
    data["constraints"]["target_start_date"] = None

    questions = create_profile(data).open_questions

    assert questions == [
        "Are there any roles you are currently considering?",
        "When would you like to start your next role?",
    ]
