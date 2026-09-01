from aarvia import create_profile
from aarvia.presentation import (
    format_date,
    format_experience,
    format_follow_up_candidate,
    format_session_summary,
    format_user_friendly_diff,
)


def experience(start="2025-05", end="2025-08"):
    return {
        "experience_type": "internship",
        "organization_or_project_name": "MiraclePlus",
        "title_or_role": "AI Analyst and Investment Mentor",
        "short_factual_summary": "Evaluated AI solutions.",
        "start_date": start,
        "end_date": end,
    }


def education():
    return {
        "institution": "UIUC",
        "degree": "Bachelor's Degree",
        "field_of_study": "Economics",
        "start_date": None,
        "expected_graduation_date": None,
        "gpa": None,
    }


def skill():
    return {
        "skill_name": "Python",
        "category": "Programming language",
        "self_reported_proficiency": None,
    }


def test_experience_summary_shows_readable_start_and_end_dates() -> None:
    lines = format_experience([experience()])

    assert lines == [
        "AI Analyst and Investment Mentor at MiraclePlus",
        "Type: Internship",
        "Dates: May 2025 – August 2025",
        "Summary: Evaluated AI solutions.",
    ]


def test_experience_ongoing_and_unknown_dates() -> None:
    assert "Dates: May 2025 – Present" in format_experience([experience(end=None)])
    assert "Dates: Not provided" in format_experience([experience(start=None, end=None)])


def test_month_date_is_human_readable() -> None:
    assert format_date("2026-01") == "January 2026"


def test_normal_diff_is_friendly_and_debug_can_include_technical_path() -> None:
    before = [experience(end=None)]
    after = [experience(end="2026-01")]
    changes = [("experience_overview[0].end_date", None, "2026-01")]

    normal = format_user_friendly_diff(
        changes,
        topic="experience_overview",
        before=before,
        after=after,
    )
    debug = format_user_friendly_diff(
        changes,
        topic="experience_overview",
        before=before,
        after=after,
        debug_paths=True,
    )

    joined = "\n".join(normal)
    assert "MiraclePlus — AI Analyst and Investment Mentor" in joined
    assert "Before: Not provided" in joined
    assert "After: January 2026" in joined
    assert "[0]" not in joined
    assert "null" not in joined
    assert "experience_overview" not in joined
    assert "end_date" not in joined
    assert "Technical path: experience_overview[0].end_date" in "\n".join(debug)


def test_record_diffs_use_education_experience_and_skill_identities() -> None:
    cases = [
        (
            "education",
            [education()],
            [education() | {"gpa": "3.7"}],
            [("education[0].gpa", None, "3.7")],
            "UIUC — Bachelor's Degree in Economics",
        ),
        (
            "experience_overview",
            [experience(end=None)],
            [experience(end="2026-01")],
            [("experience_overview[0].end_date", None, "2026-01")],
            "MiraclePlus — AI Analyst and Investment Mentor",
        ),
        (
            "skills",
            [skill()],
            [skill() | {"self_reported_proficiency": "Advanced"}],
            [("skills[0].self_reported_proficiency", None, "Advanced")],
            "Python",
        ),
    ]
    for topic, before, after, changes, identity in cases:
        output = format_user_friendly_diff(
            changes, topic=topic, before=before, after=after
        )
        assert identity in output


def test_list_diff_is_grouped_and_comma_separated() -> None:
    before = {"target_locations": []}
    after = {"target_locations": ["United States", "China"]}
    changes = [
        ("constraints.target_locations[0]", None, "United States"),
        ("constraints.target_locations[1]", None, "China"),
    ]

    output = format_user_friendly_diff(
        changes, topic="constraints", before=before, after=after
    )

    assert output.count("Target locations:") == 1
    assert "- After: United States, China" in output
    assert not any("[0]" in line or "[1]" in line for line in output)


def test_session_summary_uses_question_sets_not_array_indexes() -> None:
    original = create_profile()
    updated = create_profile(
        {
            "basic_profile": {
                "current_location": "Shanghai",
                "current_status": "Graduate student",
            }
        }
    )

    output = format_session_summary(
        original,
        updated,
        confirmed_topics=["basic_profile.current_location", "basic_profile.current_status"],
        original_state_fields={
            "skill_proficiency": "unanswered",
            "constraints.target_locations": "unanswered",
        },
        state_fields={
            "skill_proficiency": "skipped",
            "constraints.target_locations": "declined",
        },
        still_missing_paths=["career_preferences.preferred_industries"],
    )
    joined = "\n".join(output)

    assert "Updated:\n- Current location\n- Current status" in joined
    assert "Resolved:\n- Current location\n- Current status" in joined
    assert "Still missing:" in joined
    assert "Skipped:\n- Skill proficiency" in joined
    assert "Declined:\n- Target locations" in joined
    assert "open_questions" not in joined
    assert "open questions" not in joined
    assert "[0]" not in joined


def test_field_level_preferences_render_only_the_selected_field() -> None:
    value = {
        "interested_fields": [],
        "preferred_work_activities": [],
        "preferred_industries": [
            "Artificial Intelligence",
            "Software Technology",
            "Developer Tools",
            "Enterprise Software",
        ],
        "fields_or_activities_to_avoid": [],
        "currently_considered_roles": [],
    }

    output = format_follow_up_candidate(
        "career_preferences.preferred_industries",
        "career_preferences",
        value,
    )

    assert output == [
        "Preferred industries:",
        "- Artificial Intelligence",
        "- Software Technology",
        "- Developer Tools",
        "- Enterprise Software",
    ]
    assert "Not provided" not in "\n".join(output)


def test_experience_none_summary_uses_no_additional_information_language() -> None:
    profile = create_profile()
    output = format_session_summary(
        profile,
        profile,
        confirmed_topics=[],
        original_state_fields={"experience_details": "unanswered"},
        state_fields={"experience_details": "confirmed_none"},
        still_missing_paths=[],
    )
    joined = "\n".join(output)

    assert "No additional information provided:\n- Remaining experience details" in joined
    assert "Confirmed as not provided:\n- Experience dates" not in joined
