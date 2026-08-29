"""Deterministic normalization for supported provider output aliases."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .candidates import TOPICS
from .profile import ProfileValidationError


SUPPORTED_ALIASES = {
    "projects",
    "target_roles",
    "target_locations",
    "target_employment_type",
}


def _is_recursively_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return all(_is_recursively_empty(item) for item in value)
    if isinstance(value, Mapping):
        return all(_is_recursively_empty(item) for item in value.values())
    return False


def _remove_empty_unknown_fields(data: dict[str, Any]) -> None:
    known_fields = set(TOPICS) | SUPPORTED_ALIASES
    for field_name in list(data):
        if field_name not in known_fields and _is_recursively_empty(data[field_name]):
            del data[field_name]


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    current = data.get(name)
    if current is None:
        current = {}
        data[name] = current
    if not isinstance(current, dict):
        raise ProfileValidationError(f"candidate.{name} must be a dictionary")
    return current


def _stable_merge(existing: Any, incoming: Any, path: str) -> list[Any]:
    if existing is None:
        existing = []
    if not isinstance(existing, list) or not isinstance(incoming, list):
        raise ProfileValidationError(f"{path} must be a list")
    result = list(existing)
    for item in incoming:
        if item not in result:
            result.append(item)
    return result


def _normalize_projects(data: dict[str, Any]) -> None:
    if "projects" not in data:
        return
    raw_projects = data.pop("projects")
    if not isinstance(raw_projects, list):
        raise ProfileValidationError("candidate.projects must be a list")
    projects: list[dict[str, Any]] = []
    for index, raw_project in enumerate(raw_projects):
        if not isinstance(raw_project, Mapping):
            raise ProfileValidationError(f"candidate.projects[{index}] must be a dictionary")
        project = dict(raw_project)
        experience_type = project.get("experience_type")
        if experience_type not in (None, "project"):
            raise ProfileValidationError(
                f"candidate.projects[{index}].experience_type must be project"
            )
        project["experience_type"] = "project"
        projects.append(project)
    data["experience_overview"] = _stable_merge(
        data.get("experience_overview"),
        projects,
        "candidate.experience_overview",
    )


def _normalize_list_alias(
    data: dict[str, Any],
    alias: str,
    section_name: str,
    field_name: str,
) -> None:
    if alias not in data:
        return
    incoming = data.pop(alias)
    section = _section(data, section_name)
    section[field_name] = _stable_merge(
        section.get(field_name),
        incoming,
        f"candidate.{alias}",
    )


def _employment_type(value: Any) -> str:
    values = value if isinstance(value, list) else [value]
    if not values:
        raise ProfileValidationError("candidate.target_employment_type must not be empty")
    aliases = {
        "internship": "internship",
        "full-time": "full-time",
        "full time": "full-time",
        "full_time": "full-time",
        "either": "either",
    }
    normalized: list[str] = []
    for index, item in enumerate(values):
        if not isinstance(item, str) or item.strip().casefold() not in aliases:
            raise ProfileValidationError(
                f"candidate.target_employment_type[{index}] must be internship, full-time, or either"
            )
        result = aliases[item.strip().casefold()]
        if result not in normalized:
            normalized.append(result)
    if normalized == ["either"] or set(normalized) == {"internship", "full-time"}:
        return "either"
    if len(normalized) == 1:
        return normalized[0]
    raise ProfileValidationError("candidate.target_employment_type contains conflicting values")


def _normalize_employment_type(data: dict[str, Any]) -> None:
    if "target_employment_type" not in data:
        return
    value = _employment_type(data.pop("target_employment_type"))
    constraints = _section(data, "constraints")
    existing = constraints.get("employment_type_preference")
    if existing not in (None, value):
        raise ProfileValidationError(
            "candidate target employment type conflicts with constraints.employment_type_preference"
        )
    constraints["employment_type_preference"] = value


def normalize_candidate_data(value: Mapping[str, Any]) -> dict[str, Any]:
    """Remove empty provider extras, then normalize only documented aliases."""
    if not isinstance(value, Mapping):
        raise ProfileValidationError("candidate profile data must be a dictionary")
    data = deepcopy(dict(value))
    _remove_empty_unknown_fields(data)
    _normalize_projects(data)
    _normalize_list_alias(
        data,
        "target_roles",
        "career_preferences",
        "currently_considered_roles",
    )
    _normalize_list_alias(data, "target_locations", "constraints", "target_locations")
    _normalize_employment_type(data)
    return data
