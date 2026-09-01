"""User-facing formatting with no mutation of Profile or Candidate data."""

from __future__ import annotations

from calendar import month_name
import re
from typing import Any, Iterable

from .profile import CareerProfile


FIELD_LABELS = {
    "name": "Name",
    "current_location": "Current location",
    "current_status": "Current situation",
    "interested_fields": "Fields of interest",
    "preferred_work_activities": "Preferred work activities",
    "preferred_industries": "Preferred industries",
    "fields_or_activities_to_avoid": "Work to avoid",
    "currently_considered_roles": "Roles under consideration",
    "target_locations": "Target locations",
    "work_authorization_or_visa_constraints": "Work authorization or sponsorship",
    "work_arrangement_preference": "Work arrangement",
    "employment_type_preference": "Employment type",
    "target_start_date": "Target start date",
    "other_constraints": "Other requirements",
    "institution": "School",
    "degree": "Degree",
    "field_of_study": "Field of study",
    "start_date": "Start date",
    "expected_graduation_date": "End date",
    "gpa": "GPA",
    "experience_type": "Type",
    "organization_or_project_name": "Organization or project",
    "title_or_role": "Role",
    "short_factual_summary": "Summary",
    "end_date": "End date",
    "skill_name": "Skill",
    "category": "Category",
    "self_reported_proficiency": "Proficiency",
}

TOPIC_LABELS = {
    "basic_profile.name": "Name",
    "basic_profile.current_location": "Current location",
    "basic_profile.current_status": "Current status",
    "career_preferences.interested_fields": "Career interests",
    "career_preferences.preferred_work_activities": "Preferred work activities",
    "career_preferences.preferred_industries": "Preferred industries",
    "career_preferences.currently_considered_roles": "Roles under consideration",
    "constraints.target_locations": "Target locations",
    "constraints.work_authorization_or_visa_constraints": "Work authorization",
    "constraints.work_arrangement_preference": "Work arrangement",
    "constraints.employment_type_preference": "Employment type",
    "constraints.target_start_date": "Target start date",
    "basic_profile": "Basic profile",
    "career_preferences": "Career preferences",
    "constraints": "Job constraints",
    "education": "Education",
    "education_details": "Education dates",
    "experience_overview": "Experience",
    "experience_details": "Experience dates",
    "skills": "Skills",
    "skill_proficiency": "Skill proficiency",
}


def format_optional_value(value: Any, *, field: str | None = None) -> str:
    if value is None or value == "" or value == []:
        return "Not provided"
    if field and "date" in field and isinstance(value, str):
        return format_date(value)
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "Not provided"
    return str(value)


def format_date(value: str | None, *, ongoing: bool = False) -> str:
    if value is None:
        return "Present" if ongoing else "Not provided"
    match = re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", value)
    if not match:
        return value
    year, month, day = match.groups()
    label = f"{month_name[int(month)]} {year}"
    return f"{month_name[int(month)]} {int(day)}, {year}" if day else label


def format_basic_profile(value: dict[str, Any]) -> list[str]:
    return [
        f"Name: {format_optional_value(value.get('name'))}",
        f"Current location: {format_optional_value(value.get('current_location'))}",
        f"Current situation: {format_optional_value(value.get('current_status'))}",
    ]


