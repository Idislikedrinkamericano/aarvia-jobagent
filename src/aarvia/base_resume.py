"""Deterministic, role-neutral Base Resume contracts and Markdown rendering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Sequence

from .career_direction import profile_fingerprint
from .phase2_storage import load_phase2_json, save_phase2_json
from .profile import CareerProfile
from .resume_material import ResumeMaterialArtifact, ResumeSectionType
from .resume_profile_supplement import (
    ContactFieldType,
    ProfileStructuralSelection,
    ResumeProfileSupplementArtifact,
    StructuralRecordType,
    select_confirmed_resume_profile_supplement,
)
from .resume_wording import (
    ResumeWordingReviewArtifact,
    WordingCandidateType,
    select_confirmed_resume_wording,
)
from .role_catalog import (
    Phase2ValidationError,
    _enum,
    _iso_datetime,
    _mapping,
    _reject_unknown,
    _stable_id,
    _string_tuple,
    _text,
)


BASE_RESUME_SCHEMA = "aarvia.base_resume"
BASE_RESUME_SCHEMA_VERSION = 1
BASE_RESUME_ID_VERSION = "base-resume-v1"


class BaseResumeItemType(str, Enum):
    CONTACT = "contact"
    EDUCATION = "education"
    EXPERIENCE = "experience"
    PROJECT = "project"
    SKILL = "skill"


class BaseResumeTemporalState(str, Enum):
    COMPLETED = "completed"
    ONGOING = "ongoing"
    UNSPECIFIED = "unspecified"


class BaseResumeOmissionReason(str, Enum):
    USER_EXCLUDED = "user_excluded"


_SECTION_ORDER = (
    ResumeSectionType.EDUCATION,
    ResumeSectionType.EXPERIENCE,
    ResumeSectionType.PROJECTS,
    ResumeSectionType.SKILLS,
)


def _stable_hash(prefix: str, value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _optional_id(value: Any, path: str) -> str | None:
    return None if value is None else _stable_id(value, path)


def _optional_text(value: Any, path: str) -> str | None:
    return None if value is None else _text(value, path)


def _pairs(value: Any, path: str) -> tuple[tuple[str, str], ...]:
    data = _mapping(value, path)
    result = tuple(sorted((_text(key, f"{path} key"), _text(item, f"{path}.{key}")) for key, item in data.items()))
    if not result:
        raise Phase2ValidationError(f"{path} cannot be empty")
    return result


def _count(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise Phase2ValidationError(f"{path} must be a non-negative integer")
    return value


@dataclass(frozen=True, eq=True)
class BaseResumeBullet:
    bullet_id: str
    subject_id: str
    source_record_id: str
    wording_candidate_id: str
    wording_review_id: str
    material_ids: tuple[str, ...]
    evidence_item_ids: tuple[str, ...]
    enrichment_claim_ids: tuple[str, ...]
    text: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> "BaseResumeBullet":
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        result = cls(
            _stable_id(data.get("bullet_id"), f"{path}.bullet_id"),
            _stable_id(data.get("subject_id"), f"{path}.subject_id"),
            _stable_id(data.get("source_record_id"), f"{path}.source_record_id"),
            _stable_id(data.get("wording_candidate_id"), f"{path}.wording_candidate_id"),
            _stable_id(data.get("wording_review_id"), f"{path}.wording_review_id"),
            _string_tuple(data.get("material_ids"), f"{path}.material_ids"),
            _string_tuple(data.get("evidence_item_ids"), f"{path}.evidence_item_ids"),
            _string_tuple(data.get("enrichment_claim_ids"), f"{path}.enrichment_claim_ids"),
            _text(data.get("text"), f"{path}.text"),
        )
        for name in ("material_ids", "evidence_item_ids", "enrichment_claim_ids"):
            items = getattr(result, name)
            if items != tuple(sorted(set(items))):
                raise Phase2ValidationError(f"{path}.{name} must be unique and sorted")
        if not result.material_ids or not result.evidence_item_ids:
            raise Phase2ValidationError(f"{path} must retain material and evidence provenance")
        if result.bullet_id != generate_base_resume_bullet_id(result):
            raise Phase2ValidationError(f"{path}.bullet_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "bullet_id": self.bullet_id, "subject_id": self.subject_id,
            "source_record_id": self.source_record_id,
            "wording_candidate_id": self.wording_candidate_id,
            "wording_review_id": self.wording_review_id,
            "material_ids": list(self.material_ids),
            "evidence_item_ids": list(self.evidence_item_ids),
            "enrichment_claim_ids": list(self.enrichment_claim_ids), "text": self.text,
        }


@dataclass(frozen=True, eq=True)
class BaseResumeEntry:
    entry_id: str
    subject_id: str | None
    item_type: BaseResumeItemType
    source_record_id: str | None
    supplement_subject_id: str | None
    supplement_review_id: str | None
    wording_candidate_id: str | None
    wording_review_id: str | None
    material_ids: tuple[str, ...]
    evidence_item_ids: tuple[str, ...]
    enrichment_claim_ids: tuple[str, ...]
    profile_path: str | None
    fields: tuple[tuple[str, str], ...]
    bullet_ids: tuple[str, ...]
    temporal_state: BaseResumeTemporalState
    skill_group: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> "BaseResumeEntry":
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        raw_fields = data.get("fields")
        result = cls(
            _stable_id(data.get("entry_id"), f"{path}.entry_id"),
            _optional_id(data.get("subject_id"), f"{path}.subject_id"),
            _enum(data.get("item_type"), BaseResumeItemType, f"{path}.item_type"),
            _optional_id(data.get("source_record_id"), f"{path}.source_record_id"),
            _optional_id(data.get("supplement_subject_id"), f"{path}.supplement_subject_id"),
            _optional_id(data.get("supplement_review_id"), f"{path}.supplement_review_id"),
            _optional_id(data.get("wording_candidate_id"), f"{path}.wording_candidate_id"),
            _optional_id(data.get("wording_review_id"), f"{path}.wording_review_id"),
            _string_tuple(data.get("material_ids"), f"{path}.material_ids"),
            _string_tuple(data.get("evidence_item_ids"), f"{path}.evidence_item_ids"),
            _string_tuple(data.get("enrichment_claim_ids"), f"{path}.enrichment_claim_ids"),
            _optional_text(data.get("profile_path"), f"{path}.profile_path"),
            _pairs(raw_fields, f"{path}.fields"),
            _string_tuple(data.get("bullet_ids"), f"{path}.bullet_ids"),
            _enum(data.get("temporal_state"), BaseResumeTemporalState, f"{path}.temporal_state"),
            _optional_text(data.get("skill_group"), f"{path}.skill_group"),
        )
        if result.bullet_ids != tuple(dict.fromkeys(result.bullet_ids)):
            raise Phase2ValidationError(f"{path}.bullet_ids must be unique")
        for name in ("material_ids", "evidence_item_ids", "enrichment_claim_ids"):
            items = getattr(result, name)
            if items != tuple(sorted(set(items))):
                raise Phase2ValidationError(f"{path}.{name} must be unique and sorted")
        if result.item_type in {BaseResumeItemType.CONTACT, BaseResumeItemType.EDUCATION, BaseResumeItemType.SKILL} and result.bullet_ids:
            raise Phase2ValidationError(f"{path} structural entries cannot contain bullets")
        if result.item_type == BaseResumeItemType.SKILL and result.skill_group is None:
            raise Phase2ValidationError(f"{path} Skill entry requires a group")
        if result.item_type != BaseResumeItemType.SKILL and result.skill_group is not None:
            raise Phase2ValidationError(f"{path} non-Skill entry cannot have a skill group")
        if result.subject_id is None and not result.bullet_ids:
            raise Phase2ValidationError(f"{path} derived entry must contain bullets")
        if result.entry_id != generate_base_resume_entry_id(result):
            raise Phase2ValidationError(f"{path}.entry_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id, "subject_id": self.subject_id,
            "item_type": self.item_type.value, "source_record_id": self.source_record_id,
            "supplement_subject_id": self.supplement_subject_id,
            "supplement_review_id": self.supplement_review_id,
            "wording_candidate_id": self.wording_candidate_id,
            "wording_review_id": self.wording_review_id,
            "material_ids": list(self.material_ids),
            "evidence_item_ids": list(self.evidence_item_ids),
            "enrichment_claim_ids": list(self.enrichment_claim_ids),
            "profile_path": self.profile_path,
            "fields": {key: value for key, value in self.fields},
            "bullet_ids": list(self.bullet_ids),
            "temporal_state": self.temporal_state.value, "skill_group": self.skill_group,
        }


@dataclass(frozen=True, eq=True)
class BaseResumeSection:
    section_type: ResumeSectionType
    entry_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> "BaseResumeSection":
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        result = cls(
            _enum(data.get("section_type"), ResumeSectionType, f"{path}.section_type"),
            _string_tuple(data.get("entry_ids"), f"{path}.entry_ids"),
        )
        if result.entry_ids != tuple(dict.fromkeys(result.entry_ids)):
            raise Phase2ValidationError(f"{path}.entry_ids must be unique")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {"section_type": self.section_type.value, "entry_ids": list(self.entry_ids)}


@dataclass(frozen=True, eq=True)
class BaseResumeOmission:
    omission_id: str
    subject_id: str
    subject_type: str
    reason: BaseResumeOmissionReason

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> "BaseResumeOmission":
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        result = cls(
            _stable_id(data.get("omission_id"), f"{path}.omission_id"),
            _stable_id(data.get("subject_id"), f"{path}.subject_id"),
            _text(data.get("subject_type"), f"{path}.subject_type"),
            _enum(data.get("reason"), BaseResumeOmissionReason, f"{path}.reason"),
        )
        if result.omission_id != generate_base_resume_omission_id(result):
            raise Phase2ValidationError(f"{path}.omission_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {"omission_id": self.omission_id, "subject_id": self.subject_id,
                "subject_type": self.subject_type, "reason": self.reason.value}


@dataclass(frozen=True, eq=True)
class BaseResumeCoverage:
    eligible_subject_count: int
    included_subject_count: int
    omitted_subject_count: int
    contact_count: int
    education_count: int
    experience_count: int
    project_count: int
    skill_count: int
    bullet_count: int
    structural_context_without_bullets_count: int
    partial_coverage: bool

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> "BaseResumeCoverage":
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        kwargs: dict[str, Any] = {}
        for name in cls.__dataclass_fields__:
            raw = data.get(name)
            if name == "partial_coverage":
                if not isinstance(raw, bool):
                    raise Phase2ValidationError(f"{path}.{name} must be a boolean")
                kwargs[name] = raw
            else:
                kwargs[name] = _count(raw, f"{path}.{name}")
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, eq=True)
class BaseResumeArtifact:
    base_resume_id: str
    profile_fingerprint: str
    mapping_artifact_id: str
    binding_review_artifact_id: str | None
    allocation_review_artifact_id: str | None
    recommendation_set_id: str
    decision_id: str
    gap_analysis_id: str
    evidence_bank_id: str
    enrichment_artifact_id: str
    resume_material_artifact_id: str
    resume_wording_review_id: str
    supplement_id: str
    rubric_version: str
    rubric_fingerprint: str
    catalog_version: str
    role_neutral: bool
    supersedes_base_resume_id: str | None
    created_at: str
    updated_at: str
    entries: tuple[BaseResumeEntry, ...]
    bullets: tuple[BaseResumeBullet, ...]
    sections: tuple[BaseResumeSection, ...]
    omissions: tuple[BaseResumeOmission, ...]
    coverage: BaseResumeCoverage
    schema: str = BASE_RESUME_SCHEMA
    schema_version: int = BASE_RESUME_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BaseResumeArtifact":
        data = _mapping(value, "base_resume")
        _reject_unknown(data, set(cls.__dataclass_fields__), "base_resume")
        if data.get("schema") != BASE_RESUME_SCHEMA or data.get("schema_version") != 1:
            raise Phase2ValidationError("unsupported Base Resume schema")
        for name in ("entries", "bullets", "sections", "omissions"):
            if not isinstance(data.get(name), list):
                raise Phase2ValidationError(f"base_resume.{name} must be a list")
        result = cls(
            _stable_id(data.get("base_resume_id"), "base_resume.base_resume_id"),
            _text(data.get("profile_fingerprint"), "base_resume.profile_fingerprint"),
            _stable_id(data.get("mapping_artifact_id"), "base_resume.mapping_artifact_id"),
            _optional_id(data.get("binding_review_artifact_id"), "base_resume.binding_review_artifact_id"),
            _optional_id(data.get("allocation_review_artifact_id"), "base_resume.allocation_review_artifact_id"),
            _stable_id(data.get("recommendation_set_id"), "base_resume.recommendation_set_id"),
            _stable_id(data.get("decision_id"), "base_resume.decision_id"),
            _stable_id(data.get("gap_analysis_id"), "base_resume.gap_analysis_id"),
            _stable_id(data.get("evidence_bank_id"), "base_resume.evidence_bank_id"),
            _stable_id(data.get("enrichment_artifact_id"), "base_resume.enrichment_artifact_id"),
            _stable_id(data.get("resume_material_artifact_id"), "base_resume.resume_material_artifact_id"),
            _stable_id(data.get("resume_wording_review_id"), "base_resume.resume_wording_review_id"),
            _stable_id(data.get("supplement_id"), "base_resume.supplement_id"),
            _text(data.get("rubric_version"), "base_resume.rubric_version"),
            _text(data.get("rubric_fingerprint"), "base_resume.rubric_fingerprint"),
            _text(data.get("catalog_version"), "base_resume.catalog_version"),
            data.get("role_neutral"),
            _optional_id(data.get("supersedes_base_resume_id"), "base_resume.supersedes_base_resume_id"),
            _iso_datetime(data.get("created_at"), "base_resume.created_at"),
            _iso_datetime(data.get("updated_at"), "base_resume.updated_at"),
            tuple(BaseResumeEntry.from_dict(item, f"entries[{i}]") for i, item in enumerate(data["entries"])),
            tuple(BaseResumeBullet.from_dict(item, f"bullets[{i}]") for i, item in enumerate(data["bullets"])),
            tuple(BaseResumeSection.from_dict(item, f"sections[{i}]") for i, item in enumerate(data["sections"])),
            tuple(BaseResumeOmission.from_dict(item, f"omissions[{i}]") for i, item in enumerate(data["omissions"])),
            BaseResumeCoverage.from_dict(data.get("coverage"), "base_resume.coverage"),
        )
        result._validate_shape()
        if result.base_resume_id != generate_base_resume_id(result):
            raise Phase2ValidationError("Base Resume ID is not deterministic")
        return result

    def _validate_shape(self) -> None:
        if self.role_neutral is not True:
            raise Phase2ValidationError("Base Resume must be role-neutral")
        if datetime.fromisoformat(self.updated_at.replace("Z", "+00:00")) < datetime.fromisoformat(self.created_at.replace("Z", "+00:00")):
            raise Phase2ValidationError("Base Resume updated_at cannot precede created_at")
        for values, attr, label in ((self.entries, "entry_id", "entries"), (self.bullets, "bullet_id", "bullets"), (self.omissions, "omission_id", "omissions")):
            ids = [getattr(item, attr) for item in values]
            if len(ids) != len(set(ids)):
                raise Phase2ValidationError(f"Base Resume {label} must be unique")
        section_types = tuple(item.section_type for item in self.sections)
        if len(section_types) != len(set(section_types)):
            raise Phase2ValidationError("Base Resume sections must be unique")
        entry_ids = {
            item.entry_id for item in self.entries
            if item.item_type != BaseResumeItemType.CONTACT
        }
        listed = [item for section in self.sections for item in section.entry_ids]
        if set(listed) != entry_ids or len(listed) != len(entry_ids):
            raise Phase2ValidationError("each Base Resume entry must appear in exactly one section")
        bullet_ids = {item.bullet_id for item in self.bullets}
        used = [item for entry in self.entries for item in entry.bullet_ids]
        if set(used) != bullet_ids or len(used) != len(bullet_ids):
            raise Phase2ValidationError("each Base Resume bullet must appear in exactly one entry")

    def validate(self, *, profile: CareerProfile, resume_material: ResumeMaterialArtifact,
                 resume_wording_review: ResumeWordingReviewArtifact,
                 resume_profile_supplement: ResumeProfileSupplementArtifact,
                 superseded_base_resume: "BaseResumeArtifact | None" = None,
                 **context: Any) -> None:
        canonical = BaseResumeArtifact.from_dict(self.to_dict())
        if canonical != self:
            raise Phase2ValidationError("Base Resume is not canonical")
        resume_profile_supplement.validate(
            profile=profile, resume_material=resume_material,
            resume_wording_review=resume_wording_review, **context,
        )
        expected = (
            profile_fingerprint(profile), resume_material.mapping_artifact_id,
            resume_material.binding_review_artifact_id,
            resume_material.allocation_review_artifact_id,
            resume_material.recommendation_set_id, resume_material.decision_id,
            resume_material.gap_analysis_id, resume_material.evidence_bank_id,
            resume_material.enrichment_artifact_id, resume_material.resume_material_id,
            resume_wording_review.wording_review_id, resume_profile_supplement.supplement_id,
            resume_material.rubric_version, resume_material.rubric_fingerprint,
            resume_material.catalog_version,
        )
        actual = (
            self.profile_fingerprint, self.mapping_artifact_id,
            self.binding_review_artifact_id, self.allocation_review_artifact_id,
            self.recommendation_set_id, self.decision_id, self.gap_analysis_id,
            self.evidence_bank_id, self.enrichment_artifact_id,
            self.resume_material_artifact_id, self.resume_wording_review_id,
            self.supplement_id, self.rubric_version, self.rubric_fingerprint,
            self.catalog_version,
        )
        if actual != expected:
            raise Phase2ValidationError("Base Resume provenance is stale")
        rebuilt = build_base_resume(
            profile=profile, resume_material=resume_material,
            resume_wording_review=resume_wording_review,
            resume_profile_supplement=resume_profile_supplement,
            omitted_subject_ids=[item.subject_id for item in self.omissions],
            section_order=[item.section_type for item in self.sections],
            entry_order={item.section_type: item.entry_ids for item in self.sections},
            bullet_order={item.entry_id: item.bullet_ids for item in self.entries if item.bullet_ids},
            skill_group_order=_skill_group_order(self),
            created_at=self.updated_at,
            superseded_base_resume=superseded_base_resume,
            _created_at_override=self.created_at,
            **context,
        )
        if rebuilt != self:
            raise Phase2ValidationError("Base Resume content, layout, omission, or coverage is not deterministic")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "schema_version": self.schema_version,
            "base_resume_id": self.base_resume_id,
            "profile_fingerprint": self.profile_fingerprint,
            "mapping_artifact_id": self.mapping_artifact_id,
            "binding_review_artifact_id": self.binding_review_artifact_id,
            "allocation_review_artifact_id": self.allocation_review_artifact_id,
            "recommendation_set_id": self.recommendation_set_id,
            "decision_id": self.decision_id, "gap_analysis_id": self.gap_analysis_id,
            "evidence_bank_id": self.evidence_bank_id,
            "enrichment_artifact_id": self.enrichment_artifact_id,
            "resume_material_artifact_id": self.resume_material_artifact_id,
            "resume_wording_review_id": self.resume_wording_review_id,
            "supplement_id": self.supplement_id,
            "rubric_version": self.rubric_version,
            "rubric_fingerprint": self.rubric_fingerprint,
            "catalog_version": self.catalog_version,
            "role_neutral": self.role_neutral,
            "supersedes_base_resume_id": self.supersedes_base_resume_id,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "entries": [item.to_dict() for item in self.entries],
            "bullets": [item.to_dict() for item in self.bullets],
            "sections": [item.to_dict() for item in self.sections],
            "omissions": [item.to_dict() for item in self.omissions],
            "coverage": self.coverage.to_dict(),
        }


def generate_base_resume_bullet_id(value: BaseResumeBullet) -> str:
    data = value.to_dict(); data.pop("bullet_id")
    return _stable_hash("base_resume_bullet", data)


def generate_base_resume_entry_id(value: BaseResumeEntry) -> str:
    data = value.to_dict(); data.pop("entry_id"); data.pop("bullet_ids")
    return _stable_hash("base_resume_entry", data)


def generate_base_resume_omission_id(value: BaseResumeOmission) -> str:
    data = value.to_dict(); data.pop("omission_id")
    return _stable_hash("base_resume_omission", data)


def generate_base_resume_id(value: BaseResumeArtifact) -> str:
    data = value.to_dict(); data.pop("base_resume_id")
    return _stable_hash(BASE_RESUME_ID_VERSION, data)


def _subject(prefix: str, identifier: str) -> str:
    return f"{prefix}_{identifier}"


def _temporal_state(claim_ids: Sequence[str], enrichment: Any) -> BaseResumeTemporalState:
    claims = {item.claim_id: item for item in enrichment.claims}
    values = {claims[item].temporality.value for item in claim_ids if item in claims}
    if "ongoing" in values:
        return BaseResumeTemporalState.ONGOING
    if values and values <= {"completed"}:
        return BaseResumeTemporalState.COMPLETED
    return BaseResumeTemporalState.UNSPECIFIED


def _make_bullet(candidate: Any, review_id: str, materials: Mapping[str, Any]) -> BaseResumeBullet:
    selected = [materials[item] for item in candidate.material_ids]
    provisional = BaseResumeBullet(
        "base_resume_bullet_pending", _subject("wording", candidate.wording_candidate_id),
        candidate.source_record_id, candidate.wording_candidate_id, review_id,
        candidate.material_ids,
        tuple(sorted({value for item in selected for value in item.evidence_item_ids})),
        tuple(sorted({value for item in selected for value in item.enrichment_claim_ids})),
        candidate.wording_text,
    )
    return BaseResumeBullet.from_dict({**provisional.to_dict(), "bullet_id": generate_base_resume_bullet_id(provisional)}, "bullet")


def _make_entry(**kwargs: Any) -> BaseResumeEntry:
    provisional = BaseResumeEntry(entry_id="base_resume_entry_pending", **kwargs)
    return BaseResumeEntry.from_dict({**provisional.to_dict(), "entry_id": generate_base_resume_entry_id(provisional)}, "entry")


def _selection_entry(selection: ProfileStructuralSelection, review_id: str) -> BaseResumeEntry:
    snapshot = selection.profile_reference.value_snapshot
    fields = tuple(sorted((name, str(snapshot[name])) for name in selection.selected_fields))
    item_type = {
        StructuralRecordType.EDUCATION: BaseResumeItemType.EDUCATION,
        StructuralRecordType.SKILL: BaseResumeItemType.SKILL,
        StructuralRecordType.EXPERIENCE: BaseResumeItemType.EXPERIENCE,
        StructuralRecordType.PROJECT: BaseResumeItemType.PROJECT,
    }[selection.record_type]
    return _make_entry(
        subject_id=_subject("supplement", selection.selection_id), item_type=item_type,
        source_record_id=None, supplement_subject_id=selection.selection_id,
        supplement_review_id=review_id, profile_path=selection.profile_reference.path,
        wording_candidate_id=None, wording_review_id=None,
        material_ids=(), evidence_item_ids=(), enrichment_claim_ids=(),
        fields=fields, bullet_ids=(), temporal_state=BaseResumeTemporalState.UNSPECIFIED,
        skill_group=(
            str(snapshot.get("category", snapshot.get("skill_category", "Other")))
            if item_type == BaseResumeItemType.SKILL else None
        ),
    )


def _contact_entry(contact: Any, review_id: str) -> BaseResumeEntry:
    return _make_entry(
        subject_id=_subject("contact", contact.contact_field_id), item_type=BaseResumeItemType.CONTACT,
        source_record_id=None, supplement_subject_id=contact.contact_field_id,
        supplement_review_id=review_id, profile_path=None,
        wording_candidate_id=None, wording_review_id=None,
        material_ids=(), evidence_item_ids=(), enrichment_claim_ids=(),
        fields=(("field_type", contact.field_type.value), ("value", contact.value)),
        bullet_ids=(), temporal_state=BaseResumeTemporalState.UNSPECIFIED, skill_group=None,
    )


def _source_entry(
    source: Any,
    bullet_ids: Sequence[str],
    temporal: BaseResumeTemporalState,
    section_type: ResumeSectionType,
) -> BaseResumeEntry:
    kind = {
        ResumeSectionType.EXPERIENCE: BaseResumeItemType.EXPERIENCE,
        ResumeSectionType.PROJECTS: BaseResumeItemType.PROJECT,
    }.get(section_type)
    if kind is None:
        raise Phase2ValidationError(
            "a reviewed bullet must belong to Experience or Projects"
        )
    return _make_entry(
        subject_id=None, item_type=kind, source_record_id=source.source_record_id,
        supplement_subject_id=None, supplement_review_id=None,
        wording_candidate_id=None, wording_review_id=None,
        material_ids=(), evidence_item_ids=(), enrichment_claim_ids=(),
        profile_path=source.canonical_profile_path, fields=source.identity,
        bullet_ids=tuple(bullet_ids), temporal_state=temporal, skill_group=None,
    )


def _wording_skill_entry(candidate: Any, review_id: str, materials: Mapping[str, Any], source: Any) -> BaseResumeEntry:
    selected = [materials[item] for item in candidate.material_ids]
    identity = dict(source.identity)
    group = identity.get("skill_category", identity.get("category", "Other"))
    return _make_entry(
        subject_id=_subject("wording", candidate.wording_candidate_id),
        item_type=BaseResumeItemType.SKILL, source_record_id=source.source_record_id,
        supplement_subject_id=None, supplement_review_id=None,
        wording_candidate_id=candidate.wording_candidate_id,
        wording_review_id=review_id,
        material_ids=candidate.material_ids,
        evidence_item_ids=tuple(sorted({value for item in selected for value in item.evidence_item_ids})),
        enrichment_claim_ids=tuple(sorted({value for item in selected for value in item.enrichment_claim_ids})),
        profile_path=source.canonical_profile_path,
        fields=(("text", candidate.wording_text),), bullet_ids=(),
        temporal_state=BaseResumeTemporalState.UNSPECIFIED, skill_group=group,
    )


def _eligible_subjects(wording: ResumeWordingReviewArtifact, supplement: ResumeProfileSupplementArtifact, **context: Any) -> dict[str, str]:
    wording_context = {
        key: value for key, value in context.items()
        if key not in {"superseded_supplement"}
    }
    candidates = select_confirmed_resume_wording(wording, **wording_context)
    contacts, selections = select_confirmed_resume_profile_supplement(supplement, resume_wording_review=wording, **context)
    result = {_subject("wording", item.wording_candidate_id): "wording_candidate" for item in candidates}
    result.update({_subject("contact", item.contact_field_id): "contact_field" for item in contacts})
    result.update({_subject("supplement", item.selection_id): "structural_selection" for item in selections})
    return result


def _coverage(entries: Sequence[BaseResumeEntry], bullets: Sequence[BaseResumeBullet], eligible: int, omitted: int, supplement: ResumeProfileSupplementArtifact) -> BaseResumeCoverage:
    count = lambda kind: sum(item.item_type == kind for item in entries)
    return BaseResumeCoverage(
        eligible, eligible - omitted, omitted,
        count(BaseResumeItemType.CONTACT), count(BaseResumeItemType.EDUCATION),
        count(BaseResumeItemType.EXPERIENCE), count(BaseResumeItemType.PROJECT),
        count(BaseResumeItemType.SKILL), len(bullets),
        sum(item.item_type in {BaseResumeItemType.EXPERIENCE, BaseResumeItemType.PROJECT} and not item.bullet_ids for item in entries),
        supplement.coverage.partial_coverage,
    )


def _skill_group_order(value: BaseResumeArtifact) -> tuple[str, ...]:
    entries = {item.entry_id: item for item in value.entries}
    section = next((item for item in value.sections if item.section_type == ResumeSectionType.SKILLS), None)
    if section is None:
        return ()
    return tuple(dict.fromkeys(entries[item].skill_group for item in section.entry_ids if entries[item].skill_group is not None))


def build_base_resume(*, profile: CareerProfile, resume_material: ResumeMaterialArtifact,
                      resume_wording_review: ResumeWordingReviewArtifact,
                      resume_profile_supplement: ResumeProfileSupplementArtifact,
                      omitted_subject_ids: Sequence[str] = (),
                      section_order: Sequence[ResumeSectionType] | None = None,
                      entry_order: Mapping[ResumeSectionType, Sequence[str]] | None = None,
                      bullet_order: Mapping[str, Sequence[str]] | None = None,
                      skill_group_order: Sequence[str] | None = None,
                      created_at: str = "1970-01-01T00:00:00+00:00",
                      superseded_base_resume: BaseResumeArtifact | None = None,
                      _created_at_override: str | None = None, **context: Any) -> BaseResumeArtifact:
    resume_profile_supplement.validate(
        profile=profile, resume_material=resume_material,
        resume_wording_review=resume_wording_review, **context,
    )
    eligible = _eligible_subjects(resume_wording_review, resume_profile_supplement,
                                  profile=profile, resume_material=resume_material, **context)
    raw_omitted = tuple(omitted_subject_ids)
    omitted = tuple(sorted(set(raw_omitted)))
    if len(omitted) != len(raw_omitted) or not set(omitted) <= set(eligible):
        raise Phase2ValidationError("Base Resume omissions must be unique eligible subjects")
    material_by_id = {item.material_id: item for item in resume_material.materials}
    source_by_id = {item.source_record_id: item for item in context["evidence_bank"].sources}
    wording_reviews = {item.wording_candidate_id: item.review_id for item in resume_wording_review.reviews}
    supplement_reviews = {item.subject_id: item.review_id for item in resume_profile_supplement.inclusion_reviews}
    wording_context = {
        key: value for key, value in context.items()
        if key not in {"superseded_supplement"}
    }
    candidates = select_confirmed_resume_wording(
        resume_wording_review, profile=profile, resume_material=resume_material,
        **wording_context,
    )
    candidates_by_id = {item.wording_candidate_id: item for item in candidates}
    contacts, selections = select_confirmed_resume_profile_supplement(
        resume_profile_supplement, profile=profile, resume_material=resume_material,
        resume_wording_review=resume_wording_review, **context,
    )
    bullets = [
        _make_bullet(item, wording_reviews[item.wording_candidate_id], material_by_id)
        for item in candidates
        if item.candidate_type == WordingCandidateType.BULLET
        and _subject("wording", item.wording_candidate_id) not in omitted
    ]
    bullets_by_source: dict[str, list[BaseResumeBullet]] = {}
    for item in bullets:
        bullets_by_source.setdefault(item.source_record_id, []).append(item)
    entries: list[BaseResumeEntry] = []
    for source_id, values in sorted(bullets_by_source.items()):
        default_ids = tuple(sorted(item.bullet_id for item in values))
        state = _temporal_state(
            tuple(sorted({claim for item in values for claim in item.enrichment_claim_ids})),
            context["evidence_enrichment"],
        )
        source = source_by_id[source_id]
        section_types = {
            candidates_by_id[item.wording_candidate_id].section_type
            for item in values
        }
        if len(section_types) != 1:
            raise Phase2ValidationError(
                "reviewed bullets for one Source Record must use one section"
            )
        section_type = next(iter(section_types))
        draft = _source_entry(source, default_ids, state, section_type)
        requested = None if bullet_order is None else bullet_order.get(draft.entry_id)
        if requested is not None:
            requested = tuple(requested)
            if set(requested) != set(default_ids) or len(requested) != len(default_ids):
                raise Phase2ValidationError("Base Resume bullet order must contain every entry bullet once")
            draft = _source_entry(source, requested, state, section_type)
        entries.append(draft)
    for item in candidates:
        if item.candidate_type == WordingCandidateType.STRUCTURAL_ENTRY and _subject("wording", item.wording_candidate_id) not in omitted:
            entries.append(_wording_skill_entry(item, wording_reviews[item.wording_candidate_id], material_by_id, source_by_id[item.source_record_id]))
    entries.extend(_contact_entry(item, supplement_reviews[item.contact_field_id]) for item in contacts if _subject("contact", item.contact_field_id) not in omitted)
    entries.extend(_selection_entry(item, supplement_reviews[item.selection_id]) for item in selections if _subject("supplement", item.selection_id) not in omitted)
    entry_by_id = {item.entry_id: item for item in entries}
    default_sections: dict[ResumeSectionType, list[str]] = {item: [] for item in _SECTION_ORDER}
    for item in entries:
        if item.item_type == BaseResumeItemType.CONTACT:
            continue
        section = {
            BaseResumeItemType.EDUCATION: ResumeSectionType.EDUCATION,
            BaseResumeItemType.EXPERIENCE: ResumeSectionType.EXPERIENCE,
            BaseResumeItemType.PROJECT: ResumeSectionType.PROJECTS,
            BaseResumeItemType.SKILL: ResumeSectionType.SKILLS,
        }[item.item_type]
        default_sections[section].append(item.entry_id)
    for values in default_sections.values():
        values.sort()
    if skill_group_order is not None:
        ranks = {name: index for index, name in enumerate(skill_group_order)}
        groups = {entry_by_id[item].skill_group for item in default_sections[ResumeSectionType.SKILLS]}
        if set(ranks) != groups or len(ranks) != len(tuple(skill_group_order)):
            raise Phase2ValidationError("skill group order must contain every skill group once")
        default_sections[ResumeSectionType.SKILLS].sort(key=lambda item: (ranks[entry_by_id[item].skill_group], item))
    if entry_order:
        for section, requested_values in entry_order.items():
            requested = tuple(requested_values)
            if set(requested) != set(default_sections[section]) or len(requested) != len(default_sections[section]):
                raise Phase2ValidationError("Base Resume entry order must contain every section entry once")
            default_sections[section] = list(requested)
    order = tuple(section_order or _SECTION_ORDER)
    if set(order) != set(_SECTION_ORDER) or len(order) != len(_SECTION_ORDER):
        raise Phase2ValidationError("Base Resume section order must contain each section once")
    sections = tuple(BaseResumeSection(item, tuple(default_sections[item])) for item in order)
    omission_records = tuple(
        BaseResumeOmission.from_dict({
            "omission_id": generate_base_resume_omission_id(BaseResumeOmission("pending", item, eligible[item], BaseResumeOmissionReason.USER_EXCLUDED)),
            "subject_id": item, "subject_type": eligible[item], "reason": "user_excluded",
        }, "omission") for item in omitted
    )
    body_subjects = {item.subject_id for item in entries if item.subject_id} | {item.subject_id for item in bullets}
    if body_subjects & set(omitted) or body_subjects | set(omitted) != set(eligible):
        raise Phase2ValidationError("every eligible subject must appear once in body or omissions")
    entry_tuple = tuple(sorted(entries, key=lambda item: item.entry_id))
    bullet_tuple = tuple(sorted(bullets, key=lambda item: item.bullet_id))
    timestamp = _created_at_override or (created_at if superseded_base_resume is None else superseded_base_resume.created_at)
    provisional = BaseResumeArtifact(
        "base_resume_pending", profile_fingerprint(profile), resume_material.mapping_artifact_id,
        resume_material.binding_review_artifact_id,
        resume_material.allocation_review_artifact_id,
        resume_material.recommendation_set_id, resume_material.decision_id,
        resume_material.gap_analysis_id, resume_material.evidence_bank_id,
        resume_material.enrichment_artifact_id, resume_material.resume_material_id,
        resume_wording_review.wording_review_id, resume_profile_supplement.supplement_id,
        resume_material.rubric_version, resume_material.rubric_fingerprint,
        resume_material.catalog_version,
        True, None if superseded_base_resume is None else superseded_base_resume.base_resume_id,
        timestamp, created_at, entry_tuple, bullet_tuple, sections, omission_records,
        _coverage(entry_tuple, bullet_tuple, len(eligible), len(omitted), resume_profile_supplement),
    )
    result = BaseResumeArtifact.from_dict({**provisional.to_dict(), "base_resume_id": generate_base_resume_id(provisional)})
    if superseded_base_resume is None:
        if result.supersedes_base_resume_id is not None:
            raise Phase2ValidationError("unexpected Base Resume predecessor")
    else:
        superseded_base_resume.validate(
            profile=profile, resume_material=resume_material,
            resume_wording_review=resume_wording_review,
            resume_profile_supplement=resume_profile_supplement, **context,
        )
        if result.created_at != superseded_base_resume.created_at or datetime.fromisoformat(result.updated_at.replace("Z", "+00:00")) <= datetime.fromisoformat(superseded_base_resume.updated_at.replace("Z", "+00:00")):
            raise Phase2ValidationError("Base Resume revision time or lineage is invalid")
    return result


def save_base_resume(value: BaseResumeArtifact, path: str | Path, **context: Any) -> Path:
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"Base Resume artifacts are immutable: {target}")
    value.validate(**context)
    return save_phase2_json(value.to_dict(), target)


def load_base_resume(path: str | Path, **context: Any) -> BaseResumeArtifact:
    value = BaseResumeArtifact.from_dict(load_phase2_json(path))
    value.validate(**context)
    return value


_MONTHS = ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MD_ESCAPE = re.compile(r"([\\`*_[\]<>#|])")


def _escape(value: str) -> str:
    return _MD_ESCAPE.sub(
        r"\\\1", value.replace("\r\n", "\n").replace("\r", "\n")
    )


def _date(value: str, *, expected: bool = False) -> str:
    match = re.fullmatch(r"(\d{4})-(\d{2})(?:-\d{2})?", value)
    if not match:
        return _escape(value)
    year, month = int(match.group(1)), int(match.group(2))
    rendered = f"{_MONTHS[month]} {year}" if 1 <= month <= 12 else str(year)
    return f"Expected {rendered}" if expected else rendered


def render_base_resume_markdown(value: BaseResumeArtifact, **context: Any) -> str:
    value.validate(**context)
    entries = {item.entry_id: item for item in value.entries}
    bullets = {item.bullet_id: item for item in value.bullets}
    contacts = [item for item in value.entries if item.item_type == BaseResumeItemType.CONTACT]
    contact_values = {dict(item.fields)["field_type"]: dict(item.fields)["value"] for item in contacts}
    lines: list[str] = []
    name = contact_values.get(ContactFieldType.NAME.value)
    if name:
        lines.extend((f"# {_escape(name)}", ""))
    contact_parts: list[str] = []
    for field in (ContactFieldType.EMAIL, ContactFieldType.PHONE):
        if field.value in contact_values:
            contact_parts.append(_escape(contact_values[field.value]))
    for field, label in ((ContactFieldType.GITHUB_URL, "GitHub"), (ContactFieldType.LINKEDIN_URL, "LinkedIn"), (ContactFieldType.PORTFOLIO_URL, "Portfolio")):
        url = contact_values.get(field.value)
        if url:
            if not re.fullmatch(r"https://[^\s<>]+", url):
                raise Phase2ValidationError("Base Resume contains an unsafe contact URL")
            contact_parts.append(f"[{label}](<{url}>)")
    if contact_parts:
        lines.extend((" | ".join(contact_parts), ""))
    titles = {ResumeSectionType.EDUCATION: "Education", ResumeSectionType.EXPERIENCE: "Experience", ResumeSectionType.PROJECTS: "Projects", ResumeSectionType.SKILLS: "Skills"}
    for section in value.sections:
        if not section.entry_ids:
            continue
        lines.extend((f"## {titles[section.section_type]}", ""))
        if section.section_type == ResumeSectionType.SKILLS:
            grouped: dict[str, list[str]] = {}
            for item_id in section.entry_ids:
                item = entries[item_id]; fields = dict(item.fields)
                text = fields.get("text") or fields.get("skill_name") or fields.get("name")
                if text:
                    grouped.setdefault(item.skill_group or "Other", []).append(text)
            for group, values in grouped.items():
                lines.append(f"**{_escape(group)}:** {', '.join(_escape(item) for item in values)}")
            lines.append("")
            continue
        for item_id in section.entry_ids:
            item = entries[item_id]; fields = dict(item.fields)
            if item.item_type == BaseResumeItemType.PROJECT:
                title = fields.get("organization_or_project_name") or fields.get("title_or_role")
                organization = fields.get("title_or_role")
            elif item.item_type == BaseResumeItemType.EXPERIENCE:
                title = fields.get("title_or_role") or fields.get("organization_or_project_name")
                organization = fields.get("organization_or_project_name")
            else:
                title = fields.get("degree") or fields.get("field_of_study") or fields.get("institution")
                organization = fields.get("institution")
            if not title:
                raise Phase2ValidationError("Base Resume entry lacks a renderable title")
            heading = f"**{_escape(title)}**"
            if organization and organization != title:
                heading += f", {_escape(organization)}"
            lines.append(heading)
            if item.item_type == BaseResumeItemType.EDUCATION:
                if fields.get("field_of_study") and fields["field_of_study"] != title:
                    lines.append(_escape(fields["field_of_study"]))
                if fields.get("gpa"):
                    lines.append(f"GPA: {_escape(fields['gpa'])}")
            dates: list[str] = []
            if fields.get("start_date"):
                dates.append(_date(fields["start_date"]))
            if item.temporal_state == BaseResumeTemporalState.ONGOING:
                dates.append("Present")
            elif fields.get("end_date"):
                dates.append(_date(fields["end_date"]))
            elif fields.get("expected_graduation_date"):
                dates.append(_date(fields["expected_graduation_date"], expected=True))
            if dates:
                lines.append(" - ".join(dates))
            context_text = fields.get("short_factual_summary") or fields.get("description")
            if context_text:
                lines.append(f"Context: {_escape(context_text)}")
            for bullet_id in item.bullet_ids:
                lines.append(f"- {_escape(bullets[bullet_id].text)}")
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def save_base_resume_markdown(value: BaseResumeArtifact, path: str | Path, **context: Any) -> tuple[Path, str]:
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"Base Resume Markdown already exists: {target}")
    payload = render_base_resume_markdown(value, **context).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False) as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno()); temporary = Path(handle.name)
        os.replace(temporary, target)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return target, digest
