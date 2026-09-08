"""Auditable, human-confirmed Role Family assignments for canonical jobs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

from .jd_sources import (
    CaptureScope,
    JDSourceCollectionV3,
    content_sha256,
    normalize_jd_content,
)
from .role_catalog import (
    CatalogType,
    Phase2ValidationError,
    RoleCatalog,
    _enum,
    _iso_datetime,
    _mapping,
    _reject_unknown,
    _stable_id,
    _text,
    _version,
)


ROLE_ASSIGNMENT_SCHEMA = "aarvia.role_assignments"
ROLE_ASSIGNMENT_SCHEMA_VERSION = 1
ASSIGNMENT_ID_VERSION = "role-assignment-v1"
ASSIGNMENT_REVIEW_ID_VERSION = "role-assignment-review-v1"


class RoleAssignmentStatus(str, Enum):
    CURRENT = "current"
    SUPERSEDED = "superseded"


class RoleAssignmentReviewAction(str, Enum):
    CONFIRM_INITIAL = "confirm_initial"
    RECLASSIFY = "reclassify"


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True, eq=True)
class RoleEvidenceReference:
    source_id: str
    capture_id: str
    content_hash: str
    start_offset: int
    end_offset: int
    exact_value_snapshot: str

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], path: str = "role_evidence"
    ) -> RoleEvidenceReference:
        data = _mapping(value, path)
        _reject_unknown(
            data,
            {
                "source_id", "capture_id", "content_hash", "start_offset",
                "end_offset", "exact_value_snapshot",
            },
            path,
        )
        start = data.get("start_offset")
        end = data.get("end_offset")
        if not isinstance(start, int) or isinstance(start, bool) or start < 0:
            raise Phase2ValidationError(f"{path}.start_offset must be a non-negative integer")
        if not isinstance(end, int) or isinstance(end, bool) or end <= start:
            raise Phase2ValidationError(f"{path}.end_offset must follow start_offset")
        return cls(
            source_id=_stable_id(data.get("source_id"), f"{path}.source_id"),
            capture_id=_stable_id(data.get("capture_id"), f"{path}.capture_id"),
            content_hash=_text(data.get("content_hash"), f"{path}.content_hash"),
            start_offset=start,
            end_offset=end,
            exact_value_snapshot=_text(
                data.get("exact_value_snapshot"), f"{path}.exact_value_snapshot"
            ),
        )

    def validate(
        self,
        *,
        canonical_job_id: str,
        sources: JDSourceCollectionV3,
        capture_contents: Mapping[str, str],
    ) -> None:
        canonical = RoleEvidenceReference.from_dict(self.to_dict())
        if canonical != self:
            raise Phase2ValidationError("role evidence reference is not canonical")
        source = sources.source(self.source_id)
        if source.canonical_job_id != canonical_job_id:
            raise Phase2ValidationError("role evidence represents another canonical job")
        capture = sources.capture(self.capture_id)
        if capture.source_reference != self.source_id:
            raise Phase2ValidationError("role evidence capture belongs to another source")
        if capture.content_hash != self.content_hash:
            raise Phase2ValidationError("role evidence content hash does not match capture")
        if capture.capture_scope not in {
            CaptureScope.PARTIAL_EXCERPT,
            CaptureScope.FULL_JOB_DESCRIPTION,
        }:
            raise Phase2ValidationError(
                "role evidence requires a captured JD excerpt or full description"
            )
        if not capture.contains_offsets(self.start_offset, self.end_offset):
            raise Phase2ValidationError("role evidence offsets are outside capture")
        if self.capture_id not in capture_contents:
            raise Phase2ValidationError(
                f"role evidence requires normalized content for capture {self.capture_id}"
            )
        raw_content = capture_contents[self.capture_id]
        normalized = normalize_jd_content(
            raw_content, version=capture.hash_normalization_version
        )
        if content_sha256(raw_content, version=capture.hash_normalization_version) != capture.content_hash:
            raise Phase2ValidationError("role evidence capture content hash is invalid")
        if capture.content_length is not None and len(normalized) != capture.content_length:
            raise Phase2ValidationError("role evidence capture content length is invalid")
        if normalized[self.start_offset:self.end_offset] != self.exact_value_snapshot:
            raise Phase2ValidationError("role evidence snapshot does not match captured content")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "capture_id": self.capture_id,
            "content_hash": self.content_hash,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "exact_value_snapshot": self.exact_value_snapshot,
        }


def _evidence_key(item: RoleEvidenceReference) -> tuple[Any, ...]:
    return (
        item.source_id,
        item.capture_id,
        item.content_hash,
        item.start_offset,
        item.end_offset,
        item.exact_value_snapshot,
    )


def generate_role_assignment_id(
    *,
    canonical_job_id: str,
    source_id: str,
    role_id: str,
    specialization_id: str | None,
    previous_assignment_reference: str | None,
    reviewed_at: str,
    evidence_references: Sequence[RoleEvidenceReference],
) -> str:
    evidence_identity = tuple(
        "|".join(str(value) for value in _evidence_key(item))
        for item in sorted(evidence_references, key=_evidence_key)
    )
    values = (
        ASSIGNMENT_ID_VERSION,
        _stable_id(canonical_job_id, "canonical_job_id"),
        _stable_id(source_id, "source_id"),
        _stable_id(role_id, "role_id"),
        "" if specialization_id is None else _stable_id(specialization_id, "specialization_id"),
        "" if previous_assignment_reference is None else _stable_id(previous_assignment_reference, "previous_assignment_reference"),
        _iso_datetime(reviewed_at, "reviewed_at"),
        *evidence_identity,
    )
    return f"assignment_{hashlib.sha256('|'.join(values).encode('utf-8')).hexdigest()[:24]}"


def generate_role_assignment_review_id(
    *,
    assignment_id: str,
    action: RoleAssignmentReviewAction,
    previous_assignment_reference: str | None,
    old_role_id: str | None,
    old_specialization_id: str | None,
    new_role_id: str,
    new_specialization_id: str | None,
    evidence_references: Sequence[RoleEvidenceReference],
    reviewer_reference: str,
    reviewed_at: str,
    decision_reason: str,
) -> str:
    evidence_identity = tuple(
        "|".join(
            (
                item.source_id,
                item.capture_id,
                item.content_hash,
                str(item.start_offset),
                str(item.end_offset),
                item.exact_value_snapshot,
            )
        )
        for item in sorted(evidence_references, key=_evidence_key)
    )
    values = (
        ASSIGNMENT_REVIEW_ID_VERSION,
        _stable_id(assignment_id, "assignment_id"),
        action.value,
        "" if previous_assignment_reference is None else _stable_id(previous_assignment_reference, "previous_assignment_reference"),
        "" if old_role_id is None else _stable_id(old_role_id, "old_role_id"),
        "" if old_specialization_id is None else _stable_id(old_specialization_id, "old_specialization_id"),
        _stable_id(new_role_id, "new_role_id"),
        "" if new_specialization_id is None else _stable_id(new_specialization_id, "new_specialization_id"),
        *evidence_identity,
        _stable_id(reviewer_reference, "reviewer_reference"),
        _iso_datetime(reviewed_at, "reviewed_at"),
        _text(decision_reason, "decision_reason"),
    )
    return f"assignment_review_{hashlib.sha256('|'.join(values).encode('utf-8')).hexdigest()[:24]}"


@dataclass(frozen=True, eq=True)
class RoleAssignmentRecord:
    assignment_id: str
    canonical_job_id: str
    source_id: str
    role_id: str
    specialization_id: str | None
    status: RoleAssignmentStatus
    previous_assignment_reference: str | None
    evidence_references: tuple[RoleEvidenceReference, ...]
    review_reference: str

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], path: str = "assignment"
    ) -> RoleAssignmentRecord:
        data = _mapping(value, path)
        _reject_unknown(
            data,
            {
                "assignment_id", "canonical_job_id", "source_id", "role_id",
                "specialization_id", "status", "previous_assignment_reference",
                "evidence_references", "review_reference",
            },
            path,
        )
        raw_evidence = data.get("evidence_references")
        if not isinstance(raw_evidence, list) or not raw_evidence:
            raise Phase2ValidationError(f"{path}.evidence_references must be a non-empty list")
        return cls(
            assignment_id=_stable_id(data.get("assignment_id"), f"{path}.assignment_id"),
            canonical_job_id=_stable_id(data.get("canonical_job_id"), f"{path}.canonical_job_id"),
            source_id=_stable_id(data.get("source_id"), f"{path}.source_id"),
            role_id=_stable_id(data.get("role_id"), f"{path}.role_id"),
            specialization_id=(None if data.get("specialization_id") is None else _stable_id(data.get("specialization_id"), f"{path}.specialization_id")),
            status=_enum(data.get("status"), RoleAssignmentStatus, f"{path}.status"),
            previous_assignment_reference=(None if data.get("previous_assignment_reference") is None else _stable_id(data.get("previous_assignment_reference"), f"{path}.previous_assignment_reference")),
            evidence_references=tuple(RoleEvidenceReference.from_dict(item, f"{path}.evidence_references[{index}]") for index, item in enumerate(raw_evidence)),
            review_reference=_stable_id(data.get("review_reference"), f"{path}.review_reference"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignment_id": self.assignment_id,
            "canonical_job_id": self.canonical_job_id,
            "source_id": self.source_id,
            "role_id": self.role_id,
            "specialization_id": self.specialization_id,
            "status": self.status.value,
            "previous_assignment_reference": self.previous_assignment_reference,
            "evidence_references": [item.to_dict() for item in self.evidence_references],
            "review_reference": self.review_reference,
        }


@dataclass(frozen=True, eq=True)
class RoleAssignmentReviewRecord:
    review_id: str
    assignment_id: str
    action: RoleAssignmentReviewAction
    previous_assignment_reference: str | None
    old_role_id: str | None
    old_specialization_id: str | None
    new_role_id: str
    new_specialization_id: str | None
    evidence_references: tuple[RoleEvidenceReference, ...]
    reviewer_reference: str
    reviewed_at: str
    decision_reason: str

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], path: str = "assignment_review"
    ) -> RoleAssignmentReviewRecord:
        data = _mapping(value, path)
        _reject_unknown(
            data,
            {
                "review_id", "assignment_id", "action",
                "previous_assignment_reference", "old_role_id",
                "old_specialization_id", "new_role_id", "new_specialization_id",
                "evidence_references", "reviewer_reference", "reviewed_at",
                "decision_reason",
            },
            path,
        )
        raw_evidence = data.get("evidence_references")
        if not isinstance(raw_evidence, list) or not raw_evidence:
            raise Phase2ValidationError(f"{path}.evidence_references must be a non-empty list")
        result = cls(
            review_id=_stable_id(data.get("review_id"), f"{path}.review_id"),
            assignment_id=_stable_id(data.get("assignment_id"), f"{path}.assignment_id"),
            action=_enum(data.get("action"), RoleAssignmentReviewAction, f"{path}.action"),
            previous_assignment_reference=(None if data.get("previous_assignment_reference") is None else _stable_id(data.get("previous_assignment_reference"), f"{path}.previous_assignment_reference")),
            old_role_id=(None if data.get("old_role_id") is None else _stable_id(data.get("old_role_id"), f"{path}.old_role_id")),
            old_specialization_id=(None if data.get("old_specialization_id") is None else _stable_id(data.get("old_specialization_id"), f"{path}.old_specialization_id")),
            new_role_id=_stable_id(data.get("new_role_id"), f"{path}.new_role_id"),
            new_specialization_id=(None if data.get("new_specialization_id") is None else _stable_id(data.get("new_specialization_id"), f"{path}.new_specialization_id")),
            evidence_references=tuple(RoleEvidenceReference.from_dict(item, f"{path}.evidence_references[{index}]") for index, item in enumerate(raw_evidence)),
            reviewer_reference=_stable_id(data.get("reviewer_reference"), f"{path}.reviewer_reference"),
            reviewed_at=_iso_datetime(data.get("reviewed_at"), f"{path}.reviewed_at"),
            decision_reason=_text(data.get("decision_reason"), f"{path}.decision_reason"),
        )
        expected = generate_role_assignment_review_id(
            assignment_id=result.assignment_id,
            action=result.action,
            previous_assignment_reference=result.previous_assignment_reference,
            old_role_id=result.old_role_id,
            old_specialization_id=result.old_specialization_id,
            new_role_id=result.new_role_id,
            new_specialization_id=result.new_specialization_id,
            evidence_references=result.evidence_references,
            reviewer_reference=result.reviewer_reference,
            reviewed_at=result.reviewed_at,
            decision_reason=result.decision_reason,
        )
        if result.review_id != expected:
            raise Phase2ValidationError(f"{path}.review_id was not generated by Aarvia")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "assignment_id": self.assignment_id,
            "action": self.action.value,
            "previous_assignment_reference": self.previous_assignment_reference,
            "old_role_id": self.old_role_id,
            "old_specialization_id": self.old_specialization_id,
            "new_role_id": self.new_role_id,
            "new_specialization_id": self.new_specialization_id,
            "evidence_references": [item.to_dict() for item in self.evidence_references],
            "reviewer_reference": self.reviewer_reference,
            "reviewed_at": self.reviewed_at,
            "decision_reason": self.decision_reason,
        }


@dataclass(frozen=True, eq=True)
class RoleAssignmentProposal:
    role_id: str
    specialization_id: str | None
    evidence_references: tuple[RoleEvidenceReference, ...]

    @classmethod
    def from_provider(cls, value: Mapping[str, Any]) -> RoleAssignmentProposal:
        data = _mapping(value, "provider_role_assignment")
        _reject_unknown(
            data,
            {"role_id", "specialization_id", "evidence_references"},
            "provider_role_assignment",
        )
        evidence = data.get("evidence_references")
        if not isinstance(evidence, list) or not evidence:
            raise Phase2ValidationError("provider_role_assignment.evidence_references must be a non-empty list")
        return cls(
            role_id=_stable_id(data.get("role_id"), "provider_role_assignment.role_id"),
            specialization_id=(None if data.get("specialization_id") is None else _stable_id(data.get("specialization_id"), "provider_role_assignment.specialization_id")),
            evidence_references=tuple(RoleEvidenceReference.from_dict(item, f"provider_role_assignment.evidence_references[{index}]") for index, item in enumerate(evidence)),
        )


@dataclass(frozen=True, eq=True)
class RoleAssignmentArtifact:
    artifact_id: str
    artifact_type: CatalogType
    source_collection_id: str
    catalog_version: str
    created_at: str
    assignments: tuple[RoleAssignmentRecord, ...]
    review_records: tuple[RoleAssignmentReviewRecord, ...]
    schema: str = ROLE_ASSIGNMENT_SCHEMA
    schema_version: int = ROLE_ASSIGNMENT_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RoleAssignmentArtifact:
        data = _mapping(value, "role_assignments")
        _reject_unknown(
            data,
            {
                "schema", "schema_version", "artifact_id", "artifact_type",
                "source_collection_id", "catalog_version", "created_at",
                "assignments", "review_records",
            },
            "role_assignments",
        )
        if data.get("schema") != ROLE_ASSIGNMENT_SCHEMA or data.get("schema_version") != ROLE_ASSIGNMENT_SCHEMA_VERSION:
            raise Phase2ValidationError("unsupported Role Assignment schema version")
        raw_assignments = data.get("assignments")
        raw_reviews = data.get("review_records")
        if not isinstance(raw_assignments, list) or not isinstance(raw_reviews, list):
            raise Phase2ValidationError("role assignments and review_records must be lists")
        return cls(
            artifact_id=_stable_id(data.get("artifact_id"), "role_assignments.artifact_id"),
            artifact_type=_enum(data.get("artifact_type"), CatalogType, "role_assignments.artifact_type"),
            source_collection_id=_stable_id(data.get("source_collection_id"), "role_assignments.source_collection_id"),
            catalog_version=_version(data.get("catalog_version"), "role_assignments.catalog_version"),
            created_at=_iso_datetime(data.get("created_at"), "role_assignments.created_at"),
            assignments=tuple(RoleAssignmentRecord.from_dict(item, f"role_assignments.assignments[{index}]") for index, item in enumerate(raw_assignments)),
            review_records=tuple(RoleAssignmentReviewRecord.from_dict(item, f"role_assignments.review_records[{index}]") for index, item in enumerate(raw_reviews)),
        )

    def validate(
        self,
        sources: JDSourceCollectionV3,
        catalog: RoleCatalog,
        capture_contents: Mapping[str, str],
    ) -> None:
        sources.validate()
        if RoleAssignmentArtifact.from_dict(self.to_dict()) != self:
            raise Phase2ValidationError("Role Assignment artifact contains non-canonical data")
        if self.source_collection_id != sources.collection_id:
            raise Phase2ValidationError("Role Assignment artifact references another Source Collection")
        if self.catalog_version != catalog.catalog_version:
            raise Phase2ValidationError("Role Assignment artifact Catalog version does not match")
        if self.artifact_type == CatalogType.PRODUCTION and any(
            item.is_test_fixture for item in sources.sources
        ):
            raise Phase2ValidationError(
                "production Role Assignment artifact cannot use test fixtures"
            )
        assignments = {item.assignment_id: item for item in self.assignments}
        reviews = {item.review_id: item for item in self.review_records}
        if len(assignments) != len(self.assignments):
            raise Phase2ValidationError("Role Assignment artifact contains duplicate assignment IDs")
        if len(reviews) != len(self.review_records):
            raise Phase2ValidationError("Role Assignment artifact contains duplicate review IDs")
        review_by_assignment: dict[str, RoleAssignmentReviewRecord] = {}
        child_by_previous: dict[str, RoleAssignmentRecord] = {}
        current_by_job: dict[str, RoleAssignmentRecord] = {}
        for assignment in self.assignments:
            if assignment.evidence_references != tuple(sorted(set(assignment.evidence_references), key=_evidence_key)):
                raise Phase2ValidationError("Role Assignment evidence must be sorted and unique")
            source = sources.source(assignment.source_id)
            if source.canonical_job_id != assignment.canonical_job_id:
                raise Phase2ValidationError("role assignment source represents another canonical job")
            if source.canonical_source_reference not in {None, source.source_id}:
                raise Phase2ValidationError("role assignment must use a canonical logical source")
            role = catalog.role(assignment.role_id)
            if assignment.specialization_id is not None and assignment.specialization_id not in {item.specialization_id for item in role.specializations}:
                raise Phase2ValidationError("role assignment references unknown specialization")
            for evidence in assignment.evidence_references:
                evidence.validate(canonical_job_id=assignment.canonical_job_id, sources=sources, capture_contents=capture_contents)
            if assignment.status == RoleAssignmentStatus.CURRENT:
                if assignment.canonical_job_id in current_by_job:
                    raise Phase2ValidationError("canonical job has multiple current Role Assignments")
                current_by_job[assignment.canonical_job_id] = assignment
            if assignment.previous_assignment_reference is not None:
                if assignment.previous_assignment_reference in child_by_previous:
                    raise Phase2ValidationError("Role Assignment lineage branches")
                child_by_previous[assignment.previous_assignment_reference] = assignment
        for review in self.review_records:
            if review.assignment_id not in assignments:
                raise Phase2ValidationError("Role Assignment review references missing assignment")
            if review.assignment_id in review_by_assignment:
                raise Phase2ValidationError("Role Assignment has multiple reviews")
            review_by_assignment[review.assignment_id] = review
        edges: dict[str, str] = {}
        for assignment in self.assignments:
            review = reviews.get(assignment.review_reference)
            if review is None or review.assignment_id != assignment.assignment_id:
                raise Phase2ValidationError("Role Assignment lacks its human review")
            if review.evidence_references != assignment.evidence_references:
                raise Phase2ValidationError("Role Assignment review evidence does not match assignment")
            expected_id = generate_role_assignment_id(
                canonical_job_id=assignment.canonical_job_id,
                source_id=assignment.source_id,
                role_id=assignment.role_id,
                specialization_id=assignment.specialization_id,
                previous_assignment_reference=assignment.previous_assignment_reference,
                reviewed_at=review.reviewed_at,
                evidence_references=assignment.evidence_references,
            )
            if assignment.assignment_id != expected_id:
                raise Phase2ValidationError("assignment_id was not generated by Aarvia")
            if review.new_role_id != assignment.role_id or review.new_specialization_id != assignment.specialization_id:
                raise Phase2ValidationError("Role Assignment review new mapping does not match assignment")
            previous_id = assignment.previous_assignment_reference
            if previous_id is None:
                if review.action != RoleAssignmentReviewAction.CONFIRM_INITIAL or review.previous_assignment_reference is not None or review.old_role_id is not None or review.old_specialization_id is not None:
                    raise Phase2ValidationError("initial Role Assignment requires confirm_initial review")
            else:
                previous = assignments.get(previous_id)
                if previous is None:
                    raise Phase2ValidationError("Role Assignment lineage references missing previous assignment")
                if review.action != RoleAssignmentReviewAction.RECLASSIFY or review.previous_assignment_reference != previous_id:
                    raise Phase2ValidationError("reclassified Role Assignment requires matching review lineage")
                if previous.canonical_job_id != assignment.canonical_job_id or previous.source_id != assignment.source_id:
                    raise Phase2ValidationError("Role Assignment lineage crosses job or source")
                previous_review = reviews.get(previous.review_reference)
                if previous_review is None or _as_datetime(review.reviewed_at) <= _as_datetime(previous_review.reviewed_at):
                    raise Phase2ValidationError("Role Assignment review time must increase along lineage")
                if review.old_role_id != previous.role_id or review.old_specialization_id != previous.specialization_id:
                    raise Phase2ValidationError("Role Assignment review old mapping does not match previous assignment")
                edges[previous_id] = assignment.assignment_id
        for assignment in self.assignments:
            has_child = assignment.assignment_id in edges
            expected_status = RoleAssignmentStatus.SUPERSEDED if has_child else RoleAssignmentStatus.CURRENT
            if assignment.status != expected_status:
                raise Phase2ValidationError("Role Assignment status contradicts lineage")
        if set(review_by_assignment) != set(assignments):
            raise Phase2ValidationError("Role Assignment contains orphan review provenance")
        for start in assignments:
            seen: set[str] = set()
            current = start
            while current in edges:
                if current in seen:
                    raise Phase2ValidationError("Role Assignment lineage contains a cycle")
                seen.add(current)
                current = edges[current]

    def current_for_job(self, canonical_job_id: str) -> RoleAssignmentRecord:
        matches = [item for item in self.assignments if item.canonical_job_id == canonical_job_id and item.status == RoleAssignmentStatus.CURRENT]
        if len(matches) != 1:
            raise Phase2ValidationError(f"canonical job {canonical_job_id} requires exactly one current Role Assignment")
        return matches[0]

    def current_for_source(self, source_id: str, sources: JDSourceCollectionV3) -> RoleAssignmentRecord:
        source = sources.source(source_id)
        if source.canonical_job_id is None:
            raise Phase2ValidationError(f"source {source_id} has no canonical job identity")
        return self.current_for_job(source.canonical_job_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type.value,
            "source_collection_id": self.source_collection_id,
            "catalog_version": self.catalog_version,
            "created_at": self.created_at,
            "assignments": [item.to_dict() for item in self.assignments],
            "review_records": [item.to_dict() for item in self.review_records],
        }


def _new_assignment(
    *,
    previous: RoleAssignmentRecord | None,
    canonical_job_id: str,
    source_id: str,
    role_id: str,
    specialization_id: str | None,
    evidence_references: Sequence[RoleEvidenceReference],
    reviewer_reference: str,
    reviewed_at: str,
    decision_reason: str,
) -> tuple[RoleAssignmentRecord, RoleAssignmentReviewRecord]:
    action = RoleAssignmentReviewAction.CONFIRM_INITIAL if previous is None else RoleAssignmentReviewAction.RECLASSIFY
    previous_id = None if previous is None else previous.assignment_id
    assignment_id = generate_role_assignment_id(
        canonical_job_id=canonical_job_id,
        source_id=source_id,
        role_id=role_id,
        specialization_id=specialization_id,
        previous_assignment_reference=previous_id,
        reviewed_at=reviewed_at,
        evidence_references=evidence_references,
    )
    review_id = generate_role_assignment_review_id(
        assignment_id=assignment_id,
        action=action,
        previous_assignment_reference=previous_id,
        old_role_id=None if previous is None else previous.role_id,
        old_specialization_id=None if previous is None else previous.specialization_id,
        new_role_id=role_id,
        new_specialization_id=specialization_id,
        evidence_references=evidence_references,
        reviewer_reference=reviewer_reference,
        reviewed_at=reviewed_at,
        decision_reason=decision_reason,
    )
    evidence = tuple(sorted(set(evidence_references), key=_evidence_key))
    assignment = RoleAssignmentRecord(
        assignment_id=assignment_id,
        canonical_job_id=canonical_job_id,
        source_id=source_id,
        role_id=role_id,
        specialization_id=specialization_id,
        status=RoleAssignmentStatus.CURRENT,
        previous_assignment_reference=previous_id,
        evidence_references=evidence,
        review_reference=review_id,
    )
    review = RoleAssignmentReviewRecord(
        review_id=review_id,
        assignment_id=assignment_id,
        action=action,
        previous_assignment_reference=previous_id,
        old_role_id=None if previous is None else previous.role_id,
        old_specialization_id=None if previous is None else previous.specialization_id,
        new_role_id=role_id,
        new_specialization_id=specialization_id,
        evidence_references=evidence,
        reviewer_reference=_stable_id(reviewer_reference, "reviewer_reference"),
        reviewed_at=_iso_datetime(reviewed_at, "reviewed_at"),
        decision_reason=_text(decision_reason, "decision_reason"),
    )
    return assignment, review


def confirm_initial_role_assignment(
    artifact: RoleAssignmentArtifact,
    *,
    canonical_job_id: str,
    source_id: str,
    role_id: str,
    specialization_id: str | None,
    evidence_references: Sequence[RoleEvidenceReference],
    reviewer_reference: str,
    reviewed_at: str,
    decision_reason: str,
    sources: JDSourceCollectionV3,
    catalog: RoleCatalog,
    capture_contents: Mapping[str, str],
) -> RoleAssignmentArtifact:
    if any(item.canonical_job_id == canonical_job_id for item in artifact.assignments):
        raise Phase2ValidationError("canonical job already has Role Assignment history")
    assignment, review = _new_assignment(
        previous=None,
        canonical_job_id=canonical_job_id,
        source_id=source_id,
        role_id=role_id,
        specialization_id=specialization_id,
        evidence_references=evidence_references,
        reviewer_reference=reviewer_reference,
        reviewed_at=reviewed_at,
        decision_reason=decision_reason,
    )
    result = replace(
        artifact,
        assignments=(*artifact.assignments, assignment),
        review_records=(*artifact.review_records, review),
    )
    result.validate(sources, catalog, capture_contents)
    return result


def add_reclassified_assignment(
    artifact: RoleAssignmentArtifact,
    *,
    current_assignment_id: str,
    role_id: str,
    specialization_id: str | None,
    evidence_references: Sequence[RoleEvidenceReference],
    reviewer_reference: str,
    reviewed_at: str,
    decision_reason: str,
    sources: JDSourceCollectionV3,
    catalog: RoleCatalog,
    capture_contents: Mapping[str, str],
) -> RoleAssignmentArtifact:
    matches = [item for item in artifact.assignments if item.assignment_id == current_assignment_id]
    if len(matches) != 1 or matches[0].status != RoleAssignmentStatus.CURRENT:
        raise Phase2ValidationError("reclassification requires the current Role Assignment")
    previous = matches[0]
    if previous.role_id == role_id and previous.specialization_id == specialization_id:
        raise Phase2ValidationError("reclassification must change Role Family or specialization")
    assignment, review = _new_assignment(
        previous=previous,
        canonical_job_id=previous.canonical_job_id,
        source_id=previous.source_id,
        role_id=role_id,
        specialization_id=specialization_id,
        evidence_references=evidence_references,
        reviewer_reference=reviewer_reference,
        reviewed_at=reviewed_at,
        decision_reason=decision_reason,
    )
    old_items = tuple(
        replace(item, status=RoleAssignmentStatus.SUPERSEDED)
        if item.assignment_id == previous.assignment_id else item
        for item in artifact.assignments
    )
    result = replace(
        artifact,
        assignments=(*old_items, assignment),
        review_records=(*artifact.review_records, review),
    )
    result.validate(sources, catalog, capture_contents)
    return result


def save_role_assignment_artifact(
    value: RoleAssignmentArtifact,
    path: str | Path,
    *,
    sources: JDSourceCollectionV3,
    catalog: RoleCatalog,
    capture_contents: Mapping[str, str],
) -> Path:
    value.validate(sources, catalog, capture_contents)
    canonical = RoleAssignmentArtifact.from_dict(value.to_dict())
    from .phase2_storage import save_phase2_json
    return save_phase2_json(canonical.to_dict(), path)


def load_role_assignment_artifact(
    path: str | Path,
    *,
    sources: JDSourceCollectionV3,
    catalog: RoleCatalog,
    capture_contents: Mapping[str, str],
) -> RoleAssignmentArtifact:
    from .phase2_storage import load_phase2_json
    value = RoleAssignmentArtifact.from_dict(load_phase2_json(path))
    value.validate(sources, catalog, capture_contents)
    return value
