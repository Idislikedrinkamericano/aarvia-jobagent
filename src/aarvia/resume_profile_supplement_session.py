"""Private resumable CLI sessions for Resume Profile Supplement entry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .career_direction import profile_fingerprint
from .phase2_storage import load_phase2_json, save_phase2_json
from .profile import CareerProfile
from .resume_material import ResumeMaterialArtifact
from .resume_profile_supplement import (
    ContactField,
    ProfileStructuralSelection,
    ResumeProfileSupplementArtifact,
    SupplementInclusionReview,
    build_resume_profile_supplement,
)
from .resume_wording import ResumeWordingReviewArtifact
from .role_catalog import (
    Phase2ValidationError,
    _enum,
    _iso_datetime,
    _mapping,
    _reject_unknown,
    _stable_id,
    _text,
)


RESUME_PROFILE_SUPPLEMENT_SESSION_SCHEMA = "aarvia.resume_profile_supplement_session"
RESUME_PROFILE_SUPPLEMENT_SESSION_SCHEMA_VERSION = 1
RESUME_PROFILE_SUPPLEMENT_SESSION_ID_VERSION = "resume-profile-supplement-session-v1"


class SupplementSessionSection(str, Enum):
    CONTACT = "contact"
    EDUCATION = "education"
    SKILLS = "skills"
    EXPERIENCE = "experience"
    FINAL_REVIEW = "final_review"


def _stable_hash(prefix: str, value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _optional_id(value: Any, path: str) -> str | None:
    return None if value is None else _stable_id(value, path)


def _parsed_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True, eq=True)
class SupplementSessionProvenance:
    profile_fingerprint: str
    resume_material_artifact_id: str
    resume_material_schema_version: int
    resume_wording_review_id: str
    resume_wording_schema_version: int
    mapping_artifact_id: str
    mapping_schema_version: int
    binding_review_artifact_id: str | None
    allocation_review_artifact_id: str | None
    recommendation_set_id: str
    recommendation_schema_version: int
    decision_id: str
    decision_schema_version: int
    gap_analysis_id: str
    gap_analysis_schema_version: int
    evidence_bank_id: str
    evidence_bank_schema_version: int
    enrichment_artifact_id: str
    enrichment_schema_version: int
    rubric_version: str
    rubric_fingerprint: str
    catalog_version: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> SupplementSessionProvenance:
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        optional = {"binding_review_artifact_id", "allocation_review_artifact_id"}
        ids = {
            "resume_material_artifact_id", "resume_wording_review_id",
            "mapping_artifact_id", "recommendation_set_id", "decision_id",
            "gap_analysis_id", "evidence_bank_id", "enrichment_artifact_id",
        }
        versions = {
            "resume_material_schema_version": 1,
            "resume_wording_schema_version": 1,
            "mapping_schema_version": 5,
            "recommendation_schema_version": 7,
            "decision_schema_version": 2,
            "gap_analysis_schema_version": 2,
            "evidence_bank_schema_version": 1,
            "enrichment_schema_version": 1,
        }
        parsed: dict[str, Any] = {}
        for name in cls.__dataclass_fields__:
            raw = data.get(name)
            if name in optional:
                parsed[name] = _optional_id(raw, f"{path}.{name}")
            elif name in ids:
                parsed[name] = _stable_id(raw, f"{path}.{name}")
            elif name in versions:
                if raw != versions[name]:
                    raise Phase2ValidationError(f"{path}.{name} must equal {versions[name]}")
                parsed[name] = raw
            else:
                parsed[name] = _text(raw, f"{path}.{name}")
        return cls(**parsed)

    @classmethod
    def capture(
        cls, profile: CareerProfile, material: ResumeMaterialArtifact,
        wording: ResumeWordingReviewArtifact,
    ) -> SupplementSessionProvenance:
        return cls(
            profile_fingerprint(profile), material.resume_material_id, 1,
            wording.wording_review_id, 1, wording.mapping_artifact_id, 5,
            wording.binding_review_artifact_id,
            wording.allocation_review_artifact_id,
            wording.recommendation_set_id, 7, wording.decision_id, 2,
            wording.gap_analysis_id, 2, wording.evidence_bank_id, 1,
            wording.enrichment_artifact_id, 1, wording.rubric_version,
            wording.rubric_fingerprint, wording.catalog_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, eq=True)
class ResumeProfileSupplementSession:
    session_id: str
    target_output: str
    current_section: SupplementSessionSection
    provenance: SupplementSessionProvenance
    supersedes_supplement_id: str | None
    created_at: str
    updated_at: str
    contact_fields: tuple[ContactField, ...]
    structural_selections: tuple[ProfileStructuralSelection, ...]
    inclusion_reviews: tuple[SupplementInclusionReview, ...]
    schema: str = RESUME_PROFILE_SUPPLEMENT_SESSION_SCHEMA
    schema_version: int = RESUME_PROFILE_SUPPLEMENT_SESSION_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ResumeProfileSupplementSession:
        data = _mapping(value, "resume_profile_supplement_session")
        _reject_unknown(data, set(cls.__dataclass_fields__), "resume_profile_supplement_session")
        if data.get("schema") != RESUME_PROFILE_SUPPLEMENT_SESSION_SCHEMA or data.get("schema_version") != 1:
            raise Phase2ValidationError("unsupported Resume Profile Supplement session schema")
        for name in ("contact_fields", "structural_selections", "inclusion_reviews"):
            if not isinstance(data.get(name), list):
                raise Phase2ValidationError(f"session.{name} must be a list")
        result = cls(
            session_id=_stable_id(data.get("session_id"), "session.session_id"),
            target_output=_text(data.get("target_output"), "session.target_output"),
            current_section=_enum(data.get("current_section"), SupplementSessionSection, "session.current_section"),
            provenance=SupplementSessionProvenance.from_dict(data.get("provenance"), "session.provenance"),
            supersedes_supplement_id=_optional_id(data.get("supersedes_supplement_id"), "session.supersedes_supplement_id"),
            created_at=_iso_datetime(data.get("created_at"), "session.created_at"),
            updated_at=_iso_datetime(data.get("updated_at"), "session.updated_at"),
            contact_fields=tuple(ContactField.from_dict(item, f"session.contact_fields[{index}]") for index, item in enumerate(data["contact_fields"])),
            structural_selections=tuple(ProfileStructuralSelection.from_dict(item, f"session.structural_selections[{index}]") for index, item in enumerate(data["structural_selections"])),
            inclusion_reviews=tuple(SupplementInclusionReview.from_dict(item, f"session.inclusion_reviews[{index}]") for index, item in enumerate(data["inclusion_reviews"])),
        )
        if _parsed_datetime(result.updated_at) < _parsed_datetime(result.created_at):
            raise Phase2ValidationError("session updated_at cannot precede created_at")
        for values, attribute, label in (
            (result.contact_fields, "contact_field_id", "contact fields"),
            (result.structural_selections, "selection_id", "structural selections"),
            (result.inclusion_reviews, "review_id", "reviews"),
        ):
            ids = tuple(getattr(item, attribute) for item in values)
            if ids != tuple(sorted(set(ids))):
                raise Phase2ValidationError(f"session {label} must be unique and sorted")
        if result.session_id != generate_resume_profile_supplement_session_id(result):
            raise Phase2ValidationError("Resume Profile Supplement session ID is not deterministic")
        return result

    def validate(
        self, *, target_output: str | Path, profile: CareerProfile,
        resume_material: ResumeMaterialArtifact,
        resume_wording_review: ResumeWordingReviewArtifact,
        superseded_supplement: ResumeProfileSupplementArtifact | None = None,
        **context: Any,
    ) -> None:
        canonical = ResumeProfileSupplementSession.from_dict(self.to_dict())
        if canonical != self:
            raise Phase2ValidationError("Resume Profile Supplement session is not canonical")
        expected_target = str(Path(target_output).resolve())
        if self.target_output != expected_target:
            raise Phase2ValidationError("Resume Profile Supplement session targets a different output")
        expected_provenance = SupplementSessionProvenance.capture(
            profile, resume_material, resume_wording_review
        )
        if self.provenance != expected_provenance:
            raise Phase2ValidationError("Resume Profile Supplement session provenance is stale")
        expected_supersedes = (
            None if superseded_supplement is None else superseded_supplement.supplement_id
        )
        if self.supersedes_supplement_id != expected_supersedes:
            raise Phase2ValidationError("Resume Profile Supplement session revision source is stale")
        build_resume_profile_supplement(
            profile=profile, resume_material=resume_material,
            resume_wording_review=resume_wording_review,
            contact_fields=self.contact_fields,
            structural_selections=self.structural_selections,
            inclusion_reviews=self.inclusion_reviews,
            created_at=self.created_at,
            updated_at=self.updated_at,
            superseded_supplement=superseded_supplement,
            **context,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "schema_version": self.schema_version,
            "session_id": self.session_id, "target_output": self.target_output,
            "current_section": self.current_section.value,
            "provenance": self.provenance.to_dict(),
            "supersedes_supplement_id": self.supersedes_supplement_id,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "contact_fields": [item.to_dict() for item in self.contact_fields],
            "structural_selections": [item.to_dict() for item in self.structural_selections],
            "inclusion_reviews": [item.to_dict() for item in self.inclusion_reviews],
        }


def generate_resume_profile_supplement_session_id(
    value: ResumeProfileSupplementSession,
) -> str:
    data = value.to_dict()
    data.pop("session_id")
    return _stable_hash(RESUME_PROFILE_SUPPLEMENT_SESSION_ID_VERSION, data)


def build_resume_profile_supplement_session(
    *, target_output: str | Path, current_section: SupplementSessionSection,
    profile: CareerProfile, resume_material: ResumeMaterialArtifact,
    resume_wording_review: ResumeWordingReviewArtifact,
    contact_fields: Sequence[ContactField],
    structural_selections: Sequence[ProfileStructuralSelection],
    inclusion_reviews: Sequence[SupplementInclusionReview], updated_at: str,
    superseded_supplement: ResumeProfileSupplementArtifact | None = None,
    previous_session: ResumeProfileSupplementSession | None = None,
    **context: Any,
) -> ResumeProfileSupplementSession:
    created_at = updated_at if previous_session is None else previous_session.created_at
    provisional = ResumeProfileSupplementSession(
        "resume_profile_supplement_session_pending", str(Path(target_output).resolve()),
        current_section,
        SupplementSessionProvenance.capture(profile, resume_material, resume_wording_review),
        None if superseded_supplement is None else superseded_supplement.supplement_id,
        created_at, updated_at,
        tuple(sorted(contact_fields, key=lambda item: item.contact_field_id)),
        tuple(sorted(structural_selections, key=lambda item: item.selection_id)),
        tuple(sorted(inclusion_reviews, key=lambda item: item.review_id)),
    )
    result = ResumeProfileSupplementSession.from_dict({
        **provisional.to_dict(),
        "session_id": generate_resume_profile_supplement_session_id(provisional),
    })
    result.validate(
        target_output=target_output, profile=profile,
        resume_material=resume_material,
        resume_wording_review=resume_wording_review,
        superseded_supplement=superseded_supplement, **context,
    )
    return result


def save_resume_profile_supplement_session(
    value: ResumeProfileSupplementSession, path: str | Path, **context: Any,
) -> Path:
    value.validate(**context)
    return save_phase2_json(
        ResumeProfileSupplementSession.from_dict(value.to_dict()).to_dict(), path
    )


def load_resume_profile_supplement_session(
    path: str | Path, **context: Any,
) -> ResumeProfileSupplementSession:
    value = ResumeProfileSupplementSession.from_dict(load_phase2_json(path))
    value.validate(**context)
    return value
