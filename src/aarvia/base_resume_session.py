"""Private resumable layout session for Base Resume construction."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .base_resume import BaseResumeArtifact, build_base_resume
from .career_direction import profile_fingerprint
from .phase2_storage import load_phase2_json, save_phase2_json
from .resume_material import ResumeSectionType
from .role_catalog import Phase2ValidationError, _iso_datetime, _mapping, _reject_unknown, _stable_id, _string_tuple, _text


BASE_RESUME_SESSION_SCHEMA = "aarvia.base_resume_session"
BASE_RESUME_SESSION_SCHEMA_VERSION = 1


def _hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"base_resume_session_{hashlib.sha256(payload.encode()).hexdigest()[:24]}"


@dataclass(frozen=True, eq=True)
class BaseResumeSession:
    session_id: str
    target_output: str
    profile_fingerprint: str
    resume_material_artifact_id: str
    resume_wording_review_id: str
    supplement_id: str
    supersedes_base_resume_id: str | None
    omitted_subject_ids: tuple[str, ...]
    section_order: tuple[ResumeSectionType, ...]
    entry_order: tuple[tuple[str, tuple[str, ...]], ...]
    bullet_order: tuple[tuple[str, tuple[str, ...]], ...]
    skill_group_order: tuple[str, ...]
    current_section: str
    created_at: str
    updated_at: str
    schema: str = BASE_RESUME_SESSION_SCHEMA
    schema_version: int = BASE_RESUME_SESSION_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BaseResumeSession":
        data = _mapping(value, "base_resume_session")
        _reject_unknown(data, set(cls.__dataclass_fields__), "base_resume_session")
        if data.get("schema") != BASE_RESUME_SESSION_SCHEMA or data.get("schema_version") != 1:
            raise Phase2ValidationError("unsupported Base Resume session schema")
        def orders(name: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
            raw = _mapping(data.get(name), f"base_resume_session.{name}")
            return tuple(sorted((_text(key, f"{name} key"), _string_tuple(item, f"{name}.{key}")) for key, item in raw.items()))
        raw_sections = data.get("section_order")
        if not isinstance(raw_sections, list):
            raise Phase2ValidationError("base_resume_session.section_order must be a list")
        previous = data.get("supersedes_base_resume_id")
        result = cls(
            _stable_id(data.get("session_id"), "base_resume_session.session_id"),
            _text(data.get("target_output"), "base_resume_session.target_output"),
            _text(data.get("profile_fingerprint"), "base_resume_session.profile_fingerprint"),
            _stable_id(data.get("resume_material_artifact_id"), "base_resume_session.resume_material_artifact_id"),
            _stable_id(data.get("resume_wording_review_id"), "base_resume_session.resume_wording_review_id"),
            _stable_id(data.get("supplement_id"), "base_resume_session.supplement_id"),
            None if previous is None else _stable_id(previous, "base_resume_session.supersedes_base_resume_id"),
            _string_tuple(data.get("omitted_subject_ids"), "base_resume_session.omitted_subject_ids"),
            tuple(ResumeSectionType(item) for item in raw_sections),
            orders("entry_order"), orders("bullet_order"),
            _string_tuple(data.get("skill_group_order"), "base_resume_session.skill_group_order"),
            _text(data.get("current_section"), "base_resume_session.current_section"),
            _iso_datetime(data.get("created_at"), "base_resume_session.created_at"),
            _iso_datetime(data.get("updated_at"), "base_resume_session.updated_at"),
        )
        if result.omitted_subject_ids != tuple(sorted(set(result.omitted_subject_ids))):
            raise Phase2ValidationError("session omissions must be unique and sorted")
        if result.session_id != generate_base_resume_session_id(result):
            raise Phase2ValidationError("Base Resume session ID is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "schema_version": self.schema_version,
            "session_id": self.session_id, "target_output": self.target_output,
            "profile_fingerprint": self.profile_fingerprint,
            "resume_material_artifact_id": self.resume_material_artifact_id,
            "resume_wording_review_id": self.resume_wording_review_id,
            "supplement_id": self.supplement_id,
            "supersedes_base_resume_id": self.supersedes_base_resume_id,
            "omitted_subject_ids": list(self.omitted_subject_ids),
            "section_order": [item.value for item in self.section_order],
            "entry_order": {key: list(value) for key, value in self.entry_order},
            "bullet_order": {key: list(value) for key, value in self.bullet_order},
            "skill_group_order": list(self.skill_group_order),
            "current_section": self.current_section,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    def validate(self, *, target_output: str | Path, resume_material: Any,
                 resume_wording_review: Any, resume_profile_supplement: Any,
                 superseded_base_resume: BaseResumeArtifact | None = None, **context: Any) -> None:
        if BaseResumeSession.from_dict(self.to_dict()) != self:
            raise Phase2ValidationError("Base Resume session is not canonical")
        expected = (
            str(Path(target_output).resolve()), profile_fingerprint(context["profile"]),
            resume_material.resume_material_id, resume_wording_review.wording_review_id,
            resume_profile_supplement.supplement_id,
            None if superseded_base_resume is None else superseded_base_resume.base_resume_id,
        )
        actual = (
            self.target_output, self.profile_fingerprint, self.resume_material_artifact_id,
            self.resume_wording_review_id, self.supplement_id, self.supersedes_base_resume_id,
        )
        if actual != expected:
            raise Phase2ValidationError("Base Resume session provenance, output, or revision is stale")
        build_base_resume(
            resume_material=resume_material,
            resume_wording_review=resume_wording_review,
            resume_profile_supplement=resume_profile_supplement,
            omitted_subject_ids=self.omitted_subject_ids,
            section_order=self.section_order,
            entry_order={ResumeSectionType(key): values for key, values in self.entry_order},
            bullet_order=dict(self.bullet_order),
            skill_group_order=self.skill_group_order,
            created_at=self.updated_at,
            superseded_base_resume=superseded_base_resume,
            **context,
        )


def generate_base_resume_session_id(value: BaseResumeSession) -> str:
    data = value.to_dict(); data.pop("session_id")
    return _hash(data)


def build_base_resume_session(*, target_output: str | Path, profile_fingerprint: str,
                              resume_material: Any, resume_wording_review: Any,
                              resume_profile_supplement: Any,
                              omitted_subject_ids: Sequence[str],
                              section_order: Sequence[ResumeSectionType],
                              entry_order: Mapping[str, Sequence[str]],
                              bullet_order: Mapping[str, Sequence[str]],
                              skill_group_order: Sequence[str], current_section: str,
                              updated_at: str, previous_session: BaseResumeSession | None = None,
                              superseded_base_resume: BaseResumeArtifact | None = None) -> BaseResumeSession:
    provisional = BaseResumeSession(
        "base_resume_session_pending", str(Path(target_output).resolve()), profile_fingerprint,
        resume_material.resume_material_id, resume_wording_review.wording_review_id,
        resume_profile_supplement.supplement_id,
        None if superseded_base_resume is None else superseded_base_resume.base_resume_id,
        tuple(sorted(set(omitted_subject_ids))), tuple(section_order),
        tuple(sorted((key, tuple(value)) for key, value in entry_order.items())),
        tuple(sorted((key, tuple(value)) for key, value in bullet_order.items())),
        tuple(skill_group_order), current_section,
        updated_at if previous_session is None else previous_session.created_at, updated_at,
    )
    return BaseResumeSession.from_dict({**provisional.to_dict(), "session_id": generate_base_resume_session_id(provisional)})


def save_base_resume_session(value: BaseResumeSession, path: str | Path, **context: Any) -> Path:
    value.validate(**context)
    return save_phase2_json(value.to_dict(), path)


def load_base_resume_session(path: str | Path, **context: Any) -> BaseResumeSession:
    value = BaseResumeSession.from_dict(load_phase2_json(path))
    value.validate(**context)
    return value
