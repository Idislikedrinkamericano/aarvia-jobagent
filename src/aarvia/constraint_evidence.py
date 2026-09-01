"""Deterministic recovery of explicitly labelled Constraint facts."""

from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any, Mapping

from .profile import ProfileValidationError


_LOCATION_ASSIGNMENT = re.compile(
    r"\btarget_locations\s*=\s*(\[[^\]\n]*\])", re.IGNORECASE
)
_LOCATION_LABEL = re.compile(
    r"\btarget[ _-]?locations?\s*(?:are|is|:|=)\s*(.+)", re.IGNORECASE
)
_LOCATION_STOP = re.compile(
    r"\b(?:employment[ _-]?type|target[ _-]?start|start(?:ing)?|work[ _-]?arrangement|"
    r"work[ _-]?authorization|visa|sponsorship)\b",
    re.IGNORECASE,
)
_MONTHS = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}
_MONTH_YEAR = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\s+(\d{4})\b", re.IGNORECASE
)


def apply_constraint_evidence(
    candidate_data: Mapping[str, Any], evidence_text: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply only Constraint values that are explicit in the evidence."""
    data = deepcopy(dict(candidate_data))
    constraints = data.get("constraints")
    if not isinstance(constraints, dict):
        return data, {}
    patch: dict[str, Any] = {}

    locations = _explicit_target_locations(evidence_text)
    if locations and constraints.get("target_locations") != locations:
        constraints["target_locations"] = locations
        patch["target_locations"] = locations

    if _means_flexible(evidence_text) and constraints.get("work_arrangement_preference") != "flexible":
        constraints["work_arrangement_preference"] = "flexible"
        patch["work_arrangement_preference"] = "flexible"

    start_date = _explicit_month_year(evidence_text)
    if start_date is not None and constraints.get("target_start_date") != start_date:
        constraints["target_start_date"] = start_date
        patch["target_start_date"] = start_date
    elif _contains_season_year(evidence_text) and constraints.get("target_start_date") is not None:
        constraints["target_start_date"] = None
        patch["target_start_date"] = None

    return data, patch


def constraint_change_is_supported(path: str, value: Any, evidence_text: str) -> bool:
    """Recognize the two lossless Constraint normalizations used after extraction."""
    if path.endswith("work_arrangement_preference") and value == "flexible":
        return _means_flexible(evidence_text)
    if path.endswith("target_start_date") and isinstance(value, str):
        return _explicit_month_year(evidence_text) == value
    return False


def _explicit_target_locations(text: str) -> list[str]:
    assignment = _LOCATION_ASSIGNMENT.search(text)
    if assignment:
        try:
            value = json.loads(assignment.group(1))
        except json.JSONDecodeError as error:
            raise ProfileValidationError("target_locations assignment must be a valid JSON list") from error
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ProfileValidationError("target_locations assignment must be a list of non-empty strings")
        return _deduplicate([item.strip() for item in value])

    labelled = _LOCATION_LABEL.search(text)
    if not labelled:
        return []
    segment = labelled.group(1).splitlines()[0].split(".")[0]
    stop = _LOCATION_STOP.search(segment)
    if stop:
        segment = segment[:stop.start()]
    segment = segment.strip(" .;,:")
    values = [item.strip(" .;,") for item in re.split(r"\s+and\s+|,", segment)]
    return _deduplicate([item for item in values if item])


def _means_flexible(text: str) -> bool:
    normalized = " ".join(text.casefold().replace(",", " ").split())
    if "flexible" in normalized:
        return True
    has_all_modes = all(mode in normalized for mode in ("onsite", "hybrid", "remote"))
    return has_all_modes and "acceptable" in normalized


def _explicit_month_year(text: str) -> str | None:
    match = _MONTH_YEAR.search(text)
    if not match:
        return None
    prefix = text[max(0, match.start() - 40):match.start()].casefold()
    if not any(marker in prefix for marker in ("start", "available", "begin")):
        return None
    return f"{match.group(2)}-{_MONTHS[match.group(1).casefold()]}"


def _contains_season_year(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:spring|summer|fall|autumn|winter)\s+\d{4}\b",
            text,
            re.IGNORECASE,
        )
    )


def _deduplicate(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key not in seen:
            result.append(value)
            seen.add(key)
    return result
