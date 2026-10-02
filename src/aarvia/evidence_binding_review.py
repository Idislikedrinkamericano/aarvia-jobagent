"""Typed human-review provenance for policy-derived Mapping evidence bindings."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .capability_rubric import CapabilityRubric
from .career_direction import profile_fingerprint
from .phase2_storage import load_phase2_json, save_phase2_json
from .profile import CareerProfile
from .profile_dimension_mapping import (
    ProfileDimensionMappingCandidateSet,
    ProfileCriterionEvidenceBinding,
    generate_mapping_artifact_id,
)
from .role_catalog import (
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


EVIDENCE_REVIEW_SCHEMA = "aarvia.evidence_binding_reviews"
EVIDENCE_REVIEW_SCHEMA_VERSION = 1
EVIDENCE_REVIEW_ID_VERSION = "evidence-binding-review-v1"
EVIDENCE_REVIEW_ARTIFACT_ID_VERSION = "evidence-binding-review-artifact-v1"
RUBRIC_HASH_VERSION = "capability-rubric-canonical-v1"


class BindingReviewDecision(str, Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class BindingReviewerType(str, Enum):
    PROFILE_OWNER = "profile_owner"
    AUTHORIZED_REVIEWER = "authorized_reviewer"


class EvidenceBindingReviewStaleError(Phase2ValidationError):
    """The review context no longer identifies the current Profile or inputs."""


def capability_rubric_sha256(rubric: CapabilityRubric) -> str:
    if not isinstance(rubric, CapabilityRubric):
        raise TypeError("rubric must be a CapabilityRubric")
    payload = json.dumps(
        rubric.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(
        f"{RUBRIC_HASH_VERSION}|{payload}".encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def generate_binding_review_id(
    *,
    mapping_artifact_id: str,
    binding_id: str,
    role_id: str,
    dimension_id: str,
    criterion_id: str,
    decision: BindingReviewDecision,
    reviewer_type: BindingReviewerType,
    reviewed_at: str,
) -> str:
    values = (
        EVIDENCE_REVIEW_ID_VERSION,
        _stable_id(mapping_artifact_id, "mapping_artifact_id"),
        _stable_id(binding_id, "binding_id"),
        _stable_id(role_id, "role_id"),
        _stable_id(dimension_id, "dimension_id"),
        _stable_id(criterion_id, "criterion_id"),
        _enum(decision, BindingReviewDecision, "decision").value,
        _enum(reviewer_type, BindingReviewerType, "reviewer_type").value,
        _iso_datetime(reviewed_at, "reviewed_at"),
    )
    return f"binding_review_{hashlib.sha256('|'.join(values).encode()).hexdigest()[:24]}"


@dataclass(frozen=True, eq=True)
class EvidenceBindingReview:
    review_id: str
    binding_id: str
    role_id: str
    dimension_id: str
    criterion_id: str
    decision: BindingReviewDecision
    reviewer_type: BindingReviewerType
    reviewed_at: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> EvidenceBindingReview:
        data = _mapping(value, path)
        _reject_unknown(
            data,
            {
                "review_id", "binding_id", "role_id", "dimension_id",
                "criterion_id", "decision", "reviewer_type", "reviewed_at",
            },
            path,
        )
        return cls(
            review_id=_stable_id(data.get("review_id"), f"{path}.review_id"),
            binding_id=_stable_id(data.get("binding_id"), f"{path}.binding_id"),
            role_id=_stable_id(data.get("role_id"), f"{path}.role_id"),
            dimension_id=_stable_id(data.get("dimension_id"), f"{path}.dimension_id"),
            criterion_id=_stable_id(data.get("criterion_id"), f"{path}.criterion_id"),
            decision=_enum(data.get("decision"), BindingReviewDecision, f"{path}.decision"),
            reviewer_type=_enum(
                data.get("reviewer_type"), BindingReviewerType, f"{path}.reviewer_type"
            ),
            reviewed_at=_iso_datetime(data.get("reviewed_at"), f"{path}.reviewed_at"),
        )

    def validate(
        self,
        *,
        mapping_artifact_id: str,
        binding: ProfileCriterionEvidenceBinding,
    ) -> None:
        if not isinstance(self.decision, BindingReviewDecision):
            raise Phase2ValidationError("Evidence Binding Review decision is invalid")
        if not isinstance(self.reviewer_type, BindingReviewerType):
            raise Phase2ValidationError("Evidence Binding Review reviewer type is invalid")
        _iso_datetime(self.reviewed_at, "review.reviewed_at")
        if (
            self.binding_id,
            self.role_id,
            self.dimension_id,
            self.criterion_id,
        ) != (
            binding.binding_id,
            binding.role_id,
            binding.dimension_id,
            binding.criterion_id,
        ):
            raise EvidenceBindingReviewStaleError(
                "Evidence Binding Review no longer matches its Mapping binding"
            )
        expected = generate_binding_review_id(
            mapping_artifact_id=mapping_artifact_id,
            binding_id=self.binding_id,
            role_id=self.role_id,
            dimension_id=self.dimension_id,
            criterion_id=self.criterion_id,
            decision=self.decision,
            reviewer_type=self.reviewer_type,
            reviewed_at=self.reviewed_at,
        )
        if self.review_id != expected:
            raise Phase2ValidationError("Evidence Binding Review ID is not deterministic")

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "binding_id": self.binding_id,
            "role_id": self.role_id,
            "dimension_id": self.dimension_id,
            "criterion_id": self.criterion_id,
            "decision": self.decision.value,
            "reviewer_type": self.reviewer_type.value,
            "reviewed_at": self.reviewed_at,
        }


def generate_evidence_review_artifact_id(
    *,
    profile_fingerprint_value: str,
    rubric_version: str,
    rubric_sha256: str,
    mapping_artifact_id: str,
    reviews: tuple[EvidenceBindingReview, ...],
) -> str:
    payload = json.dumps(
        [item.to_dict() for item in sorted(reviews, key=lambda item: item.review_id)],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    values = (
        EVIDENCE_REVIEW_ARTIFACT_ID_VERSION,
        _text(profile_fingerprint_value, "profile_fingerprint"),
        _version(rubric_version, "rubric_version"),
        _text(rubric_sha256, "rubric_sha256"),
        _stable_id(mapping_artifact_id, "mapping_artifact_id"),
        hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    )
    return f"evidence_reviews_{hashlib.sha256('|'.join(values).encode()).hexdigest()[:24]}"


@dataclass(frozen=True, eq=True)
class EvidenceBindingReviewArtifact:
    review_artifact_id: str
    profile_fingerprint: str
    rubric_version: str
    rubric_sha256: str
    mapping_artifact_id: str
    reviews: tuple[EvidenceBindingReview, ...]
    schema: str = EVIDENCE_REVIEW_SCHEMA
    schema_version: int = EVIDENCE_REVIEW_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> EvidenceBindingReviewArtifact:
        data = _mapping(value, "evidence_reviews")
        _reject_unknown(
            data,
            {
                "schema", "schema_version", "review_artifact_id",
                "profile_fingerprint", "rubric_version", "rubric_sha256",
                "mapping_artifact_id", "reviews",
            },
            "evidence_reviews",
        )
        if (
            data.get("schema") != EVIDENCE_REVIEW_SCHEMA
            or data.get("schema_version") != EVIDENCE_REVIEW_SCHEMA_VERSION
        ):
            raise Phase2ValidationError("unsupported Evidence Binding Review schema")
        raw_reviews = data.get("reviews")
        if not isinstance(raw_reviews, list):
            raise Phase2ValidationError("evidence_reviews.reviews must be a list")
        return cls(
            review_artifact_id=_stable_id(
                data.get("review_artifact_id"), "evidence_reviews.review_artifact_id"
            ),
            profile_fingerprint=_text(
                data.get("profile_fingerprint"), "evidence_reviews.profile_fingerprint"
            ),
            rubric_version=_version(
                data.get("rubric_version"), "evidence_reviews.rubric_version"
            ),
            rubric_sha256=_text(
                data.get("rubric_sha256"), "evidence_reviews.rubric_sha256"
            ),
            mapping_artifact_id=_stable_id(
                data.get("mapping_artifact_id"), "evidence_reviews.mapping_artifact_id"
            ),
            reviews=tuple(
                EvidenceBindingReview.from_dict(item, f"evidence_reviews.reviews[{index}]")
                for index, item in enumerate(raw_reviews)
            ),
        )

    def validate(
        self,
        *,
        profile: CareerProfile,
        rubric: CapabilityRubric,
        mapping: ProfileDimensionMappingCandidateSet,
        catalog: RoleCatalog,
    ) -> None:
        if (self.schema, self.schema_version) != (
            EVIDENCE_REVIEW_SCHEMA,
            EVIDENCE_REVIEW_SCHEMA_VERSION,
        ):
            raise Phase2ValidationError("unsupported Evidence Binding Review schema")
        if not isinstance(self.reviews, tuple) or any(
            not isinstance(item, EvidenceBindingReview) for item in self.reviews
        ):
            raise Phase2ValidationError("Evidence Binding Reviews must be a typed tuple")
        if rubric.schema_version != 2 or mapping.schema_version not in {3, 4, 5}:
            raise Phase2ValidationError(
                "Evidence Binding Reviews require Rubric schema 2 and Mapping schema 3 or 4"
            )
        expected_profile = profile_fingerprint(profile)
        expected_rubric_hash = capability_rubric_sha256(rubric)
        expected_mapping = generate_mapping_artifact_id(mapping)
        if self.profile_fingerprint != expected_profile:
            raise EvidenceBindingReviewStaleError("Evidence Binding Review Profile is stale")
        if self.rubric_version != rubric.rubric_version or self.rubric_sha256 != expected_rubric_hash:
            raise EvidenceBindingReviewStaleError("Evidence Binding Review Rubric is stale")
        if self.mapping_artifact_id != expected_mapping:
            raise EvidenceBindingReviewStaleError("Evidence Binding Review Mapping is stale")
        mapping.validate(profile=profile, rubric=rubric, catalog=catalog)
        binding_ids = [item.binding_id for item in self.reviews]
        review_ids = [item.review_id for item in self.reviews]
        if len(binding_ids) != len(set(binding_ids)):
            raise Phase2ValidationError("a Mapping binding can have at most one Review")
        if len(review_ids) != len(set(review_ids)):
            raise Phase2ValidationError("duplicate Evidence Binding Review IDs")
        bindings = {
            item.binding_id: item
            for item in mapping.mappings
            if isinstance(item, ProfileCriterionEvidenceBinding)
        }
        for group in mapping.unresolved_evidence_groups:
            bindings.update({item.binding_id: item for item in group.members})
        for review in self.reviews:
            binding = bindings.get(review.binding_id)
            if binding is None:
                raise EvidenceBindingReviewStaleError(
                    "Evidence Binding Review references a missing Mapping binding"
                )
            review.validate(mapping_artifact_id=self.mapping_artifact_id, binding=binding)
        if tuple(item.review_id for item in self.reviews) != tuple(
            sorted(item.review_id for item in self.reviews)
        ):
            raise Phase2ValidationError("Evidence Binding Reviews must be deterministically ordered")
        expected_id = generate_evidence_review_artifact_id(
            profile_fingerprint_value=self.profile_fingerprint,
            rubric_version=self.rubric_version,
            rubric_sha256=self.rubric_sha256,
            mapping_artifact_id=self.mapping_artifact_id,
            reviews=self.reviews,
        )
        if self.review_artifact_id != expected_id:
            raise Phase2ValidationError("Evidence Binding Review artifact ID is not deterministic")

    def decision_by_binding(self) -> dict[str, BindingReviewDecision]:
        return {item.binding_id: item.decision for item in self.reviews}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "review_artifact_id": self.review_artifact_id,
            "profile_fingerprint": self.profile_fingerprint,
            "rubric_version": self.rubric_version,
            "rubric_sha256": self.rubric_sha256,
            "mapping_artifact_id": self.mapping_artifact_id,
            "reviews": [item.to_dict() for item in self.reviews],
        }


def create_evidence_binding_review_artifact(
    *,
    profile: CareerProfile,
    rubric: CapabilityRubric,
    mapping: ProfileDimensionMappingCandidateSet,
    catalog: RoleCatalog,
    decisions: Mapping[str, tuple[BindingReviewDecision, BindingReviewerType, str]],
) -> EvidenceBindingReviewArtifact:
    mapping.validate(profile=profile, rubric=rubric, catalog=catalog)
    if rubric.schema_version != 2 or mapping.schema_version not in {3, 4, 5}:
        raise Phase2ValidationError(
            "Evidence Binding Reviews require Rubric schema 2 and Mapping schema 3 or 4"
        )
    bindings = {
        item.binding_id: item
        for item in mapping.mappings
        if isinstance(item, ProfileCriterionEvidenceBinding)
    }
    for group in mapping.unresolved_evidence_groups:
        bindings.update({item.binding_id: item for item in group.members})
    if set(decisions) - set(bindings):
        raise Phase2ValidationError("Evidence Binding Review references a missing Mapping binding")
    mapping_id = generate_mapping_artifact_id(mapping)
    reviews = []
    for binding_id in sorted(decisions):
        raw_decision, raw_reviewer, reviewed_at = decisions[binding_id]
        binding = bindings[binding_id]
        decision = _enum(raw_decision, BindingReviewDecision, "decision")
        reviewer = _enum(raw_reviewer, BindingReviewerType, "reviewer_type")
        reviewed = _iso_datetime(reviewed_at, "reviewed_at")
        review_id = generate_binding_review_id(
            mapping_artifact_id=mapping_id,
            binding_id=binding.binding_id,
            role_id=binding.role_id,
            dimension_id=binding.dimension_id,
            criterion_id=binding.criterion_id,
            decision=decision,
            reviewer_type=reviewer,
            reviewed_at=reviewed,
        )
        reviews.append(
            EvidenceBindingReview(
                review_id, binding.binding_id, binding.role_id, binding.dimension_id,
                binding.criterion_id, decision, reviewer, reviewed
            )
        )
    ordered = tuple(sorted(reviews, key=lambda item: item.review_id))
    fingerprint = profile_fingerprint(profile)
    rubric_hash = capability_rubric_sha256(rubric)
    artifact = EvidenceBindingReviewArtifact(
        review_artifact_id=generate_evidence_review_artifact_id(
            profile_fingerprint_value=fingerprint,
            rubric_version=rubric.rubric_version,
            rubric_sha256=rubric_hash,
            mapping_artifact_id=mapping_id,
            reviews=ordered,
        ),
        profile_fingerprint=fingerprint,
        rubric_version=rubric.rubric_version,
        rubric_sha256=rubric_hash,
        mapping_artifact_id=mapping_id,
        reviews=ordered,
    )
    artifact.validate(profile=profile, rubric=rubric, mapping=mapping, catalog=catalog)
    return artifact


def save_evidence_binding_reviews(
    value: EvidenceBindingReviewArtifact,
    path: str | Path,
    *,
    profile: CareerProfile,
    rubric: CapabilityRubric,
    mapping: ProfileDimensionMappingCandidateSet,
    catalog: RoleCatalog,
) -> Path:
    if not isinstance(value, EvidenceBindingReviewArtifact):
        raise TypeError("value must be an EvidenceBindingReviewArtifact")
    value.validate(profile=profile, rubric=rubric, mapping=mapping, catalog=catalog)
    return save_phase2_json(
        EvidenceBindingReviewArtifact.from_dict(value.to_dict()).to_dict(), path
    )


def load_evidence_binding_reviews(
    path: str | Path,
    *,
    profile: CareerProfile,
    rubric: CapabilityRubric,
    mapping: ProfileDimensionMappingCandidateSet,
    catalog: RoleCatalog,
) -> EvidenceBindingReviewArtifact:
    raw = load_phase2_json(path)
    value = EvidenceBindingReviewArtifact.from_dict(raw)
    value.validate(profile=profile, rubric=rubric, mapping=mapping, catalog=catalog)
    return value
