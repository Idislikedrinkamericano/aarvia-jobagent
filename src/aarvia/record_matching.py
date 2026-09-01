"""Deterministic identity matching for Profile list-entry refinement."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


IDENTITY_FIELDS = {
    "education": ("institution", "degree", "field_of_study"),
    "experience_overview": (
        "organization_or_project_name",
        "title_or_role",
    ),
    "skills": ("skill_name",),
}


def normalize_identity_text(value: str) -> str:
    """Apply only lossless text normalization suitable for exact matching."""
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", normalized.strip()).casefold()


def stable_record_identity(topic: str, record: dict[str, Any]) -> tuple[str, ...]:
    try:
        fields = IDENTITY_FIELDS[topic]
    except KeyError as error:
        raise ValueError(f"unsupported record topic: {topic}") from error
    return tuple(normalize_identity_text(record[field]) for field in fields)


def find_refinement_matches(
    topic: str,
    existing: list[dict[str, Any]],
    candidate: dict[str, Any],
) -> list[int]:
    """Return exact stable-identity matches, with type as an ambiguity tie-breaker."""
    identity = stable_record_identity(topic, candidate)
    matches = [
        index
        for index, record in enumerate(existing)
        if stable_record_identity(topic, record) == identity
    ]
    if topic != "experience_overview" or len(matches) <= 1:
        return matches

    candidate_type = normalize_identity_text(candidate["experience_type"])
    type_matches = [
        index
        for index in matches
        if normalize_identity_text(existing[index]["experience_type"]) == candidate_type
    ]
    return type_matches if len(type_matches) == 1 else matches


def identity_field_values_equivalent(topic: str, field: str, left: Any, right: Any) -> bool:
    return (
        field in IDENTITY_FIELDS.get(topic, ())
        and isinstance(left, str)
        and isinstance(right, str)
        and normalize_identity_text(left) == normalize_identity_text(right)
    )
