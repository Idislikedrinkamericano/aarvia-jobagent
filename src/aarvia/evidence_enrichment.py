"""User-confirmed enrichment layered over an immutable Evidence Bank."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .capability_rubric import CapabilityRubric
from .career_direction import UserRoleDecision
from .career_gap_analysis import CareerGapAnalysis
from .evidence_bank import EvidenceBank, EvidenceItem, EvidenceSourceRecord, ResumeEvidenceUse
from .evidence_binding_review import EvidenceBindingReviewArtifact
from .evidence_group_allocation_review import EvidenceGroupAllocationReviewArtifact
from .phase2_storage import load_phase2_json, save_phase2_json
from .profile import CareerProfile
from .profile_dimension_mapping import ProfileDimensionMappingCandidateSet
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
from .role_recommendation import RoleRecommendationArtifact


EVIDENCE_ENRICHMENT_SCHEMA = "aarvia.evidence_enrichment"
EVIDENCE_ENRICHMENT_SCHEMA_VERSION = 1
EVIDENCE_ENRICHMENT_ID_VERSION = "evidence-enrichment-v1"


class EnrichmentClaimType(str, Enum):
    CHALLENGE = "challenge"
    ACTION = "action"
    RESPONSIBILITY = "responsibility"
    RESULT = "result"
    METRIC = "metric"
    SCALE = "scale"
    TECHNOLOGY = "technology"
    OWNERSHIP = "ownership"
    COMPLETION_STATUS = "completion_status"
    TIMELINE = "timeline"
    CONTEXT = "context"


class EnrichmentRelationship(str, Enum):
    CONFIRMS = "confirms"
    CLARIFIES = "clarifies"
    SUPPLEMENTS = "supplements"


class EnrichmentTemporality(str, Enum):
    COMPLETED = "completed"
    ONGOING = "ongoing"


class EnrichmentScope(str, Enum):
    PERSONAL_CONTRIBUTION = "personal_contribution"
    TEAM_CONTEXT = "team_context"
    PROJECT_CONTEXT = "project_context"


class EnrichmentOrigin(str, Enum):
    USER_PROVIDED = "user_provided"


class ClaimReviewDecision(str, Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class ClaimReviewerType(str, Enum):
    PROFILE_OWNER = "profile_owner"


class ResumeEnrichmentUse(str, Enum):
    ACHIEVEMENT_DETAIL = "achievement_detail"
    FACTUAL_CONTEXT = "factual_context"
    STRUCTURAL_INFORMATION = "structural_information"


_STRUCTURED_VALUE_TYPES = {
    EnrichmentClaimType.METRIC,
    EnrichmentClaimType.SCALE,
    EnrichmentClaimType.TECHNOLOGY,
    EnrichmentClaimType.COMPLETION_STATUS,
    EnrichmentClaimType.TIMELINE,
}
_ACHIEVEMENT_TYPES = {
    EnrichmentClaimType.CHALLENGE,
    EnrichmentClaimType.ACTION,
    EnrichmentClaimType.RESPONSIBILITY,
    EnrichmentClaimType.RESULT,
    EnrichmentClaimType.METRIC,
    EnrichmentClaimType.SCALE,
    EnrichmentClaimType.OWNERSHIP,
}


def _optional_id(value: Any, path: str) -> str | None:
    return None if value is None else _stable_id(value, path)


def _fingerprint(value: Any, path: str) -> str:
    result = _text(value, path)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", result):
        raise Phase2ValidationError(f"{path} must be a SHA-256 fingerprint")
    return result


def _parsed_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _stable_hash(prefix: str, value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _verbatim(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Phase2ValidationError(f"{path} must be non-empty text")
    if "\x00" in value:
        raise Phase2ValidationError(f"{path} contains an invalid control character")
    return value


def _optional_verbatim(value: Any, path: str) -> str | None:
    return None if value is None else _verbatim(value, path)


def _string_ids(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise Phase2ValidationError(f"{path} must be a list")
    result = tuple(_stable_id(item, f"{path}[{index}]") for index, item in enumerate(value))
    if result != tuple(sorted(set(result))):
        raise Phase2ValidationError(f"{path} must be unique and deterministically sorted")
    return result


@dataclass(frozen=True, eq=True)
class EnrichmentClaim:
    claim_id: str
    source_record_id: str
    evidence_item_ids: tuple[str, ...]
    claim_type: EnrichmentClaimType
    relationship: EnrichmentRelationship
    user_supplied_statement: str
    temporality: EnrichmentTemporality
    scope: EnrichmentScope
    structured_value: str | None
    origin: EnrichmentOrigin = EnrichmentOrigin.USER_PROVIDED

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> EnrichmentClaim:
        data = _mapping(value, path)
        _reject_unknown(
            data,
            {
                "claim_id", "source_record_id", "evidence_item_ids", "claim_type",
                "relationship", "user_supplied_statement", "temporality", "scope",
                "structured_value", "origin",
            },
            path,
        )
        result = cls(
            claim_id=_stable_id(data.get("claim_id"), f"{path}.claim_id"),
            source_record_id=_stable_id(
                data.get("source_record_id"), f"{path}.source_record_id"
            ),
            evidence_item_ids=_string_ids(
                data.get("evidence_item_ids"), f"{path}.evidence_item_ids"
            ),
            claim_type=_enum(data.get("claim_type"), EnrichmentClaimType, f"{path}.claim_type"),
            relationship=_enum(
                data.get("relationship"), EnrichmentRelationship, f"{path}.relationship"
            ),
            user_supplied_statement=_verbatim(
                data.get("user_supplied_statement"), f"{path}.user_supplied_statement"
            ),
            temporality=_enum(
                data.get("temporality"), EnrichmentTemporality, f"{path}.temporality"
            ),
            scope=_enum(data.get("scope"), EnrichmentScope, f"{path}.scope"),
            structured_value=_optional_verbatim(
                data.get("structured_value"), f"{path}.structured_value"
            ),
            origin=_enum(data.get("origin"), EnrichmentOrigin, f"{path}.origin"),
        )
        if result.structured_value is not None and result.claim_type not in _STRUCTURED_VALUE_TYPES:
            raise Phase2ValidationError(
                f"{path}.structured_value is not allowed for {result.claim_type.value}"
            )
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "source_record_id": self.source_record_id,
            "evidence_item_ids": list(self.evidence_item_ids),
            "claim_type": self.claim_type.value,
            "relationship": self.relationship.value,
            "user_supplied_statement": self.user_supplied_statement,
            "temporality": self.temporality.value,
            "scope": self.scope.value,
            "structured_value": self.structured_value,
            "origin": self.origin.value,
        }


@dataclass(frozen=True, eq=True)
class ClaimReviewRecord:
    review_id: str
    claim_id: str
    decision: ClaimReviewDecision
    reviewer_type: ClaimReviewerType
    reviewed_at: str
    decision_reason: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> ClaimReviewRecord:
        data = _mapping(value, path)
        _reject_unknown(
            data,
            {"review_id", "claim_id", "decision", "reviewer_type", "reviewed_at", "decision_reason"},
            path,
        )
        result = cls(
            review_id=_stable_id(data.get("review_id"), f"{path}.review_id"),
            claim_id=_stable_id(data.get("claim_id"), f"{path}.claim_id"),
            decision=_enum(data.get("decision"), ClaimReviewDecision, f"{path}.decision"),
            reviewer_type=_enum(
                data.get("reviewer_type"), ClaimReviewerType, f"{path}.reviewer_type"
            ),
            reviewed_at=_iso_datetime(data.get("reviewed_at"), f"{path}.reviewed_at"),
            decision_reason=_optional_verbatim(
                data.get("decision_reason"), f"{path}.decision_reason"
            ),
        )
        if result.review_id != generate_claim_review_id(result):
            raise Phase2ValidationError(f"{path}.review_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "claim_id": self.claim_id,
            "decision": self.decision.value,
            "reviewer_type": self.reviewer_type.value,
            "reviewed_at": self.reviewed_at,
            "decision_reason": self.decision_reason,
        }


@dataclass(frozen=True, eq=True)
class EvidenceEnrichmentSummary:
    claim_count: int
    confirmed_count: int
    rejected_count: int
    deferred_count: int
    source_level_context_count: int
    resume_selectable_count: int

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> EvidenceEnrichmentSummary:
        data = _mapping(value, path)
        names = set(cls.__dataclass_fields__)
        _reject_unknown(data, names, path)
        values: dict[str, int] = {}
        for name in names:
            item = data.get(name)
            if not isinstance(item, int) or isinstance(item, bool) or item < 0:
                raise Phase2ValidationError(f"{path}.{name} must be a non-negative integer")
            values[name] = item
        return cls(**values)

    def to_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, eq=True)
class ResumeEnrichmentMaterial:
    claim_id: str
    source_record_id: str
    evidence_item_ids: tuple[str, ...]
    use: ResumeEnrichmentUse


@dataclass(frozen=True, eq=True)
class EvidenceEnrichmentArtifact:
    enrichment_artifact_id: str
    profile_fingerprint: str
    evidence_bank_id: str
    mapping_artifact_id: str
    binding_review_artifact_id: str | None
    allocation_review_artifact_id: str | None
    recommendation_set_id: str
    recommendation_schema_version: int
    decision_id: str
    gap_analysis_id: str
    rubric_version: str
    rubric_fingerprint: str
    catalog_version: str
    supersedes_enrichment_artifact_id: str | None
    created_at: str
    updated_at: str
    claims: tuple[EnrichmentClaim, ...]
    claim_reviews: tuple[ClaimReviewRecord, ...]
    summary: EvidenceEnrichmentSummary
    schema: str = EVIDENCE_ENRICHMENT_SCHEMA
    schema_version: int = EVIDENCE_ENRICHMENT_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> EvidenceEnrichmentArtifact:
        data = _mapping(value, "evidence_enrichment")
        allowed = {
            "schema", "schema_version", "enrichment_artifact_id", "profile_fingerprint",
            "evidence_bank_id", "mapping_artifact_id", "binding_review_artifact_id",
            "allocation_review_artifact_id", "recommendation_set_id",
            "recommendation_schema_version", "decision_id", "gap_analysis_id",
            "rubric_version", "rubric_fingerprint", "catalog_version",
            "supersedes_enrichment_artifact_id", "created_at", "updated_at", "claims",
            "claim_reviews", "summary",
        }
        _reject_unknown(data, allowed, "evidence_enrichment")
        if data.get("schema") != EVIDENCE_ENRICHMENT_SCHEMA or data.get("schema_version") != 1:
            raise Phase2ValidationError("unsupported Evidence Enrichment schema")
        if data.get("recommendation_schema_version") != 7:
            raise Phase2ValidationError("Evidence Enrichment requires Recommendation schema 7")
        raw_claims = data.get("claims")
        raw_reviews = data.get("claim_reviews")
        if not isinstance(raw_claims, list) or not isinstance(raw_reviews, list):
            raise Phase2ValidationError("Evidence Enrichment claims and reviews must be lists")
        result = cls(
            enrichment_artifact_id=_stable_id(
                data.get("enrichment_artifact_id"), "evidence_enrichment.enrichment_artifact_id"
            ),
            profile_fingerprint=_fingerprint(
                data.get("profile_fingerprint"), "evidence_enrichment.profile_fingerprint"
            ),
            evidence_bank_id=_stable_id(data.get("evidence_bank_id"), "evidence_enrichment.evidence_bank_id"),
            mapping_artifact_id=_stable_id(data.get("mapping_artifact_id"), "evidence_enrichment.mapping_artifact_id"),
            binding_review_artifact_id=_optional_id(data.get("binding_review_artifact_id"), "evidence_enrichment.binding_review_artifact_id"),
            allocation_review_artifact_id=_optional_id(data.get("allocation_review_artifact_id"), "evidence_enrichment.allocation_review_artifact_id"),
            recommendation_set_id=_stable_id(data.get("recommendation_set_id"), "evidence_enrichment.recommendation_set_id"),
            recommendation_schema_version=7,
            decision_id=_stable_id(data.get("decision_id"), "evidence_enrichment.decision_id"),
            gap_analysis_id=_stable_id(data.get("gap_analysis_id"), "evidence_enrichment.gap_analysis_id"),
            rubric_version=_version(data.get("rubric_version"), "evidence_enrichment.rubric_version"),
            rubric_fingerprint=_fingerprint(data.get("rubric_fingerprint"), "evidence_enrichment.rubric_fingerprint"),
            catalog_version=_version(data.get("catalog_version"), "evidence_enrichment.catalog_version"),
            supersedes_enrichment_artifact_id=_optional_id(data.get("supersedes_enrichment_artifact_id"), "evidence_enrichment.supersedes_enrichment_artifact_id"),
            created_at=_iso_datetime(data.get("created_at"), "evidence_enrichment.created_at"),
            updated_at=_iso_datetime(data.get("updated_at"), "evidence_enrichment.updated_at"),
            claims=tuple(EnrichmentClaim.from_dict(item, f"evidence_enrichment.claims[{index}]") for index, item in enumerate(raw_claims)),
            claim_reviews=tuple(ClaimReviewRecord.from_dict(item, f"evidence_enrichment.claim_reviews[{index}]") for index, item in enumerate(raw_reviews)),
            summary=EvidenceEnrichmentSummary.from_dict(data.get("summary"), "evidence_enrichment.summary"),
        )
        result._validate_shape()
        if result.enrichment_artifact_id != generate_enrichment_artifact_id(result):
            raise Phase2ValidationError("Evidence Enrichment artifact ID is not deterministic")
        return result

    def _validate_shape(self) -> None:
        if _parsed_datetime(self.updated_at) < _parsed_datetime(self.created_at):
            raise Phase2ValidationError("Evidence Enrichment updated_at cannot precede created_at")
        claim_ids = tuple(item.claim_id for item in self.claims)
        review_ids = tuple(item.review_id for item in self.claim_reviews)
        if claim_ids != tuple(sorted(set(claim_ids))):
            raise Phase2ValidationError("Evidence Enrichment claims must be unique and sorted")
        if review_ids != tuple(sorted(set(review_ids))):
            raise Phase2ValidationError("Evidence Enrichment reviews must be unique and sorted")
        reviewed_claim_ids = tuple(sorted(item.claim_id for item in self.claim_reviews))
        if reviewed_claim_ids != claim_ids:
            raise Phase2ValidationError("every Enrichment Claim must have exactly one Review Record")
        if len(reviewed_claim_ids) != len(set(reviewed_claim_ids)):
            raise Phase2ValidationError("an Enrichment Claim cannot have multiple Review Records")

    def validate(
        self, *, profile: CareerProfile, rubric: CapabilityRubric, catalog: RoleCatalog,
        mapping: ProfileDimensionMappingCandidateSet,
        recommendation: RoleRecommendationArtifact, decision: UserRoleDecision,
        gap_analysis: CareerGapAnalysis, evidence_bank: EvidenceBank,
        evidence_reviews: EvidenceBindingReviewArtifact | None = None,
        allocation_reviews: EvidenceGroupAllocationReviewArtifact | None = None,
        superseded_enrichment: EvidenceEnrichmentArtifact | None = None,
        superseded_decision: UserRoleDecision | None = None,
    ) -> None:
        if EvidenceEnrichmentArtifact.from_dict(self.to_dict()) != self:
            raise Phase2ValidationError("Evidence Enrichment is not in canonical form")
        evidence_bank.validate(
            profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
            recommendation=recommendation, decision=decision, gap_analysis=gap_analysis,
            evidence_reviews=evidence_reviews, allocation_reviews=allocation_reviews,
            superseded_decision=superseded_decision,
        )
        expected_provenance = (
            evidence_bank.profile_fingerprint, evidence_bank.evidence_bank_id,
            evidence_bank.mapping_artifact_id, evidence_bank.binding_review_artifact_id,
            evidence_bank.allocation_review_artifact_id, evidence_bank.recommendation_set_id,
            evidence_bank.decision_id, evidence_bank.gap_analysis_id,
            evidence_bank.rubric_version, evidence_bank.rubric_fingerprint,
            evidence_bank.catalog_version,
        )
        actual_provenance = (
            self.profile_fingerprint, self.evidence_bank_id, self.mapping_artifact_id,
            self.binding_review_artifact_id, self.allocation_review_artifact_id,
            self.recommendation_set_id, self.decision_id, self.gap_analysis_id,
            self.rubric_version, self.rubric_fingerprint, self.catalog_version,
        )
        if actual_provenance != expected_provenance:
            raise Phase2ValidationError("Evidence Enrichment provenance is stale or inconsistent")
        _validate_claim_context(self, evidence_bank)
        if superseded_enrichment is not None:
            _validate_revision_source_against_context(superseded_enrichment, evidence_bank)
        _validate_enrichment_revision(self, superseded_enrichment)
        if self.summary != _summary(self.claims, self.claim_reviews, evidence_bank):
            raise Phase2ValidationError("Evidence Enrichment summary is not deterministic")
        if self.enrichment_artifact_id != generate_enrichment_artifact_id(self):
            raise Phase2ValidationError("Evidence Enrichment artifact ID is not deterministic")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "enrichment_artifact_id": self.enrichment_artifact_id,
            "profile_fingerprint": self.profile_fingerprint,
            "evidence_bank_id": self.evidence_bank_id,
            "mapping_artifact_id": self.mapping_artifact_id,
            "binding_review_artifact_id": self.binding_review_artifact_id,
            "allocation_review_artifact_id": self.allocation_review_artifact_id,
            "recommendation_set_id": self.recommendation_set_id,
            "recommendation_schema_version": self.recommendation_schema_version,
            "decision_id": self.decision_id,
            "gap_analysis_id": self.gap_analysis_id,
            "rubric_version": self.rubric_version,
            "rubric_fingerprint": self.rubric_fingerprint,
            "catalog_version": self.catalog_version,
            "supersedes_enrichment_artifact_id": self.supersedes_enrichment_artifact_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "claims": [item.to_dict() for item in self.claims],
            "claim_reviews": [item.to_dict() for item in self.claim_reviews],
            "summary": self.summary.to_dict(),
        }


def generate_enrichment_claim_id(
    value: EnrichmentClaim, evidence_bank_id: str
) -> str:
    return _stable_hash("enrichment_claim", {
        "evidence_bank_contract": EVIDENCE_ENRICHMENT_ID_VERSION,
        "evidence_bank_id": evidence_bank_id,
        "source_record_id": value.source_record_id,
        "evidence_item_ids": list(value.evidence_item_ids),
        "claim_type": value.claim_type.value,
        "relationship": value.relationship.value,
        "user_supplied_statement": value.user_supplied_statement,
        "temporality": value.temporality.value,
        "scope": value.scope.value,
        "structured_value": value.structured_value,
        "origin": value.origin.value,
    })


def generate_claim_review_id(value: ClaimReviewRecord) -> str:
    return _stable_hash("enrichment_review", {
        "claim_id": value.claim_id,
        "decision": value.decision.value,
        "reviewer_type": value.reviewer_type.value,
        "reviewed_at": value.reviewed_at,
        "decision_reason": value.decision_reason,
    })


def generate_enrichment_artifact_id(value: EvidenceEnrichmentArtifact) -> str:
    payload = value.to_dict()
    payload.pop("enrichment_artifact_id", None)
    return _stable_hash("evidence_enrichment", payload)


def create_enrichment_claim(
    *, evidence_bank_id: str, source_record_id: str, evidence_item_ids: Sequence[str],
    claim_type: EnrichmentClaimType, relationship: EnrichmentRelationship,
    user_supplied_statement: str, temporality: EnrichmentTemporality,
    scope: EnrichmentScope, structured_value: str | None = None,
) -> EnrichmentClaim:
    provisional = EnrichmentClaim(
        claim_id="enrichment_claim_pending",
        source_record_id=source_record_id,
        evidence_item_ids=tuple(sorted(set(evidence_item_ids))),
        claim_type=claim_type,
        relationship=relationship,
        user_supplied_statement=user_supplied_statement,
        temporality=temporality,
        scope=scope,
        structured_value=structured_value,
    )
    return EnrichmentClaim.from_dict({
        **provisional.to_dict(),
        "claim_id": generate_enrichment_claim_id(provisional, evidence_bank_id),
    }, "enrichment_claim")


def create_claim_review(
    claim: EnrichmentClaim, *, decision: ClaimReviewDecision, reviewed_at: str,
    decision_reason: str | None = None,
) -> ClaimReviewRecord:
    provisional = ClaimReviewRecord(
        review_id="enrichment_review_pending", claim_id=claim.claim_id,
        decision=decision, reviewer_type=ClaimReviewerType.PROFILE_OWNER,
        reviewed_at=reviewed_at, decision_reason=decision_reason,
    )
    return ClaimReviewRecord.from_dict({
        **provisional.to_dict(),
        "review_id": generate_claim_review_id(provisional),
    }, "claim_review")


def _resume_materials(
    claims: Sequence[EnrichmentClaim], reviews: Sequence[ClaimReviewRecord],
    evidence_bank: EvidenceBank,
) -> tuple[ResumeEnrichmentMaterial, ...]:
    review_by_claim = {item.claim_id: item for item in reviews}
    item_by_id = {item.evidence_item_id: item for item in evidence_bank.items}
    result: list[ResumeEnrichmentMaterial] = []
    for claim in claims:
        review = review_by_claim[claim.claim_id]
        if review.decision != ClaimReviewDecision.CONFIRMED or not claim.evidence_item_ids:
            continue
        items = tuple(item_by_id[item_id] for item_id in claim.evidence_item_ids)
        structural_only = all(
            item.resume_use == ResumeEvidenceUse.STRUCTURAL_INFORMATION for item in items
        )
        if structural_only:
            if claim.claim_type not in {
                EnrichmentClaimType.TECHNOLOGY, EnrichmentClaimType.COMPLETION_STATUS,
                EnrichmentClaimType.TIMELINE, EnrichmentClaimType.CONTEXT,
            }:
                continue
            use = ResumeEnrichmentUse.STRUCTURAL_INFORMATION
        elif claim.claim_type in _ACHIEVEMENT_TYPES:
            use = ResumeEnrichmentUse.ACHIEVEMENT_DETAIL
        else:
            use = ResumeEnrichmentUse.FACTUAL_CONTEXT
        result.append(ResumeEnrichmentMaterial(
            claim_id=claim.claim_id, source_record_id=claim.source_record_id,
            evidence_item_ids=claim.evidence_item_ids, use=use,
        ))
    return tuple(sorted(result, key=lambda item: item.claim_id))


def _summary(
    claims: Sequence[EnrichmentClaim], reviews: Sequence[ClaimReviewRecord],
    evidence_bank: EvidenceBank,
) -> EvidenceEnrichmentSummary:
    decisions = [item.decision for item in reviews]
    return EvidenceEnrichmentSummary(
        claim_count=len(claims),
        confirmed_count=decisions.count(ClaimReviewDecision.CONFIRMED),
        rejected_count=decisions.count(ClaimReviewDecision.REJECTED),
        deferred_count=decisions.count(ClaimReviewDecision.DEFERRED),
        source_level_context_count=sum(not item.evidence_item_ids for item in claims),
        resume_selectable_count=len(_resume_materials(claims, reviews, evidence_bank)),
    )


def _validate_claim_context(value: EvidenceEnrichmentArtifact, evidence_bank: EvidenceBank) -> None:
    source_ids = {item.source_record_id for item in evidence_bank.sources}
    item_by_id = {item.evidence_item_id: item for item in evidence_bank.items}
    for claim in value.claims:
        if claim.claim_id != generate_enrichment_claim_id(claim, evidence_bank.evidence_bank_id):
            raise Phase2ValidationError("Enrichment Claim ID is not deterministic for its Evidence Bank")
        if claim.source_record_id not in source_ids:
            raise Phase2ValidationError("Enrichment Claim references an unknown source record")
        for item_id in claim.evidence_item_ids:
            item = item_by_id.get(item_id)
            if item is None:
                raise Phase2ValidationError("Enrichment Claim references an unknown Evidence Item")
            if item.source_record_id != claim.source_record_id:
                raise Phase2ValidationError("Enrichment Claim crosses Evidence Source Records")


def _validate_enrichment_revision(
    current: EvidenceEnrichmentArtifact,
    previous: EvidenceEnrichmentArtifact | None,
) -> None:
    if previous is None:
        if current.supersedes_enrichment_artifact_id is not None:
            raise Phase2ValidationError("Evidence Enrichment revision source is required")
        return
    if current.supersedes_enrichment_artifact_id != previous.enrichment_artifact_id:
        raise Phase2ValidationError("Evidence Enrichment revision lineage is invalid")
    if current.created_at != previous.created_at or _parsed_datetime(current.updated_at) <= _parsed_datetime(previous.updated_at):
        raise Phase2ValidationError("Evidence Enrichment revision timestamps are invalid")
    previous_claims = {item.claim_id: item for item in previous.claims}
    current_claims = {item.claim_id: item for item in current.claims}
    if any(current_claims.get(claim_id) != claim for claim_id, claim in previous_claims.items()):
        raise Phase2ValidationError("Evidence Enrichment revisions cannot remove or edit claims")


def _validate_revision_source_against_context(
    value: EvidenceEnrichmentArtifact, evidence_bank: EvidenceBank
) -> None:
    if EvidenceEnrichmentArtifact.from_dict(value.to_dict()) != value:
        raise Phase2ValidationError("Evidence Enrichment revision source is not canonical")
    expected = (
        evidence_bank.profile_fingerprint, evidence_bank.evidence_bank_id,
        evidence_bank.mapping_artifact_id, evidence_bank.binding_review_artifact_id,
        evidence_bank.allocation_review_artifact_id, evidence_bank.recommendation_set_id,
        evidence_bank.decision_id, evidence_bank.gap_analysis_id,
        evidence_bank.rubric_version, evidence_bank.rubric_fingerprint,
        evidence_bank.catalog_version,
    )
    actual = (
        value.profile_fingerprint, value.evidence_bank_id, value.mapping_artifact_id,
        value.binding_review_artifact_id, value.allocation_review_artifact_id,
        value.recommendation_set_id, value.decision_id, value.gap_analysis_id,
        value.rubric_version, value.rubric_fingerprint, value.catalog_version,
    )
    if actual != expected:
        raise Phase2ValidationError("Evidence Enrichment revision source is stale")
    _validate_claim_context(value, evidence_bank)
    if value.summary != _summary(value.claims, value.claim_reviews, evidence_bank):
        raise Phase2ValidationError("Evidence Enrichment revision summary is invalid")


def build_evidence_enrichment(
    *, profile: CareerProfile, rubric: CapabilityRubric, catalog: RoleCatalog,
    mapping: ProfileDimensionMappingCandidateSet,
    recommendation: RoleRecommendationArtifact, decision: UserRoleDecision,
    gap_analysis: CareerGapAnalysis, evidence_bank: EvidenceBank,
    claims: Sequence[EnrichmentClaim], claim_reviews: Sequence[ClaimReviewRecord],
    created_at: str, evidence_reviews: EvidenceBindingReviewArtifact | None = None,
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None = None,
    superseded_enrichment: EvidenceEnrichmentArtifact | None = None,
    superseded_decision: UserRoleDecision | None = None,
) -> EvidenceEnrichmentArtifact:
    evidence_bank.validate(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision, gap_analysis=gap_analysis,
        evidence_reviews=evidence_reviews, allocation_reviews=allocation_reviews,
        superseded_decision=superseded_decision,
    )
    combined_claims = tuple(sorted(claims, key=lambda item: item.claim_id))
    combined_reviews = tuple(sorted(claim_reviews, key=lambda item: item.review_id))
    provisional = EvidenceEnrichmentArtifact(
        enrichment_artifact_id="evidence_enrichment_pending",
        profile_fingerprint=evidence_bank.profile_fingerprint,
        evidence_bank_id=evidence_bank.evidence_bank_id,
        mapping_artifact_id=evidence_bank.mapping_artifact_id,
        binding_review_artifact_id=evidence_bank.binding_review_artifact_id,
        allocation_review_artifact_id=evidence_bank.allocation_review_artifact_id,
        recommendation_set_id=evidence_bank.recommendation_set_id,
        recommendation_schema_version=7,
        decision_id=evidence_bank.decision_id,
        gap_analysis_id=evidence_bank.gap_analysis_id,
        rubric_version=evidence_bank.rubric_version,
        rubric_fingerprint=evidence_bank.rubric_fingerprint,
        catalog_version=evidence_bank.catalog_version,
        supersedes_enrichment_artifact_id=(
            None if superseded_enrichment is None else superseded_enrichment.enrichment_artifact_id
        ),
        created_at=(superseded_enrichment.created_at if superseded_enrichment else created_at),
        updated_at=created_at,
        claims=combined_claims,
        claim_reviews=combined_reviews,
        summary=_summary(combined_claims, combined_reviews, evidence_bank),
    )
    result = EvidenceEnrichmentArtifact.from_dict({
        **provisional.to_dict(),
        "enrichment_artifact_id": generate_enrichment_artifact_id(provisional),
    })
    result.validate(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision, gap_analysis=gap_analysis,
        evidence_bank=evidence_bank, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews, superseded_enrichment=superseded_enrichment,
        superseded_decision=superseded_decision,
    )
    return result


def save_evidence_enrichment(
    value: EvidenceEnrichmentArtifact, path: str | Path, *, profile: CareerProfile,
    rubric: CapabilityRubric, catalog: RoleCatalog,
    mapping: ProfileDimensionMappingCandidateSet,
    recommendation: RoleRecommendationArtifact, decision: UserRoleDecision,
    gap_analysis: CareerGapAnalysis, evidence_bank: EvidenceBank,
    evidence_reviews: EvidenceBindingReviewArtifact | None = None,
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None = None,
    superseded_enrichment: EvidenceEnrichmentArtifact | None = None,
    superseded_decision: UserRoleDecision | None = None,
) -> Path:
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"Evidence Enrichment artifacts are immutable: {target}")
    value.validate(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision, gap_analysis=gap_analysis,
        evidence_bank=evidence_bank, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews, superseded_enrichment=superseded_enrichment,
        superseded_decision=superseded_decision,
    )
    return save_phase2_json(EvidenceEnrichmentArtifact.from_dict(value.to_dict()).to_dict(), target)


def load_evidence_enrichment(
    path: str | Path, *, profile: CareerProfile, rubric: CapabilityRubric,
    catalog: RoleCatalog, mapping: ProfileDimensionMappingCandidateSet,
    recommendation: RoleRecommendationArtifact, decision: UserRoleDecision,
    gap_analysis: CareerGapAnalysis, evidence_bank: EvidenceBank,
    evidence_reviews: EvidenceBindingReviewArtifact | None = None,
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None = None,
    superseded_enrichment: EvidenceEnrichmentArtifact | None = None,
    superseded_decision: UserRoleDecision | None = None,
) -> EvidenceEnrichmentArtifact:
    value = EvidenceEnrichmentArtifact.from_dict(load_phase2_json(path))
    value.validate(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision, gap_analysis=gap_analysis,
        evidence_bank=evidence_bank, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews, superseded_enrichment=superseded_enrichment,
        superseded_decision=superseded_decision,
    )
    return value


def load_evidence_enrichment_revision_source(path: str | Path, **context: Any) -> EvidenceEnrichmentArtifact:
    value = EvidenceEnrichmentArtifact.from_dict(load_phase2_json(path))
    bank = context["evidence_bank"]
    bank.validate(
        profile=context["profile"], rubric=context["rubric"], catalog=context["catalog"],
        mapping=context["mapping"], recommendation=context["recommendation"],
        decision=context["decision"], gap_analysis=context["gap_analysis"],
        evidence_reviews=context.get("evidence_reviews"),
        allocation_reviews=context.get("allocation_reviews"),
        superseded_decision=context.get("superseded_decision"),
    )
    _validate_revision_source_against_context(value, bank)
    return value


def select_resume_enrichment_materials(
    value: EvidenceEnrichmentArtifact, *, profile: CareerProfile,
    rubric: CapabilityRubric, catalog: RoleCatalog,
    mapping: ProfileDimensionMappingCandidateSet,
    recommendation: RoleRecommendationArtifact, decision: UserRoleDecision,
    gap_analysis: CareerGapAnalysis, evidence_bank: EvidenceBank,
    evidence_reviews: EvidenceBindingReviewArtifact | None = None,
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None = None,
    superseded_enrichment: EvidenceEnrichmentArtifact | None = None,
    superseded_decision: UserRoleDecision | None = None,
) -> tuple[ResumeEnrichmentMaterial, ...]:
    value.validate(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision, gap_analysis=gap_analysis,
        evidence_bank=evidence_bank, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews, superseded_enrichment=superseded_enrichment,
        superseded_decision=superseded_decision,
    )
    return _resume_materials(value.claims, value.claim_reviews, evidence_bank)
