"""Structured career profile models and strict dictionary validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
import re
from typing import Any, ClassVar, Mapping


class ProfileValidationError(ValueError):
    """Raised when career profile input does not match the expected schema."""


_DATE_PATTERN = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?$")


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProfileValidationError(f"{path} must be a dictionary")
    return value


def _reject_unknown(data: Mapping[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ProfileValidationError(f"{path} contains unknown fields: {', '.join(unknown)}")


def _text(value: Any, path: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        qualifier = "a non-empty string" if required else "a non-empty string or null"
        raise ProfileValidationError(f"{path} must be {qualifier}")
    return value.strip()


def _date_text(value: Any, path: str, *, required: bool = False) -> str | None:
    result = _text(value, path, required=required)
    if result is None:
        return None
    if not _DATE_PATTERN.fullmatch(result):
        raise ProfileValidationError(f"{path} must use YYYY-MM or YYYY-MM-DD format")
    try:
        date.fromisoformat(result if len(result) == 10 else f"{result}-01")
    except ValueError as error:
        raise ProfileValidationError(f"{path} is not a valid date") from error
    return result


def _string_list(value: Any, path: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProfileValidationError(f"{path} must be a list of strings")
    result: list[str] = []
    for index, item in enumerate(value):
        parsed = _text(item, f"{path}[{index}]", required=True)
        assert parsed is not None
        result.append(parsed)
    return result


def _objects(value: Any, model: type, path: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProfileValidationError(f"{path} must be a list")
    return [model.from_dict(_mapping(item, f"{path}[{index}]"), f"{path}[{index}]") for index, item in enumerate(value)]


@dataclass(eq=True)
class BasicProfile:
    name: str | None = None
    current_location: str | None = None
    current_status: str | None = None

    FIELDS: ClassVar[set[str]] = {"name", "current_location", "current_status"}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "basic_profile") -> BasicProfile:
        data = _mapping(value, path)
        _reject_unknown(data, cls.FIELDS, path)
        return cls(
            name=_text(data.get("name"), f"{path}.name"),
            current_location=_text(data.get("current_location"), f"{path}.current_location"),
            current_status=_text(data.get("current_status"), f"{path}.current_status"),
        )


@dataclass(eq=True)
class Education:
    institution: str
    degree: str
    field_of_study: str
    start_date: str | None
    expected_graduation_date: str | None
    gpa: str | None = None

    FIELDS: ClassVar[set[str]] = {
        "institution", "degree", "field_of_study", "start_date", "expected_graduation_date", "gpa"
    }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "education") -> Education:
        data = _mapping(value, path)
        _reject_unknown(data, cls.FIELDS, path)
        return cls(
            institution=_text(data.get("institution"), f"{path}.institution", required=True),
            degree=_text(data.get("degree"), f"{path}.degree", required=True),
            field_of_study=_text(data.get("field_of_study"), f"{path}.field_of_study", required=True),
            start_date=_date_text(data.get("start_date"), f"{path}.start_date"),
            expected_graduation_date=_date_text(
                data.get("expected_graduation_date"), f"{path}.expected_graduation_date"
            ),
            gpa=_text(data.get("gpa"), f"{path}.gpa"),
        )


@dataclass(eq=True)
class ExperienceOverview:
    experience_type: str
    organization_or_project_name: str
    title_or_role: str
    short_factual_summary: str | None
    start_date: str | None
    end_date: str | None = None

    FIELDS: ClassVar[set[str]] = {
        "experience_type", "organization_or_project_name", "title_or_role",
        "short_factual_summary", "start_date", "end_date"
    }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "experience_overview") -> ExperienceOverview:
        data = _mapping(value, path)
        _reject_unknown(data, cls.FIELDS, path)
        return cls(
            experience_type=_text(data.get("experience_type"), f"{path}.experience_type", required=True),
            organization_or_project_name=_text(
                data.get("organization_or_project_name"), f"{path}.organization_or_project_name", required=True
            ),
            title_or_role=_text(data.get("title_or_role"), f"{path}.title_or_role", required=True),
            short_factual_summary=_text(
                data.get("short_factual_summary"), f"{path}.short_factual_summary"
            ),
            start_date=_date_text(data.get("start_date"), f"{path}.start_date"),
            end_date=_date_text(data.get("end_date"), f"{path}.end_date"),
        )


@dataclass(eq=True)
class Skill:
    skill_name: str
    category: str
    self_reported_proficiency: str | None = None

    FIELDS: ClassVar[set[str]] = {"skill_name", "category", "self_reported_proficiency"}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "skills") -> Skill:
        data = _mapping(value, path)
        _reject_unknown(data, cls.FIELDS, path)
        return cls(
            skill_name=_text(data.get("skill_name"), f"{path}.skill_name", required=True),
            category=_text(data.get("category"), f"{path}.category", required=True),
            self_reported_proficiency=_text(
                data.get("self_reported_proficiency"), f"{path}.self_reported_proficiency"
            ),
        )


@dataclass(eq=True)
class CareerPreferences:
    interested_fields: list[str] = field(default_factory=list)
    preferred_work_activities: list[str] = field(default_factory=list)
    preferred_industries: list[str] = field(default_factory=list)
    fields_or_activities_to_avoid: list[str] = field(default_factory=list)
    currently_considered_roles: list[str] = field(default_factory=list)

    FIELDS: ClassVar[set[str]] = {
        "interested_fields", "preferred_work_activities", "preferred_industries",
        "fields_or_activities_to_avoid", "currently_considered_roles"
    }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "career_preferences") -> CareerPreferences:
        data = _mapping(value, path)
        _reject_unknown(data, cls.FIELDS, path)
        return cls(**{name: _string_list(data.get(name), f"{path}.{name}") for name in cls.FIELDS})


@dataclass(eq=True)
class Constraints:
    target_locations: list[str] = field(default_factory=list)
    work_authorization_or_visa_constraints: str | None = None
    work_arrangement_preference: str | None = None
    employment_type_preference: str | None = None
    target_start_date: str | None = None
    other_constraints: list[str] = field(default_factory=list)

    FIELDS: ClassVar[set[str]] = {
        "target_locations", "work_authorization_or_visa_constraints", "work_arrangement_preference",
        "employment_type_preference", "target_start_date", "other_constraints"
    }
    WORK_ARRANGEMENTS: ClassVar[set[str]] = {"remote", "hybrid", "onsite", "flexible"}
    EMPLOYMENT_TYPES: ClassVar[set[str]] = {"internship", "full-time", "either"}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "constraints") -> Constraints:
        data = _mapping(value, path)
        _reject_unknown(data, cls.FIELDS, path)
        arrangement = _text(data.get("work_arrangement_preference"), f"{path}.work_arrangement_preference")
        employment = _text(data.get("employment_type_preference"), f"{path}.employment_type_preference")
        if arrangement is not None and arrangement not in cls.WORK_ARRANGEMENTS:
            raise ProfileValidationError(
                f"{path}.work_arrangement_preference must be one of: {', '.join(sorted(cls.WORK_ARRANGEMENTS))}"
            )
        if employment is not None and employment not in cls.EMPLOYMENT_TYPES:
            raise ProfileValidationError(
                f"{path}.employment_type_preference must be one of: {', '.join(sorted(cls.EMPLOYMENT_TYPES))}"
            )
        return cls(
            target_locations=_string_list(data.get("target_locations"), f"{path}.target_locations"),
            work_authorization_or_visa_constraints=_text(
                data.get("work_authorization_or_visa_constraints"),
                f"{path}.work_authorization_or_visa_constraints",
            ),
            work_arrangement_preference=arrangement,
            employment_type_preference=employment,
            target_start_date=_date_text(data.get("target_start_date"), f"{path}.target_start_date"),
            other_constraints=_string_list(data.get("other_constraints"), f"{path}.other_constraints"),
        )


@dataclass(eq=True)
class CareerProfile:
    basic_profile: BasicProfile = field(default_factory=BasicProfile)
    education: list[Education] = field(default_factory=list)
    experience_overview: list[ExperienceOverview] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)
    career_preferences: CareerPreferences = field(default_factory=CareerPreferences)
    constraints: Constraints = field(default_factory=Constraints)
    open_questions: list[str] = field(default_factory=list)

    FIELDS: ClassVar[set[str]] = {
        "basic_profile", "education", "experience_overview", "skills",
        "career_preferences", "constraints", "open_questions"
    }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CareerProfile:
        data = _mapping(value, "profile")
        _reject_unknown(data, cls.FIELDS, "profile")
        supplied_questions = _string_list(data.get("open_questions"), "profile.open_questions")
        profile = cls(
            basic_profile=BasicProfile.from_dict(data.get("basic_profile", {})),
            education=_objects(data.get("education"), Education, "education"),
            experience_overview=_objects(
                data.get("experience_overview"), ExperienceOverview, "experience_overview"
            ),
            skills=_objects(data.get("skills"), Skill, "skills"),
            career_preferences=CareerPreferences.from_dict(data.get("career_preferences", {})),
            constraints=Constraints.from_dict(data.get("constraints", {})),
        )
        from .discovery import generate_open_questions

        generated_questions = generate_open_questions(profile)
        if "open_questions" in data:
            generated_set = set(generated_questions)
            if (
                any(question not in generated_set for question in supplied_questions)
                or supplied_questions
                != [question for question in generated_questions if question in supplied_questions]
            ):
                raise ProfileValidationError(
                    "profile.open_questions does not match the profile's missing information"
                )
            profile.open_questions = supplied_questions
        else:
            profile.open_questions = generated_questions
        return profile

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of the profile."""
        return asdict(self)