def format_education(value: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(value, start=1):
        lines.append(f"{index}. {education_identity(item)}")
        lines.append(
            "Dates: "
            f"{_format_date_range(item.get('start_date'), item.get('expected_graduation_date'))}"
        )
        lines.append(f"GPA: {format_optional_value(item.get('gpa'))}")
    return lines


def format_experience(value: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in value:
        lines.append(f"{item['title_or_role']} at {item['organization_or_project_name']}")
        lines.append(f"Type: {item['experience_type'].replace('-', ' ').title()}")
        lines.append(f"Dates: {_format_date_range(item.get('start_date'), item.get('end_date'), ongoing=True)}")
        lines.append(f"Summary: {format_optional_value(item.get('short_factual_summary'))}")
    return lines


def format_skills(value: list[dict[str, Any]]) -> list[str]:
    return [
        f"{item['skill_name']} — {item['category']}; "
        f"proficiency: {format_optional_value(item.get('self_reported_proficiency'))}"
        for item in value
    ]


def format_career_preferences(value: dict[str, Any]) -> list[str]:
    return [
        f"{FIELD_LABELS[field]}: {format_optional_value(value.get(field))}"
        for field in (
            "interested_fields",
            "preferred_work_activities",
            "preferred_industries",
            "fields_or_activities_to_avoid",
            "currently_considered_roles",
        )
    ]


def format_constraints(value: dict[str, Any]) -> list[str]:
    return [
        f"{FIELD_LABELS[field]}: {format_optional_value(value.get(field), field=field)}"
        for field in (
            "target_locations",
            "work_authorization_or_visa_constraints",
            "work_arrangement_preference",
            "employment_type_preference",
            "target_start_date",
            "other_constraints",
        )
    ]


def format_topic(topic: str, value: Any) -> list[str]:
    formatter = {
        "basic_profile": format_basic_profile,
        "education": format_education,
        "experience_overview": format_experience,
        "skills": format_skills,
        "career_preferences": format_career_preferences,
        "constraints": format_constraints,
    }.get(topic)
    if formatter is None:
        return [format_optional_value(value)]
    return formatter(value)


def format_follow_up_candidate(discovery_path: str, topic: str, value: Any) -> list[str]:
    """Render only the field currently being confirmed, or the active detail group."""
    if "." not in discovery_path:
        return format_topic(topic, value)
    _section, field = discovery_path.split(".", 1)
    field_value = value.get(field) if isinstance(value, dict) else None
    label = FIELD_LABELS.get(field, field.replace("_", " ").title())
    if isinstance(field_value, list):
        return [f"{label}:"] + _bullets(str(item) for item in field_value)
    return [f"{label}: {format_optional_value(field_value, field=field)}"]


def format_unmatched_record(topic: str, record: dict[str, Any]) -> list[str]:
    return [
        "Aarvia could not match this information to an existing record:",
        record_identity(topic, record),
        "This follow-up is for enriching existing records, so it will not be added automatically.",
    ]


def format_ambiguous_record_matches(
    topic: str,
    candidate: dict[str, Any],
    matches: list[dict[str, Any]],
) -> list[str]:
    lines = [
        "More than one existing record matches:",
        record_identity(topic, candidate),
        "Choose the record Aarvia should update:",
    ]
    for index, record in enumerate(matches, start=1):
        lines.append(f"{index}. {record_identity(topic, record)}")
        if topic == "experience_overview":
            lines.append(
                "   Dates: "
                + _format_date_range(
                    record.get("start_date"), record.get("end_date"), ongoing=True
                )
            )
    return lines


def format_user_friendly_diff(
    changes: list[tuple[str, Any, Any]],
    *,
    topic: str,
    before: Any,
    after: Any,
    debug_paths: bool = False,
) -> list[str]:
    lines = ["Proposed changes:"]
    last_identity: str | None = None
    rendered_lists: set[str] = set()
    for path, old, new in changes:
        field = path.rsplit(".", 1)[-1]
        list_item_match = re.search(r"\[(\d+)\]$", field)
        if list_item_match:
            list_path = re.sub(r"\[\d+\]$", "", path)
            if list_path in rendered_lists:
                continue
            rendered_lists.add(list_path)
            field = re.sub(r"\[\d+\]$", "", field)
            if isinstance(before, dict) and isinstance(after, dict):
                old, new = before.get(field, []), after.get(field, [])
        record_match = re.search(r"\[(\d+)\]", path)
        identity = None
        if record_match and topic in {"education", "experience_overview", "skills"}:
            index = int(record_match.group(1))
            records = after if index < len(after) else before
            if index < len(records):
                identity = record_identity(topic, records[index])
        if identity and identity != last_identity:
            lines.extend(["", identity])
            last_identity = identity
        lines.append(f"{FIELD_LABELS.get(field, field.replace('_', ' ').title())}:")
        lines.append(f"- Before: {format_optional_value(old, field=field)}")
        lines.append(f"- After: {format_optional_value(new, field=field)}")
        if debug_paths:
            lines.append(f"  Technical path: {path}")
    return lines


def format_conflict(
    topic: str,
    record: dict[str, Any] | None,
    field: str,
    current: Any,
    new: Any,
) -> list[str]:
    label = FIELD_LABELS.get(field, field.replace("_", " ").title())
    value_label = label.lower()
    if value_label.startswith("current "):
        value_label = value_label.removeprefix("current ")
    lines = [f"{label} conflict for:"]
    if record is not None:
        lines.append(record_identity(topic, record))
    else:
        lines.append(TOPIC_LABELS.get(topic, topic.replace("_", " ").title()))
    lines.extend(
        [
            "",
            f"Current {value_label}: {format_optional_value(current, field=field)}",
            f"New {value_label}: {format_optional_value(new, field=field)}",
            "",
            "Which should Aarvia keep?",
        ]
    )
    return lines


def format_session_summary(
    original: CareerProfile,
    updated: CareerProfile,
    *,
    confirmed_topics: Iterable[str],
    original_state_fields: dict[str, Any],
    state_fields: dict[str, Any],
    still_missing_paths: Iterable[str],
) -> list[str]:
    new_questions = set(updated.open_questions)
    resolved = [
        question_label(question)
        for question in original.open_questions
        if question not in new_questions
    ]
    still_missing = [TOPIC_LABELS.get(path, question_label(path)) for path in still_missing_paths]
    detail_none_paths = {"education_details", "experience_details"}
    confirmed_none = [
        TOPIC_LABELS.get(path, path.replace("_", " ").title())
        for path, status in state_fields.items()
        if _status_value(status) == "confirmed_none"
        and _status_value(original_state_fields.get(path)) != "confirmed_none"
        and path not in detail_none_paths
    ]
    no_additional = [
        {
            "education_details": "Remaining education details",
            "experience_details": "Remaining experience details",
        }[path]
        for path, status in state_fields.items()
        if path in detail_none_paths
        and _status_value(status) == "confirmed_none"
        and _status_value(original_state_fields.get(path)) != "confirmed_none"
    ]
    skipped = [
        TOPIC_LABELS.get(path, path.replace("_", " ").title())
        for path, status in state_fields.items()
        if _status_value(status) == "skipped"
        and _status_value(original_state_fields.get(path)) != "skipped"
    ]
    declined = [
        TOPIC_LABELS.get(path, path.replace("_", " ").title())
        for path, status in state_fields.items()
        if _status_value(status) == "declined"
        and _status_value(original_state_fields.get(path)) != "declined"
    ]
    updated_topics = _deduplicate(
        TOPIC_LABELS.get(topic, topic.replace("_", " ").title())
        for topic in confirmed_topics
    )
    lines = ["Session summary", "", "Updated:"]
    lines.extend(_bullets(updated_topics))
    lines.extend(["", "Resolved:"])
    lines.extend(_bullets(_deduplicate(resolved)))
    lines.extend(["", "Still missing:"])
    lines.extend(_bullets(_deduplicate(still_missing)))
    lines.extend(["", "Confirmed as not provided:"])
    lines.extend(_bullets(confirmed_none))
    lines.extend(["", "No additional information provided:"])
    lines.extend(_bullets(no_additional))
    lines.extend(["", "Skipped:"])
    lines.extend(_bullets(skipped))
    lines.extend(["", "Declined:"])
    lines.extend(_bullets(declined))
    return lines


def education_identity(record: dict[str, Any]) -> str:
    return f"{record['institution']} — {record['degree']} in {record['field_of_study']}"


def record_identity(topic: str, record: dict[str, Any]) -> str:
    if topic == "education":
        return education_identity(record)
    if topic == "experience_overview":
        return f"{record['organization_or_project_name']} — {record['title_or_role']}"
    if topic == "skills":
        return record["skill_name"]
    return TOPIC_LABELS.get(topic, topic.replace("_", " ").title())


def question_label(question: str) -> str:
    exact = {
        "What is your current location?": "Current location",
        "What is your current education or employment status?": "Current status",
        "Which career fields interest you?": "Career interests",
        "What kinds of work activities do you prefer?": "Preferred work activities",
        "Which industries interest you?": "Preferred industries",
        "Are there any roles you are currently considering?": "Roles under consideration",
        "Which locations are you targeting?": "Target locations",
        "Do you have any work authorization or visa constraints?": "Work authorization",
        "Do you prefer remote, hybrid, or onsite work?": "Work arrangement",
        "Are you seeking an internship, a full-time role, or either?": "Employment type",
        "When would you like to start your next role?": "Target start date",
    }
    if question in exact:
        return exact[question]
    if question.startswith("What did you do or accomplish as "):
        return "Experience summary"
    if "education background" in question:
        return "Education"
    if question.startswith("What work, internship"):
        return "Experience"
    if question.startswith("What skills"):
        return "Skills"
    return question


def _format_date_range(start: str | None, end: str | None, *, ongoing: bool = False) -> str:
    if start is None and end is None:
        return "Not provided"
    start_label = format_date(start)
    end_label = format_date(end, ongoing=ongoing and start is not None)
    return f"{start_label} – {end_label}"


def _bullets(values: Iterable[str]) -> list[str]:
    items = list(values)
    return [f"- {item}" for item in items] if items else ["- None"]


def _deduplicate(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _status_value(status: Any) -> str:
    return getattr(status, "value", status)
