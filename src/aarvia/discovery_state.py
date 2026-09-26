"""Persistent follow-up state and crash-recoverable Profile/state commits."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import Enum
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .profile import CareerProfile, ProfileValidationError


DISCOVERY_PATHS = (
    "basic_profile.name",
    "basic_profile.current_location",
    "basic_profile.current_status",
    "career_preferences.interested_fields",
    "career_preferences.preferred_work_activities",
    "career_preferences.preferred_industries",
    "career_preferences.currently_considered_roles",
    "constraints.target_locations",
    "constraints.work_authorization_or_visa_constraints",
    "constraints.work_arrangement_preference",
    "constraints.employment_type_preference",
    "constraints.target_start_date",
    "education_details",
    "experience_details",
    "skill_proficiency",
)

LEGACY_TOPIC_PATHS = {
    "basic_profile": DISCOVERY_PATHS[0:3],
    "career_preferences": DISCOVERY_PATHS[3:7],
    "constraints": DISCOVERY_PATHS[7:12],
    "education_details": ("education_details",),
    "experience_details": ("experience_details",),
    "skill_proficiency": ("skill_proficiency",),
}


class FollowUpStatus(str, Enum):
    UNANSWERED = "unanswered"
    ANSWERED = "answered"
    CONFIRMED_NONE = "confirmed_none"
    SKIPPED = "skipped"
    DECLINED = "declined"


@dataclass(eq=True)
class DiscoveryState:
    fields: dict[str, FollowUpStatus] = field(
        default_factory=lambda: {
            path: FollowUpStatus.UNANSWERED for path in DISCOVERY_PATHS
        }
    )

    def mark(self, path: str, status: FollowUpStatus) -> None:
        if path not in DISCOVERY_PATHS:
            raise ProfileValidationError(f"unknown discovery state path: {path}")
        self.fields[path] = status

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "aarvia.discovery_state",
            "version": 2,
            "fields": {path: self.fields[path].value for path in DISCOVERY_PATHS},
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DiscoveryState:
        if not isinstance(value, Mapping):
            raise ProfileValidationError("discovery state must be a dictionary")
        version = value.get("version", 1)
        if version == 1:
            raise ProfileValidationError("version 1 discovery state requires Profile-aware migration")
        unknown = sorted(set(value) - {"schema", "version", "fields"})
        if unknown:
            raise ProfileValidationError(
                f"discovery state contains unknown fields: {', '.join(unknown)}"
            )
        if value.get("schema") != "aarvia.discovery_state" or version != 2:
            raise ProfileValidationError("unsupported discovery state version")
        raw_fields = value.get("fields", {})
        if not isinstance(raw_fields, Mapping):
            raise ProfileValidationError("discovery state fields must be a dictionary")
        unknown_paths = sorted(set(raw_fields) - set(DISCOVERY_PATHS))
        if unknown_paths:
            raise ProfileValidationError(
                f"discovery state contains unknown paths: {', '.join(unknown_paths)}"
            )
        state = cls()
        for path, raw_status in raw_fields.items():
            try:
                state.fields[path] = FollowUpStatus(raw_status)
            except (TypeError, ValueError) as error:
                raise ProfileValidationError(
                    f"discovery state for {path} has invalid status: {raw_status!r}"
                ) from error
        return state


def state_path_for(profile_path: str | Path) -> Path:
    profile = Path(profile_path)
    return profile.with_name(f"{profile.stem}.discovery.json")


def load_discovery_state(
    path: str | Path,
    *,
    profile: CareerProfile | None = None,
    missing_paths: set[str] | None = None,
) -> DiscoveryState:
    source = Path(path)
    if not source.exists():
        return DiscoveryState()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ProfileValidationError(f"{source} does not contain valid JSON") from error
    if raw.get("version", 1) == 1:
        if profile is None:
            raise ProfileValidationError(
                "version 1 discovery state requires the associated CareerProfile"
            )
        return _migrate_v1(raw, profile, missing_paths or set())
    return DiscoveryState.from_dict(raw)


def _migrate_v1(
    raw: Mapping[str, Any], profile: CareerProfile, missing_paths: set[str]
) -> DiscoveryState:
    unknown = sorted(set(raw) - {"version", "topics"})
    if unknown or raw.get("version", 1) != 1 or not isinstance(raw.get("topics", {}), Mapping):
        raise ProfileValidationError("invalid version 1 discovery state")
    legacy = raw.get("topics", {})
    state = DiscoveryState()
    for path in DISCOVERY_PATHS:
        if _profile_has_value(profile, path) or (
            path in {"education_details", "experience_details", "skill_proficiency"}
            and path not in missing_paths
        ):
            state.fields[path] = FollowUpStatus.ANSWERED
            continue
        parent = next(
            (topic for topic, paths in LEGACY_TOPIC_PATHS.items() if path in paths),
            None,
        )
        try:
            old_status = FollowUpStatus(legacy.get(parent, "unanswered"))
        except ValueError as error:
            raise ProfileValidationError(f"invalid version 1 status for {parent}") from error
        if path in missing_paths and old_status == FollowUpStatus.ANSWERED:
            state.fields[path] = FollowUpStatus.UNANSWERED
        elif old_status in {
            FollowUpStatus.CONFIRMED_NONE,
            FollowUpStatus.SKIPPED,
            FollowUpStatus.DECLINED,
        }:
            state.fields[path] = old_status
    return state


def _profile_has_value(profile: CareerProfile, path: str) -> bool:
    if path in {"education_details", "experience_details", "skill_proficiency"}:
        return False
    section, field_name = path.split(".", 1)
    value = getattr(getattr(profile, section), field_name)
    return value not in (None, "", [])


def save_profile_and_state(
    profile: CareerProfile,
    profile_path: str | Path,
    state: DiscoveryState,
    state_path: str | Path,
) -> None:
    """Commit both files with a rollback journal recoverable on the next run."""
    if not isinstance(profile, CareerProfile):
        raise TypeError("profile must be a CareerProfile")
    profile = profile_with_resolved_open_questions(profile, state)
    state = DiscoveryState.from_dict(state.to_dict())
    profile_target = Path(profile_path)
    state_target = Path(state_path)
    profile_target.parent.mkdir(parents=True, exist_ok=True)
    state_target.parent.mkdir(parents=True, exist_ok=True)
    journal = profile_target.with_name(f".{profile_target.name}.follow-up-transaction.json")
    recover_follow_up_transaction(journal)

    originals = {
        str(profile_target): _encoded_contents(profile_target),
        str(state_target): _encoded_contents(state_target),
    }
    _write_json_atomic(journal, {"version": 1, "originals": originals})
    profile_temp = _write_temp_json(profile_target, profile.to_dict())
    state_temp = _write_temp_json(state_target, state.to_dict())
    try:
        os.replace(profile_temp, profile_target)
        os.replace(state_temp, state_target)
    except BaseException:
        _restore_originals(originals)
        raise
    finally:
        profile_temp.unlink(missing_ok=True)
        state_temp.unlink(missing_ok=True)
    journal.unlink(missing_ok=True)


def profile_with_resolved_open_questions(
    profile: CareerProfile, state: DiscoveryState
) -> CareerProfile:
    """Return a validated Profile whose questions reflect explicit confirmed-none state."""
    from .discovery import generate_open_questions
    from .follow_up import OPEN_QUESTION_PATHS

    validated_state = DiscoveryState.from_dict(state.to_dict())
    questions = [
        question
        for question in generate_open_questions(profile)
        if validated_state.fields.get(OPEN_QUESTION_PATHS.get(question))
        != FollowUpStatus.CONFIRMED_NONE
    ]
    data = profile.to_dict()
    data["open_questions"] = questions
    return CareerProfile.from_dict(data)


def recover_follow_up_transaction(journal_path: str | Path) -> None:
    journal = Path(journal_path)
    if not journal.exists():
        return
    try:
        payload = json.loads(journal.read_text(encoding="utf-8"))
        originals = payload["originals"]
        if payload.get("version") != 1 or not isinstance(originals, dict):
            raise ValueError("invalid transaction journal")
        _restore_originals(originals)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ProfileValidationError(f"cannot recover follow-up transaction: {journal}") from error
    journal.unlink(missing_ok=True)


def _encoded_contents(path: Path) -> str | None:
    if not path.exists():
        return None
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _restore_originals(originals: Mapping[str, str | None]) -> None:
    for raw_path, encoded in originals.items():
        target = Path(raw_path)
        if encoded is None:
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = _write_temp_bytes(target, base64.b64decode(encoded))
        os.replace(temporary, target)


def _write_temp_json(target: Path, value: Any) -> Path:
    return _write_temp_bytes(
        target,
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def _write_temp_bytes(target: Path, value: bytes) -> Path:
    with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False) as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
        return Path(handle.name)


def _write_json_atomic(target: Path, value: Any) -> None:
    temporary = _write_temp_json(target, value)
    os.replace(temporary, target)
