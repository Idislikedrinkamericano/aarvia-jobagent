"""Human allocation provenance for unresolved Mapping schema 5 evidence groups."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .capability_rubric import CapabilityRubric
from .career_direction import profile_fingerprint
from .evidence_binding_review import capability_rubric_sha256
from .phase2_storage import load_phase2_json, save_phase2_json
from .profile import CareerProfile
from .profile_dimension_mapping import (
    AllocationReasonCode,
    AllocatedProfileCriterionEvidenceBinding,
    ContributionRelationship,
    ProfileCriterionEvidenceBinding,
    ProfileDimensionMappingCandidateSet,
    UnresolvedEvidenceGroup,
    _allocated_binding,
    generate_mapping_artifact_id,
)
from .role_catalog import Phase2ValidationError, RoleCatalog

ALLOCATION_REVIEW_SCHEMA = "aarvia.evidence_group_allocation_reviews"
ALLOCATION_REVIEW_SCHEMA_VERSION = 1


class AllocationReviewDecision(str, Enum):
    RESOLVED = "resolved"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class AllocationReviewerType(str, Enum):
    PROFILE_OWNER = "profile_owner"
    AUTHORIZED_REVIEWER = "authorized_reviewer"


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Phase2ValidationError(f"{path} must be a non-empty string")
    return value.strip()


def _iso(value: Any, path: str) -> str:
    result = _text(value, path)
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as error:
        raise Phase2ValidationError(f"{path} must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise Phase2ValidationError(f"{path} must include a timezone")
    return result


def _stable(prefix: str, values: tuple[str, ...]) -> str:
    return f"{prefix}_{hashlib.sha256('|'.join(values).encode()).hexdigest()[:24]}"


def generate_allocation_review_id(*, mapping_artifact_id: str, evidence_group_id: str,
                                  decision: AllocationReviewDecision,
                                  primary_dimension_id: str | None,
                                  secondary_dimension_id: str | None,
                                  reviewer_type: AllocationReviewerType,
                                  reviewed_at: str) -> str:
    return _stable("allocation_review", (
        mapping_artifact_id, evidence_group_id, decision.value,
        primary_dimension_id or "", secondary_dimension_id or "",
        reviewer_type.value, reviewed_at,
    ))


@dataclass(frozen=True, eq=True)
class EvidenceGroupAllocationReview:
    review_id: str
    evidence_group_id: str
    member_binding_ids: tuple[str, ...]
    decision: AllocationReviewDecision
    primary_dimension_id: str | None
    secondary_dimension_id: str | None
    reviewer_type: AllocationReviewerType
    reviewed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "evidence_group_id": self.evidence_group_id,
            "member_binding_ids": list(self.member_binding_ids),
            "decision": self.decision.value,
            "primary_dimension_id": self.primary_dimension_id,
            "secondary_dimension_id": self.secondary_dimension_id,
            "reviewer_type": self.reviewer_type.value,
            "reviewed_at": self.reviewed_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> EvidenceGroupAllocationReview:
        allowed = {"review_id", "evidence_group_id", "member_binding_ids", "decision", "primary_dimension_id", "secondary_dimension_id", "reviewer_type", "reviewed_at"}
        if not isinstance(value, Mapping) or set(value) != allowed:
            raise Phase2ValidationError(f"{path} fields are invalid")
        members = value["member_binding_ids"]
        if not isinstance(members, list) or any(not isinstance(item, str) for item in members):
            raise Phase2ValidationError(f"{path}.member_binding_ids must be strings")
        try:
            decision = AllocationReviewDecision(value["decision"])
            reviewer = AllocationReviewerType(value["reviewer_type"])
        except (ValueError, TypeError) as error:
            raise Phase2ValidationError(f"{path} enum is invalid") from error
        primary = value["primary_dimension_id"]
        secondary = value["secondary_dimension_id"]
        if primary is not None and not isinstance(primary, str):
            raise Phase2ValidationError(f"{path}.primary_dimension_id is invalid")
        if secondary is not None and not isinstance(secondary, str):
            raise Phase2ValidationError(f"{path}.secondary_dimension_id is invalid")
        return cls(_text(value["review_id"], f"{path}.review_id"), _text(value["evidence_group_id"], f"{path}.evidence_group_id"), tuple(members), decision, primary, secondary, reviewer, _iso(value["reviewed_at"], f"{path}.reviewed_at"))


@dataclass(frozen=True, eq=True)
class EvidenceGroupAllocationReviewArtifact:
    review_artifact_id: str
    profile_fingerprint: str
    rubric_version: str
    rubric_sha256: str
    mapping_artifact_id: str
    reviews: tuple[EvidenceGroupAllocationReview, ...]
    schema: str = ALLOCATION_REVIEW_SCHEMA
    schema_version: int = ALLOCATION_REVIEW_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.schema, "schema_version": self.schema_version, "review_artifact_id": self.review_artifact_id, "profile_fingerprint": self.profile_fingerprint, "rubric_version": self.rubric_version, "rubric_sha256": self.rubric_sha256, "mapping_artifact_id": self.mapping_artifact_id, "reviews": [item.to_dict() for item in self.reviews]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> EvidenceGroupAllocationReviewArtifact:
        allowed = {"schema", "schema_version", "review_artifact_id", "profile_fingerprint", "rubric_version", "rubric_sha256", "mapping_artifact_id", "reviews"}
        if not isinstance(value, Mapping) or set(value) != allowed or value.get("schema") != ALLOCATION_REVIEW_SCHEMA or value.get("schema_version") != 1:
            raise Phase2ValidationError("unsupported Evidence Group Allocation Review schema")
        raw = value.get("reviews")
        if not isinstance(raw, list):
            raise Phase2ValidationError("allocation reviews must be a list")
        return cls(_text(value.get("review_artifact_id"), "allocation_reviews.review_artifact_id"), _text(value.get("profile_fingerprint"), "allocation_reviews.profile_fingerprint"), _text(value.get("rubric_version"), "allocation_reviews.rubric_version"), _text(value.get("rubric_sha256"), "allocation_reviews.rubric_sha256"), _text(value.get("mapping_artifact_id"), "allocation_reviews.mapping_artifact_id"), tuple(EvidenceGroupAllocationReview.from_dict(item, f"allocation_reviews.reviews[{index}]") for index, item in enumerate(raw)))

    def validate(self, *, profile: CareerProfile, rubric: CapabilityRubric,
                 mapping: ProfileDimensionMappingCandidateSet, catalog: RoleCatalog) -> None:
        mapping.validate(profile=profile, rubric=rubric, catalog=catalog)
        if mapping.schema_version != 5:
            raise Phase2ValidationError("allocation reviews require Mapping schema 5")
        mapping_id = generate_mapping_artifact_id(mapping)
        if self.profile_fingerprint != profile_fingerprint(profile) or self.rubric_version != rubric.rubric_version or self.rubric_sha256 != capability_rubric_sha256(rubric) or self.mapping_artifact_id != mapping_id:
            raise Phase2ValidationError("Evidence Group Allocation Review is stale")
        groups = {item.evidence_group_id: item for item in mapping.unresolved_evidence_groups}
        if len(self.reviews) != len({item.evidence_group_id for item in self.reviews}):
            raise Phase2ValidationError("duplicate allocation review")
        if tuple(self.reviews) != tuple(sorted(self.reviews, key=lambda item: item.evidence_group_id)):
            raise Phase2ValidationError("allocation reviews are not canonical")
        for review in self.reviews:
            group = groups.get(review.evidence_group_id)
            if group is None:
                raise Phase2ValidationError("allocation review references an unknown group")
            expected_members = tuple(item.binding_id for item in group.members)
            if review.member_binding_ids != expected_members:
                raise Phase2ValidationError("allocation review member snapshot mismatch")
            if review.decision == AllocationReviewDecision.RESOLVED:
                if review.primary_dimension_id not in group.allowed_primary_dimension_ids:
                    raise Phase2ValidationError("allocation primary is not an allowed choice")
                if review.secondary_dimension_id is not None and (review.secondary_dimension_id == review.primary_dimension_id or review.secondary_dimension_id not in group.allowed_secondary_dimension_ids):
                    raise Phase2ValidationError("allocation secondary is not an allowed choice")
            elif review.primary_dimension_id is not None or review.secondary_dimension_id is not None:
                raise Phase2ValidationError("non-resolved allocation review cannot select dimensions")
            expected_id = generate_allocation_review_id(mapping_artifact_id=mapping_id, evidence_group_id=review.evidence_group_id, decision=review.decision, primary_dimension_id=review.primary_dimension_id, secondary_dimension_id=review.secondary_dimension_id, reviewer_type=review.reviewer_type, reviewed_at=review.reviewed_at)
            if review.review_id != expected_id:
                raise Phase2ValidationError("allocation review ID is not deterministic")
        expected_artifact = _stable("allocation_reviews", (self.profile_fingerprint, self.rubric_version, self.rubric_sha256, self.mapping_artifact_id, json.dumps([item.to_dict() for item in self.reviews], sort_keys=True, separators=(",", ":"))))
        if self.review_artifact_id != expected_artifact:
            raise Phase2ValidationError("allocation review artifact ID is not deterministic")

    def decisions_by_group(self) -> dict[str, EvidenceGroupAllocationReview]:
        return {item.evidence_group_id: item for item in self.reviews}


def create_evidence_group_allocation_review_artifact(*, profile: CareerProfile,
        rubric: CapabilityRubric, mapping: ProfileDimensionMappingCandidateSet,
        catalog: RoleCatalog, decisions: Mapping[str, tuple[AllocationReviewDecision, str | None, str | None, AllocationReviewerType, str]]) -> EvidenceGroupAllocationReviewArtifact:
    mapping.validate(profile=profile, rubric=rubric, catalog=catalog)
    mapping_id = generate_mapping_artifact_id(mapping)
    groups = {item.evidence_group_id: item for item in mapping.unresolved_evidence_groups}
    reviews = []
    for group_id in sorted(decisions):
        if group_id not in groups:
            raise Phase2ValidationError("allocation decision references an unknown group")
        raw_decision, primary, secondary, raw_reviewer, raw_reviewed_at = decisions[group_id]
        try:
            decision = AllocationReviewDecision(raw_decision)
            reviewer = AllocationReviewerType(raw_reviewer)
        except (TypeError, ValueError) as error:
            raise Phase2ValidationError("allocation review decision context is invalid") from error
        reviewed_at = _iso(raw_reviewed_at, "allocation_reviews.reviewed_at")
        group = groups[group_id]
        reviews.append(EvidenceGroupAllocationReview(generate_allocation_review_id(mapping_artifact_id=mapping_id, evidence_group_id=group_id, decision=decision, primary_dimension_id=primary, secondary_dimension_id=secondary, reviewer_type=reviewer, reviewed_at=reviewed_at), group_id, tuple(item.binding_id for item in group.members), decision, primary, secondary, reviewer, reviewed_at))
    fingerprint = profile_fingerprint(profile)
    rubric_hash = capability_rubric_sha256(rubric)
    artifact_id = _stable("allocation_reviews", (fingerprint, rubric.rubric_version, rubric_hash, mapping_id, json.dumps([item.to_dict() for item in reviews], sort_keys=True, separators=(",", ":"))))
    result = EvidenceGroupAllocationReviewArtifact(artifact_id, fingerprint, rubric.rubric_version, rubric_hash, mapping_id, tuple(reviews))
    result.validate(profile=profile, rubric=rubric, mapping=mapping, catalog=catalog)
    return result


def resolved_group_bindings(mapping: ProfileDimensionMappingCandidateSet,
        reviews: EvidenceGroupAllocationReviewArtifact | None) -> tuple[AllocatedProfileCriterionEvidenceBinding, ...]:
    if reviews is None:
        return ()
    decisions = reviews.decisions_by_group()
    result: list[AllocatedProfileCriterionEvidenceBinding] = []
    for group in mapping.unresolved_evidence_groups:
        review = decisions.get(group.evidence_group_id)
        if review is None or review.decision != AllocationReviewDecision.RESOLVED:
            continue
        for member in group.members:
            if member.dimension_id == review.primary_dimension_id:
                result.append(_allocated_binding(member, relationship=ContributionRelationship.PRIMARY, primary_reason=AllocationReasonCode.UNIQUE_STRONGEST_PRIMARY))
            elif member.dimension_id == review.secondary_dimension_id:
                result.append(_allocated_binding(member, relationship=ContributionRelationship.SECONDARY))
    return tuple(sorted(result, key=lambda item: item.binding_id))


def effective_resolved_group_bindings(
    mapping: ProfileDimensionMappingCandidateSet,
    reviews: EvidenceGroupAllocationReviewArtifact | None,
    *, excluded_binding_ids: frozenset[str] = frozenset(),
) -> tuple[AllocatedProfileCriterionEvidenceBinding, ...]:
    """Apply binding rejection without choosing an unapproved Dimension."""
    initial = resolved_group_bindings(mapping, reviews)
    by_group: dict[str, list[AllocatedProfileCriterionEvidenceBinding]] = {}
    for binding in initial:
        if binding.binding_id not in excluded_binding_ids:
            by_group.setdefault(binding.evidence_group_id, []).append(binding)
    result: list[AllocatedProfileCriterionEvidenceBinding] = []
    for group_id in sorted(by_group):
        members = by_group[group_id]
        primaries = [item for item in members if item.contribution_relationship == ContributionRelationship.PRIMARY]
        if primaries:
            result.extend(members)
            continue
        for item in members:
            base = ProfileCriterionEvidenceBinding(**{key: value for key, value in item.__dict__.items() if key in ProfileCriterionEvidenceBinding.__dataclass_fields__})
            result.append(_allocated_binding(base, relationship=ContributionRelationship.PRIMARY, primary_reason=AllocationReasonCode.UNIQUE_STRONGEST_PRIMARY))
    return tuple(sorted(result, key=lambda item: item.binding_id))


def unresolved_group_ids(
    mapping: ProfileDimensionMappingCandidateSet,
    reviews: EvidenceGroupAllocationReviewArtifact | None,
) -> tuple[str, ...]:
    """Return groups that still require an allocation decision."""
    decisions = {} if reviews is None else reviews.decisions_by_group()
    return tuple(sorted(
        group.evidence_group_id
        for group in mapping.unresolved_evidence_groups
        if group.evidence_group_id not in decisions
        or decisions[group.evidence_group_id].decision == AllocationReviewDecision.DEFERRED
    ))


def save_evidence_group_allocation_reviews(value: EvidenceGroupAllocationReviewArtifact, path: str | Path, *, profile: CareerProfile, rubric: CapabilityRubric, mapping: ProfileDimensionMappingCandidateSet, catalog: RoleCatalog) -> Path:
    value.validate(profile=profile, rubric=rubric, mapping=mapping, catalog=catalog)
    return save_phase2_json(EvidenceGroupAllocationReviewArtifact.from_dict(value.to_dict()).to_dict(), path)


def load_evidence_group_allocation_reviews(path: str | Path, *, profile: CareerProfile, rubric: CapabilityRubric, mapping: ProfileDimensionMappingCandidateSet, catalog: RoleCatalog) -> EvidenceGroupAllocationReviewArtifact:
    value = EvidenceGroupAllocationReviewArtifact.from_dict(load_phase2_json(path))
    value.validate(profile=profile, rubric=rubric, mapping=mapping, catalog=catalog)
    return value
