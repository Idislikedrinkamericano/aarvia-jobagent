import pytest

from aarvia import CareerProfile, ProfileValidationError, create_profile


def complete_profile_data() -> dict:
    return {
        "basic_profile": {
            "name": "Lin",
            "current_location": "Shanghai",
            "current_status": "Graduate student",
        },
        "education": [
            {
                "institution": "Example University",
                "degree": "MSc",
                "field_of_study": "Computer Science",
                "start_date": "2025-09",
                "expected_graduation_date": "2027-06",
            }
        ],
        "experience_overview": [
            {
                "experience_type": "project",
                "organization_or_project_name": "Search Project",
                "title_or_role": "Developer",
                "short_factual_summary": "Built a document search prototype.",
                "start_date": "2026-01",
                "end_date": None,
            }
        ],
        "skills": [
            {
                "skill_name": "Python",
                "category": "programming language",
                "self_reported_proficiency": "intermediate",
            }
        ],
        "career_preferences": {
            "interested_fields": ["developer tools"],
            "preferred_work_activities": ["building prototypes"],
            "preferred_industries": ["software"],
            "fields_or_activities_to_avoid": ["high-volume sales"],
            "currently_considered_roles": ["software engineer"],
        },
        "constraints": {
            "target_locations": ["Shanghai", "Remote"],
            "work_authorization_or_visa_constraints": "Requires sponsorship outside China",
            "work_arrangement_preference": "hybrid",
            "employment_type_preference": "full-time",
            "target_start_date": "2027-07",
            "other_constraints": ["No frequent travel"],
        },
    }


def test_create_valid_profile() -> None:
    profile = create_profile(complete_profile_data())

    assert isinstance(profile, CareerProfile)
    assert profile.basic_profile.name == "Lin"
    assert profile.education[0].field_of_study == "Computer Science"
    assert profile.skills[0].skill_name == "Python"
    assert profile.open_questions == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda data: data["skills"][0].update(skill_name=3), "skill_name"),
        (lambda data: data["education"][0].update(start_date="September 2025"), "start_date"),
        (lambda data: data["constraints"].update(work_arrangement_preference="sometimes"), "work_arrangement"),
    ],
)
def test_invalid_fields_are_rejected(change, message: str) -> None:
    data = complete_profile_data()
    change(data)

    with pytest.raises(ProfileValidationError, match=message):
        create_profile(data)


def test_unknown_fields_are_not_silently_accepted() -> None:
    data = complete_profile_data()
    data["career_preferences"]["recommended_role"] = "data scientist"

    with pytest.raises(ProfileValidationError, match="unknown fields: recommended_role"):
        create_profile(data)


def test_inconsistent_supplied_open_questions_are_rejected() -> None:
    data = complete_profile_data()
    data["open_questions"] = ["Invent a new question"]

    with pytest.raises(ProfileValidationError, match="does not match"):
        create_profile(data)
