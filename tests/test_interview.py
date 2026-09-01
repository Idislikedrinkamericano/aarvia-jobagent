import json

from aarvia import create_profile, load_profile, save_profile
from aarvia.interview import parse_comma_list, run_discovery


def scripted_input(answers):
    iterator = iter(answers)

    def read(_prompt: str) -> str:
        try:
            return next(iterator)
        except StopIteration as error:
            raise AssertionError("interview requested more input than expected") from error

    return read


def run_with_answers(path, answers):
    output = []
    complete = run_discovery(path, input_fn=scripted_input(answers), output_fn=output.append)
    return complete, output


def complete_non_record_data() -> dict:
    return {
        "basic_profile": {"current_location": "Example City", "current_status": "Student"},
        "career_preferences": {
            "interested_fields": ["AI"],
            "preferred_work_activities": ["Building systems"],
            "preferred_industries": ["Software"],
            "fields_or_activities_to_avoid": [],
            "currently_considered_roles": ["Software engineer"],
        },
        "constraints": {
            "target_locations": ["Example City"],
            "work_authorization_or_visa_constraints": "None",
            "work_arrangement_preference": "hybrid",
            "employment_type_preference": "full-time",
            "target_start_date": "2026-09",
            "other_constraints": [],
        },
    }


def test_first_run_creates_profile(tmp_path) -> None:
    path = tmp_path / "profiles" / "profile.json"

    complete, output = run_with_answers(path, [":quit"])

    assert complete is False
    assert path.exists()
    assert load_profile(path) == create_profile()
    assert "Progress saved." in output


def test_answers_are_saved_to_correct_fields(tmp_path) -> None:
    path = tmp_path / "profile.json"

    run_with_answers(path, ["  Example City  ", "Student", ":quit"])

    profile = load_profile(path)
    assert profile.basic_profile.current_location == "Example City"
    assert profile.basic_profile.current_status == "Student"


def test_comma_lists_drop_blanks_and_duplicates_in_order() -> None:
    assert parse_comma_list(" AI Agents, NLP, , AI Agents, Machine Learning ") == [
        "AI Agents",
        "NLP",
        "Machine Learning",
    ]


def test_skip_is_not_saved_as_profile_data(tmp_path) -> None:
    path = tmp_path / "profile.json"

    run_with_answers(path, [":skip", ":quit"])

    raw = path.read_text(encoding="utf-8")
    assert ":skip" not in raw
    assert load_profile(path).basic_profile.current_location is None


def test_quit_preserves_partial_record_draft_and_resume_finishes_it(tmp_path) -> None:
    path = tmp_path / "profile.json"
    run_with_answers(path, ["Example City", "Student", "Example University", ":quit"])

    draft_path = tmp_path / "profile.interview.json"
    assert json.loads(draft_path.read_text(encoding="utf-8"))["education"]["institution"] == "Example University"

    run_with_answers(path, ["MSc", "Computer Science", "2025-09", "2027-06", "n", ":quit"])

    profile = load_profile(path)
    assert profile.education[0].institution == "Example University"
    assert not draft_path.exists()


def test_existing_fields_are_loaded_and_not_overwritten(tmp_path) -> None:
    path = tmp_path / "profile.json"
    save_profile(create_profile({"basic_profile": {"current_location": "Beijing"}}), path)

    run_with_answers(path, ["Employed", ":quit"])

    profile = load_profile(path)
    assert profile.basic_profile.current_location == "Beijing"
    assert profile.basic_profile.current_status == "Employed"


def test_invalid_date_reports_error_and_reprompts(tmp_path) -> None:
    path = tmp_path / "profile.json"
    save_profile(create_profile({"basic_profile": {"current_location": "Example City", "current_status": "Student"}}), path)

    _, output = run_with_answers(
        path,
        ["Example University", "MSc", "Computer Science", "September 2025", "2025-09", "2027-06", "n", ":quit"],
    )

    assert load_profile(path).education[0].start_date == "2025-09"
    assert any("Invalid input:" in message and "YYYY-MM" in message for message in output)


def test_multiple_education_experience_and_skills_are_saved(tmp_path) -> None:
    path = tmp_path / "profile.json"
    save_profile(create_profile(complete_non_record_data()), path)
    answers = [
        "University One", "BSc", "Computer Science", "2020-09", "2024-06", "y",
        "University Two", "MSc", "Artificial Intelligence", "2024-09", "2026-06", "n",
        "internship", "Example Org", "Intern", "Built an internal tool.", "2023-06", "2023-08", "y",
        "project", "Example Project", "Lead", "Built a search prototype.", "2024-01", ":skip", "n",
        "Python", "programming language", "advanced", "y",
        "SQL", "database", ":skip", "n",
    ]

    complete, output = run_with_answers(path, answers)

    profile = load_profile(path)
    assert complete is True
    assert len(profile.education) == 2
    assert len(profile.experience_overview) == 2
    assert profile.experience_overview[1].end_date is None
    assert len(profile.skills) == 2
    assert profile.skills[1].self_reported_proficiency is None
    assert "Career profile complete." in output


def test_completed_profile_requires_no_input(tmp_path) -> None:
    path = tmp_path / "profile.json"
    data = complete_non_record_data()
    data.update(
        {
            "education": [{
                "institution": "University", "degree": "BSc", "field_of_study": "CS",
                "start_date": "2020-09", "expected_graduation_date": "2024-06",
            }],
            "experience_overview": [{
                "experience_type": "project", "organization_or_project_name": "Project",
                "title_or_role": "Developer", "short_factual_summary": "Built a tool.",
                "start_date": "2024-01", "end_date": None,
            }],
            "skills": [{"skill_name": "Python", "category": "programming language"}],
        }
    )
    save_profile(create_profile(data), path)

    complete, output = run_with_answers(path, [])

    assert complete is True
    assert output[-2:] == ["Career profile complete.", f"Saved to: {path}"]
