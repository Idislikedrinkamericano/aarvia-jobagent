"""Private, user-reviewed structural supplements for a future Base Resume."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from .career_direction import ProfileReference, profile_fingerprint
from .phase2_storage import load_phase2_json, save_phase2_json
from .profile import CareerProfile
from .resume_material import ResumeMaterialArtifact
from .resume_wording import ResumeWordingReviewArtifact
from .role_catalog import (
    Phase2ValidationError, _enum, _iso_datetime, _mapping, _reject_unknown,
    _stable_id, _string_tuple, _text,
)


RESUME_PROFILE_SUPPLEMENT_SCHEMA = "aarvia.resume_profile_supplement"
RESUME_PROFILE_SUPPLEMENT_SCHEMA_VERSION = 1
RESUME_PROFILE_SUPPLEMENT_ID_VERSION = "resume-profile-supplement-v1"


class ContactFieldType(str, Enum):
    NAME = "name"
    EMAIL = "email"
    PHONE = "phone"
    GITHUB_URL = "github_url"
    LINKEDIN_URL = "linkedin_url"
    PORTFOLIO_URL = "portfolio_url"


class ContactOrigin(str, Enum):
    USER_PROVIDED = "user_provided"


class StructuralRecordType(str, Enum):
    EDUCATION = "education"
    SKILL = "skill"
    EXPERIENCE = "experience"
    PROJECT = "project"


class StructuralUse(str, Enum):
    EDUCATION_ENTRY = "education_entry"
    SKILL_ENTRY = "skill_entry"
    EXPERIENCE_CONTEXT = "experience_context"
    PROJECT_CONTEXT = "project_context"


class SupplementReviewDecision(str, Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class SupplementReviewerType(str, Enum):
    PROFILE_OWNER = "profile_owner"


class SupplementSubjectType(str, Enum):
    CONTACT_FIELD = "contact_field"
    STRUCTURAL_SELECTION = "structural_selection"


class CoverageState(str, Enum):
    ALREADY_COVERED_BY_MATERIAL = "already_covered_by_material"
    CONFIRMED_IN_SUPPLEMENT = "confirmed_in_supplement"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    UNRESOLVED = "unresolved"
    MISSING_CONTACT = "missing_contact"


_EDUCATION_FIELDS = (
    "degree", "expected_graduation_date", "field_of_study", "gpa",
    "institution", "start_date",
)
_SKILL_FIELDS = ("category", "self_reported_proficiency", "skill_name")
_EXPERIENCE_FIELDS = (
    "end_date", "experience_type", "organization_or_project_name",
    "short_factual_summary", "start_date", "title_or_role",
)
_REQUIRED_CONTACT_FIELDS = {
    ContactFieldType.NAME, ContactFieldType.EMAIL, ContactFieldType.PHONE,
}


def _stable_hash(prefix: str, value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _parsed_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _optional_id(value: Any, path: str) -> str | None:
    return None if value is None else _stable_id(value, path)


def _count(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise Phase2ValidationError(f"{path} must be a non-negative integer")
    return value


def _bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise Phase2ValidationError(f"{path} must be boolean")
    return value


def _contact_value(value: Any, field_type: ContactFieldType, path: str) -> str:
    result = _text(value, path)
    assert result is not None
    if result != result.strip() or "\x00" in result:
        raise Phase2ValidationError(f"{path} must be trimmed valid text")
    if field_type == ContactFieldType.EMAIL:
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", result):
            raise Phase2ValidationError(f"{path} must be a valid email address")
    elif field_type == ContactFieldType.PHONE:
        if not re.fullmatch(r"[+()0-9.\-\s]+", result) or not 7 <= len(re.sub(r"\D", "", result)) <= 15:
            raise Phase2ValidationError(f"{path} must be a valid phone number")
    elif field_type in {
        ContactFieldType.GITHUB_URL, ContactFieldType.LINKEDIN_URL,
        ContactFieldType.PORTFOLIO_URL,
    }:
        parsed = urlparse(result)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise Phase2ValidationError(f"{path} must be an HTTPS URL")
        host = (parsed.hostname or "").casefold()
        if field_type == ContactFieldType.GITHUB_URL and host not in {"github.com", "www.github.com"}:
            raise Phase2ValidationError(f"{path} must use github.com")
        if field_type == ContactFieldType.LINKEDIN_URL and host not in {"linkedin.com", "www.linkedin.com"}:
            raise Phase2ValidationError(f"{path} must use linkedin.com")
    return result


@dataclass(frozen=True, eq=True)
class ContactField:
    contact_field_id: str
    field_type: ContactFieldType
    value: str
    origin: ContactOrigin = ContactOrigin.USER_PROVIDED

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "contact_field") -> ContactField:
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        field_type = _enum(data.get("field_type"), ContactFieldType, f"{path}.field_type")
        result = cls(
            contact_field_id=_stable_id(data.get("contact_field_id"), f"{path}.contact_field_id"),
            field_type=field_type,
            value=_contact_value(data.get("value"), field_type, f"{path}.value"),
            origin=_enum(data.get("origin"), ContactOrigin, f"{path}.origin"),
        )
        if result.contact_field_id != generate_contact_field_id(result):
            raise Phase2ValidationError(f"{path}.contact_field_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "contact_field_id": self.contact_field_id, "field_type": self.field_type.value,
            "value": self.value, "origin": self.origin.value,
        }


@dataclass(frozen=True, eq=True)
class ProfileStructuralSelection:
    selection_id: str
    record_type: StructuralRecordType
    structural_use: StructuralUse
    profile_reference: ProfileReference
    selected_fields: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "structural_selection") -> ProfileStructuralSelection:
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        result = cls(
            selection_id=_stable_id(data.get("selection_id"), f"{path}.selection_id"),
            record_type=_enum(data.get("record_type"), StructuralRecordType, f"{path}.record_type"),
            structural_use=_enum(data.get("structural_use"), StructuralUse, f"{path}.structural_use"),
            profile_reference=ProfileReference.from_dict(data.get("profile_reference"), f"{path}.profile_reference"),
            selected_fields=_string_tuple(data.get("selected_fields"), f"{path}.selected_fields"),
        )
        if not result.selected_fields or result.selected_fields != tuple(sorted(set(result.selected_fields))):
            raise Phase2ValidationError(f"{path}.selected_fields must be non-empty, unique, and sorted")
        if result.selection_id != generate_structural_selection_id(result):
            raise Phase2ValidationError(f"{path}.selection_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "selection_id": self.selection_id, "record_type": self.record_type.value,
            "structural_use": self.structural_use.value,
            "profile_reference": self.profile_reference.to_dict(),
            "selected_fields": list(self.selected_fields),
        }


@dataclass(frozen=True, eq=True)
class SupplementInclusionReview:
    review_id: str
    subject_type: SupplementSubjectType
    subject_id: str
    decision: SupplementReviewDecision
    reviewer_type: SupplementReviewerType
    reviewed_at: str
    reason: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "supplement_review") -> SupplementInclusionReview:
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        reason = data.get("reason")
        result = cls(
            review_id=_stable_id(data.get("review_id"), f"{path}.review_id"),
            subject_type=_enum(data.get("subject_type"), SupplementSubjectType, f"{path}.subject_type"),
            subject_id=_stable_id(data.get("subject_id"), f"{path}.subject_id"),
            decision=_enum(data.get("decision"), SupplementReviewDecision, f"{path}.decision"),
            reviewer_type=_enum(data.get("reviewer_type"), SupplementReviewerType, f"{path}.reviewer_type"),
            reviewed_at=_iso_datetime(data.get("reviewed_at"), f"{path}.reviewed_at"),
            reason=None if reason is None else _text(reason, f"{path}.reason"),
        )
        if result.review_id != generate_supplement_review_id(result):
            raise Phase2ValidationError(f"{path}.review_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id, "subject_type": self.subject_type.value,
            "subject_id": self.subject_id, "decision": self.decision.value,
            "reviewer_type": self.reviewer_type.value, "reviewed_at": self.reviewed_at,
            "reason": self.reason,
        }


@dataclass(frozen=True, eq=True)
class SupplementCoverageSummary:
    contact_total: int
    contact_confirmed: int
    contact_rejected: int
    contact_deferred: int
    contact_missing: int
    education_total: int
    experience_total: int
    skill_total: int
    already_covered_by_material: int
    confirmed_in_supplement: int
    rejected: int
    deferred: int
    unresolved: int
    partial_coverage: bool

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> SupplementCoverageSummary:
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        kwargs = {
            name: (_bool(data.get(name), f"{path}.{name}") if name == "partial_coverage"
                   else _count(data.get(name), f"{path}.{name}"))
            for name in cls.__dataclass_fields__
        }
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, eq=True)
class ResumeProfileSupplementSummary:
    contact_field_count: int
    structural_selection_count: int
    confirmed_count: int
    rejected_count: int
    deferred_count: int

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> ResumeProfileSupplementSummary:
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        return cls(**{name: _count(data.get(name), f"{path}.{name}") for name in cls.__dataclass_fields__})

    def to_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, eq=True)
class ResumeProfileSupplementArtifact:
    supplement_id: str
    profile_fingerprint: str
    resume_material_artifact_id: str
    resume_material_schema_version: int
    resume_wording_review_id: str
    resume_wording_schema_version: int
    supersedes_supplement_id: str | None
    created_at: str
    updated_at: str
    contact_fields: tuple[ContactField, ...]
    structural_selections: tuple[ProfileStructuralSelection, ...]
    inclusion_reviews: tuple[SupplementInclusionReview, ...]
    coverage: SupplementCoverageSummary
    summary: ResumeProfileSupplementSummary
    schema: str = RESUME_PROFILE_SUPPLEMENT_SCHEMA
    schema_version: int = RESUME_PROFILE_SUPPLEMENT_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ResumeProfileSupplementArtifact:
        data = _mapping(value, "resume_profile_supplement")
        _reject_unknown(data, set(cls.__dataclass_fields__), "resume_profile_supplement")
        if data.get("schema") != RESUME_PROFILE_SUPPLEMENT_SCHEMA or data.get("schema_version") != 1:
            raise Phase2ValidationError("unsupported Resume Profile Supplement schema")
        for name in ("contact_fields", "structural_selections", "inclusion_reviews"):
            if not isinstance(data.get(name), list):
                raise Phase2ValidationError(f"resume_profile_supplement.{name} must be a list")
        result = cls(
            supplement_id=_stable_id(data.get("supplement_id"), "resume_profile_supplement.supplement_id"),
            profile_fingerprint=_text(data.get("profile_fingerprint"), "resume_profile_supplement.profile_fingerprint"),
            resume_material_artifact_id=_stable_id(data.get("resume_material_artifact_id"), "resume_profile_supplement.resume_material_artifact_id"),
            resume_material_schema_version=data.get("resume_material_schema_version"),
            resume_wording_review_id=_stable_id(data.get("resume_wording_review_id"), "resume_profile_supplement.resume_wording_review_id"),
            resume_wording_schema_version=data.get("resume_wording_schema_version"),
            supersedes_supplement_id=_optional_id(data.get("supersedes_supplement_id"), "resume_profile_supplement.supersedes_supplement_id"),
            created_at=_iso_datetime(data.get("created_at"), "resume_profile_supplement.created_at"),
            updated_at=_iso_datetime(data.get("updated_at"), "resume_profile_supplement.updated_at"),
            contact_fields=tuple(ContactField.from_dict(item, f"contact_fields[{i}]") for i, item in enumerate(data["contact_fields"])),
            structural_selections=tuple(ProfileStructuralSelection.from_dict(item, f"structural_selections[{i}]") for i, item in enumerate(data["structural_selections"])),
            inclusion_reviews=tuple(SupplementInclusionReview.from_dict(item, f"inclusion_reviews[{i}]") for i, item in enumerate(data["inclusion_reviews"])),
            coverage=SupplementCoverageSummary.from_dict(data.get("coverage"), "resume_profile_supplement.coverage"),
            summary=ResumeProfileSupplementSummary.from_dict(data.get("summary"), "resume_profile_supplement.summary"),
        )
        result._validate_shape()
        if result.supplement_id != generate_resume_profile_supplement_id(result):
            raise Phase2ValidationError("Resume Profile Supplement ID is not deterministic")
        return result

    def _validate_shape(self) -> None:
        if self.resume_material_schema_version != 1 or self.resume_wording_schema_version != 1:
            raise Phase2ValidationError("Resume Profile Supplement requires Material and Wording schema 1")
        if _parsed_datetime(self.updated_at) < _parsed_datetime(self.created_at):
            raise Phase2ValidationError("Supplement updated_at cannot precede created_at")
        for values, label, attr in (
            (self.contact_fields, "contact fields", "contact_field_id"),
            (self.structural_selections, "structural selections", "selection_id"),
            (self.inclusion_reviews, "inclusion reviews", "review_id"),
        ):
            ids = tuple(getattr(item, attr) for item in values)
            if ids != tuple(sorted(set(ids))):
                raise Phase2ValidationError(f"Supplement {label} must be unique and sorted")
        types = tuple(item.field_type for item in self.contact_fields)
        if len(types) != len(set(types)):
            raise Phase2ValidationError("each Contact field type may appear only once")
        subjects = {
            **{item.contact_field_id: SupplementSubjectType.CONTACT_FIELD for item in self.contact_fields},
            **{item.selection_id: SupplementSubjectType.STRUCTURAL_SELECTION for item in self.structural_selections},
        }
        reviewed = [item.subject_id for item in self.inclusion_reviews]
        if len(reviewed) != len(set(reviewed)) or set(reviewed) != set(subjects):
            raise Phase2ValidationError("each Supplement subject must have exactly one Review")
        if any(subjects[item.subject_id] != item.subject_type for item in self.inclusion_reviews):
            raise Phase2ValidationError("Supplement Review subject type is inconsistent")
        created = _parsed_datetime(self.created_at)
        updated = _parsed_datetime(self.updated_at)
        if any(not created <= _parsed_datetime(item.reviewed_at) <= updated for item in self.inclusion_reviews):
            raise Phase2ValidationError("Supplement Review timestamp must fall within the artifact revision")
        if self.summary != _summary(self.contact_fields, self.structural_selections, self.inclusion_reviews):
            raise Phase2ValidationError("Supplement summary is not deterministic")

    def validate(self, *, profile: CareerProfile, resume_material: ResumeMaterialArtifact,
                 resume_wording_review: ResumeWordingReviewArtifact,
                 superseded_supplement: ResumeProfileSupplementArtifact | None = None,
                 **context: Any) -> None:
        canonical = ResumeProfileSupplementArtifact.from_dict(self.to_dict())
        if canonical != self:
            raise Phase2ValidationError("Resume Profile Supplement is not canonical")
        resume_wording_review.validate(
            profile=profile, resume_material=resume_material, **context
        )
        if self.profile_fingerprint != profile_fingerprint(profile):
            raise Phase2ValidationError("Resume Profile Supplement Profile is stale")
        if self.resume_material_artifact_id != resume_material.resume_material_id:
            raise Phase2ValidationError("Resume Profile Supplement Material is stale")
        if self.resume_wording_review_id != resume_wording_review.wording_review_id:
            raise Phase2ValidationError("Resume Profile Supplement Wording is stale")
        _validate_selections(self, profile, resume_material, context["evidence_bank"])
        expected_coverage = _coverage(profile, resume_material, context["evidence_bank"], self.contact_fields, self.structural_selections, self.inclusion_reviews)
        if self.coverage != expected_coverage:
            raise Phase2ValidationError("Resume Profile Supplement coverage is not deterministic")
        _validate_revision(self, superseded_supplement)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "schema_version": self.schema_version,
            "supplement_id": self.supplement_id,
            "profile_fingerprint": self.profile_fingerprint,
            "resume_material_artifact_id": self.resume_material_artifact_id,
            "resume_material_schema_version": self.resume_material_schema_version,
            "resume_wording_review_id": self.resume_wording_review_id,
            "resume_wording_schema_version": self.resume_wording_schema_version,
            "supersedes_supplement_id": self.supersedes_supplement_id,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "contact_fields": [item.to_dict() for item in self.contact_fields],
            "structural_selections": [item.to_dict() for item in self.structural_selections],
            "inclusion_reviews": [item.to_dict() for item in self.inclusion_reviews],
            "coverage": self.coverage.to_dict(), "summary": self.summary.to_dict(),
        }


def generate_contact_field_id(value: ContactField) -> str:
    data = value.to_dict(); data.pop("contact_field_id")
    return _stable_hash("contact_field", data)


def generate_structural_selection_id(value: ProfileStructuralSelection) -> str:
    data = value.to_dict(); data.pop("selection_id")
    return _stable_hash("structural_selection", data)


def generate_supplement_review_id(value: SupplementInclusionReview) -> str:
    data = value.to_dict(); data.pop("review_id")
    return _stable_hash("supplement_review", data)


def generate_resume_profile_supplement_id(value: ResumeProfileSupplementArtifact) -> str:
    data = value.to_dict(); data.pop("supplement_id")
    return _stable_hash(RESUME_PROFILE_SUPPLEMENT_ID_VERSION, data)


def create_contact_field(field_type: ContactFieldType, value: str) -> ContactField:
    provisional = ContactField("contact_field_pending", field_type, value)
    return ContactField.from_dict({**provisional.to_dict(), "contact_field_id": generate_contact_field_id(provisional)})


def _record_type_and_use(profile: CareerProfile, path: str) -> tuple[StructuralRecordType, StructuralUse, tuple[str, ...]]:
    match = re.fullmatch(r"(education|skills|experience_overview)\[(\d+)\]", path)
    if not match:
        raise Phase2ValidationError("structural selection must reference a whole Profile record")
    collection, raw_index = match.groups(); index = int(raw_index)
    if collection == "education":
        if index >= len(profile.education): raise Phase2ValidationError("Education index is out of range")
        return StructuralRecordType.EDUCATION, StructuralUse.EDUCATION_ENTRY, _EDUCATION_FIELDS
    if collection == "skills":
        if index >= len(profile.skills): raise Phase2ValidationError("Skill index is out of range")
        return StructuralRecordType.SKILL, StructuralUse.SKILL_ENTRY, _SKILL_FIELDS
    if index >= len(profile.experience_overview): raise Phase2ValidationError("Experience index is out of range")
    is_project = profile.experience_overview[index].experience_type.casefold() == "project"
    return ((StructuralRecordType.PROJECT, StructuralUse.PROJECT_CONTEXT) if is_project
            else (StructuralRecordType.EXPERIENCE, StructuralUse.EXPERIENCE_CONTEXT)) + (_EXPERIENCE_FIELDS,)


def create_structural_selection(profile: CareerProfile, path: str, selected_fields: Sequence[str]) -> ProfileStructuralSelection:
    record_type, structural_use, allowed = _record_type_and_use(profile, path)
    fields = tuple(sorted(set(selected_fields)))
    if not fields or any(item not in allowed for item in fields):
        raise Phase2ValidationError("selection contains unsupported Profile fields")
    snapshot = ProfileReference.capture(profile, path).value_snapshot
    if any(snapshot.get(item) is None for item in fields):
        raise Phase2ValidationError("selection cannot display an absent Profile value")
    provisional = ProfileStructuralSelection(
        "structural_selection_pending", record_type, structural_use,
        ProfileReference.capture(profile, path), fields,
    )
    return ProfileStructuralSelection.from_dict({**provisional.to_dict(), "selection_id": generate_structural_selection_id(provisional)})


def create_supplement_review(subject: ContactField | ProfileStructuralSelection, *, decision: SupplementReviewDecision,
                             reviewed_at: str, reason: str | None = None) -> SupplementInclusionReview:
    subject_type = (SupplementSubjectType.CONTACT_FIELD if isinstance(subject, ContactField)
                    else SupplementSubjectType.STRUCTURAL_SELECTION)
    subject_id = subject.contact_field_id if isinstance(subject, ContactField) else subject.selection_id
    provisional = SupplementInclusionReview(
        "supplement_review_pending", subject_type, subject_id, decision,
        SupplementReviewerType.PROFILE_OWNER, reviewed_at, reason,
    )
    return SupplementInclusionReview.from_dict({**provisional.to_dict(), "review_id": generate_supplement_review_id(provisional)})


def _covered_paths(resume_material: ResumeMaterialArtifact, evidence_bank: Any) -> set[str]:
    source_ids = {item.source_record_id for item in resume_material.materials}
    return {item.canonical_profile_path for item in evidence_bank.sources if item.source_record_id in source_ids}


def _all_paths(profile: CareerProfile) -> tuple[str, ...]:
    return tuple(
        [f"education[{i}]" for i in range(len(profile.education))]
        + [f"experience_overview[{i}]" for i in range(len(profile.experience_overview))]
        + [f"skills[{i}]" for i in range(len(profile.skills))]
    )


def _validate_selections(value: ResumeProfileSupplementArtifact, profile: CareerProfile,
                         resume_material: ResumeMaterialArtifact, evidence_bank: Any) -> None:
    covered = _covered_paths(resume_material, evidence_bank)
    paths: list[str] = []
    for selection in value.structural_selections:
        selection.profile_reference.validate(profile)
        expected_type, expected_use, allowed = _record_type_and_use(profile, selection.profile_reference.path)
        if selection.record_type != expected_type or selection.structural_use != expected_use:
            raise Phase2ValidationError("structural selection type does not match its Profile record")
        if any(item not in allowed for item in selection.selected_fields):
            raise Phase2ValidationError("structural selection contains unsupported fields")
        snapshot = selection.profile_reference.value_snapshot
        if any(snapshot.get(item) is None for item in selection.selected_fields):
            raise Phase2ValidationError("structural selection references an absent Profile value")
        if selection.profile_reference.path in covered:
            raise Phase2ValidationError("Resume Material already covers this Profile record")
        paths.append(selection.profile_reference.path)
    if len(paths) != len(set(paths)):
        raise Phase2ValidationError("each Profile record may have only one structural selection")


def _summary(contacts: Sequence[ContactField], selections: Sequence[ProfileStructuralSelection],
             reviews: Sequence[SupplementInclusionReview]) -> ResumeProfileSupplementSummary:
    decisions = [item.decision for item in reviews]
    return ResumeProfileSupplementSummary(
        len(contacts), len(selections),
        decisions.count(SupplementReviewDecision.CONFIRMED),
        decisions.count(SupplementReviewDecision.REJECTED),
        decisions.count(SupplementReviewDecision.DEFERRED),
    )


def _coverage(profile: CareerProfile, resume_material: ResumeMaterialArtifact, evidence_bank: Any,
              contacts: Sequence[ContactField], selections: Sequence[ProfileStructuralSelection],
              reviews: Sequence[SupplementInclusionReview]) -> SupplementCoverageSummary:
    decisions = {item.subject_id: item.decision for item in reviews}
    covered = _covered_paths(resume_material, evidence_bank)
    selected = {item.profile_reference.path: decisions[item.selection_id] for item in selections}
    record_states = []
    for path in _all_paths(profile):
        if path in covered: record_states.append(CoverageState.ALREADY_COVERED_BY_MATERIAL)
        elif path not in selected: record_states.append(CoverageState.UNRESOLVED)
        else:
            record_states.append({
                SupplementReviewDecision.CONFIRMED: CoverageState.CONFIRMED_IN_SUPPLEMENT,
                SupplementReviewDecision.REJECTED: CoverageState.REJECTED,
                SupplementReviewDecision.DEFERRED: CoverageState.DEFERRED,
            }[selected[path]])
    contact_decisions = {item.field_type: decisions[item.contact_field_id] for item in contacts}
    contact_states = [contact_decisions.get(item) for item in ContactFieldType]
    result = SupplementCoverageSummary(
        contact_total=len(ContactFieldType),
        contact_confirmed=contact_states.count(SupplementReviewDecision.CONFIRMED),
        contact_rejected=contact_states.count(SupplementReviewDecision.REJECTED),
        contact_deferred=contact_states.count(SupplementReviewDecision.DEFERRED),
        contact_missing=contact_states.count(None),
        education_total=len(profile.education), experience_total=len(profile.experience_overview),
        skill_total=len(profile.skills),
        already_covered_by_material=record_states.count(CoverageState.ALREADY_COVERED_BY_MATERIAL),
        confirmed_in_supplement=record_states.count(CoverageState.CONFIRMED_IN_SUPPLEMENT),
        rejected=record_states.count(CoverageState.REJECTED),
        deferred=record_states.count(CoverageState.DEFERRED),
        unresolved=record_states.count(CoverageState.UNRESOLVED),
        partial_coverage=False,
    )
    return SupplementCoverageSummary(**{
        **result.to_dict(),
        "partial_coverage": bool(
            any(contact_decisions.get(item) != SupplementReviewDecision.CONFIRMED
                for item in _REQUIRED_CONTACT_FIELDS)
            or result.unresolved or result.deferred
        ),
    })


def _validate_revision(value: ResumeProfileSupplementArtifact,
                       previous: ResumeProfileSupplementArtifact | None) -> None:
    if value.supersedes_supplement_id is None:
        if previous is not None: raise Phase2ValidationError("Supplement revision source was not referenced")
        return
    if previous is None: raise Phase2ValidationError("Supplement revision requires its superseded artifact")
    if value.supersedes_supplement_id != previous.supplement_id:
        raise Phase2ValidationError("Supplement revision references the wrong artifact")
    if value.created_at != previous.created_at or _parsed_datetime(value.updated_at) <= _parsed_datetime(previous.updated_at):
        raise Phase2ValidationError("Supplement revision timestamps are invalid")
    for name in ("profile_fingerprint", "resume_material_artifact_id", "resume_wording_review_id"):
        if getattr(value, name) != getattr(previous, name):
            raise Phase2ValidationError("Supplement revision provenance is stale")


def build_resume_profile_supplement(*, profile: CareerProfile, resume_material: ResumeMaterialArtifact,
                                    resume_wording_review: ResumeWordingReviewArtifact,
                                    contact_fields: Sequence[ContactField],
                                    structural_selections: Sequence[ProfileStructuralSelection],
                                    inclusion_reviews: Sequence[SupplementInclusionReview], created_at: str,
                                    superseded_supplement: ResumeProfileSupplementArtifact | None = None,
                                    **context: Any) -> ResumeProfileSupplementArtifact:
    resume_wording_review.validate(
        profile=profile, resume_material=resume_material, **context
    )
    contacts = tuple(sorted(contact_fields, key=lambda item: item.contact_field_id))
    selections = tuple(sorted(structural_selections, key=lambda item: item.selection_id))
    reviews = tuple(sorted(inclusion_reviews, key=lambda item: item.review_id))
    timestamp = created_at if superseded_supplement is None else superseded_supplement.created_at
    provisional = ResumeProfileSupplementArtifact(
        "resume_profile_supplement_pending", profile_fingerprint(profile), resume_material.resume_material_id, 1,
        resume_wording_review.wording_review_id, 1,
        None if superseded_supplement is None else superseded_supplement.supplement_id,
        timestamp, created_at, contacts, selections, reviews,
        _coverage(profile, resume_material, context["evidence_bank"], contacts, selections, reviews),
        _summary(contacts, selections, reviews),
    )
    result = ResumeProfileSupplementArtifact.from_dict({
        **provisional.to_dict(), "supplement_id": generate_resume_profile_supplement_id(provisional),
    })
    result.validate(profile=profile, resume_material=resume_material,
                    resume_wording_review=resume_wording_review,
                    superseded_supplement=superseded_supplement, **context)
    return result


def save_resume_profile_supplement(value: ResumeProfileSupplementArtifact, path: str | Path, **context: Any) -> Path:
    target = Path(path)
    if target.exists(): raise FileExistsError(f"Resume Profile Supplement artifacts are immutable: {target}")
    value.validate(**context)
    return save_phase2_json(ResumeProfileSupplementArtifact.from_dict(value.to_dict()).to_dict(), target)


def load_resume_profile_supplement(path: str | Path, **context: Any) -> ResumeProfileSupplementArtifact:
    value = ResumeProfileSupplementArtifact.from_dict(load_phase2_json(path))
    value.validate(**context)
    return value


def load_resume_profile_supplement_revision_source(path: str | Path, *, profile: CareerProfile,
                                                    resume_material: ResumeMaterialArtifact,
                                                    resume_wording_review: ResumeWordingReviewArtifact,
                                                    **context: Any) -> ResumeProfileSupplementArtifact:
    value = ResumeProfileSupplementArtifact.from_dict(load_phase2_json(path))
    resume_wording_review.validate(
        profile=profile, resume_material=resume_material, **context
    )
    if value.profile_fingerprint != profile_fingerprint(profile) or value.resume_material_artifact_id != resume_material.resume_material_id or value.resume_wording_review_id != resume_wording_review.wording_review_id:
        raise Phase2ValidationError("superseded Resume Profile Supplement provenance is stale")
    _validate_selections(value, profile, resume_material, context["evidence_bank"])
    if value.coverage != _coverage(profile, resume_material, context["evidence_bank"], value.contact_fields, value.structural_selections, value.inclusion_reviews):
        raise Phase2ValidationError("superseded Supplement coverage is invalid")
    return value


def select_confirmed_resume_profile_supplement(value: ResumeProfileSupplementArtifact, **context: Any) -> tuple[tuple[ContactField, ...], tuple[ProfileStructuralSelection, ...]]:
    value.validate(**context)
    decisions = {item.subject_id: item.decision for item in value.inclusion_reviews}
    return (
        tuple(item for item in value.contact_fields if decisions[item.contact_field_id] == SupplementReviewDecision.CONFIRMED),
        tuple(item for item in value.structural_selections if decisions[item.selection_id] == SupplementReviewDecision.CONFIRMED),
    )
