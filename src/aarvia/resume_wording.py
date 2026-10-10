"""User-authored Resume wording review contracts."""

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
from .evidence_bank import EvidenceBank
from .evidence_binding_review import EvidenceBindingReviewArtifact
from .evidence_enrichment import (
    EnrichmentScope,
    EnrichmentTemporality,
    EvidenceEnrichmentArtifact,
)
from .evidence_group_allocation_review import EvidenceGroupAllocationReviewArtifact
from .phase2_storage import load_phase2_json, save_phase2_json
from .profile import CareerProfile
from .profile_dimension_mapping import ProfileDimensionMappingCandidateSet
from .resume_material import (
    ResumeMaterialArtifact,
    ResumeMaterialEligibility,
    ResumeSectionType,
)
from .role_catalog import (
    Phase2ValidationError,
    RoleCatalog,
    _enum,
    _iso_datetime,
    _mapping,
    _reject_unknown,
    _stable_id,
    _string_tuple,
    _text,
    _version,
)
from .role_recommendation import RoleRecommendationArtifact


RESUME_WORDING_REVIEW_SCHEMA = "aarvia.resume_wording_review"
RESUME_WORDING_REVIEW_SCHEMA_VERSION = 1
RESUME_WORDING_REVIEW_ID_VERSION = "resume-wording-review-v1"


class WordingCandidateType(str, Enum):
    BULLET = "bullet"
    STRUCTURAL_ENTRY = "structural_entry"


class WordingOrigin(str, Enum):
    USER_AUTHORED = "user_authored"
    EXACT_MATERIAL = "exact_material"


class WordingReviewDecision(str, Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class WordingReviewerType(str, Enum):
    PROFILE_OWNER = "profile_owner"


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


def _optional_text(value: Any, path: str) -> str | None:
    return None if value is None else _verbatim(value, path)


def _optional_id(value: Any, path: str) -> str | None:
    return None if value is None else _stable_id(value, path)


def _count(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise Phase2ValidationError(f"{path} must be a non-negative integer")
    return value


def _fingerprint(value: Any, path: str) -> str:
    result = _text(value, path)
    assert result is not None
    if not result.startswith("sha256:") or len(result) != 71:
        raise Phase2ValidationError(f"{path} must be a SHA-256 fingerprint")
    try:
        int(result[7:], 16)
    except ValueError as error:
        raise Phase2ValidationError(f"{path} must be a SHA-256 fingerprint") from error
    return result


@dataclass(frozen=True, eq=True)
class WordingCandidate:
    wording_candidate_id: str
    section_type: ResumeSectionType
    candidate_type: WordingCandidateType
    source_record_id: str
    material_ids: tuple[str, ...]
    wording_text: str
    origin: WordingOrigin
    created_at: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> WordingCandidate:
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        result = cls(
            wording_candidate_id=_stable_id(
                data.get("wording_candidate_id"), f"{path}.wording_candidate_id"
            ),
            section_type=_enum(
                data.get("section_type"), ResumeSectionType, f"{path}.section_type"
            ),
            candidate_type=_enum(
                data.get("candidate_type"), WordingCandidateType,
                f"{path}.candidate_type",
            ),
            source_record_id=_stable_id(
                data.get("source_record_id"), f"{path}.source_record_id"
            ),
            material_ids=_string_tuple(data.get("material_ids"), f"{path}.material_ids"),
            wording_text=_verbatim(data.get("wording_text"), f"{path}.wording_text"),
            origin=_enum(data.get("origin"), WordingOrigin, f"{path}.origin"),
            created_at=_iso_datetime(data.get("created_at"), f"{path}.created_at"),
        )
        if not result.material_ids:
            raise Phase2ValidationError(f"{path} must reference at least one Resume Material")
        if result.material_ids != tuple(sorted(set(result.material_ids))):
            raise Phase2ValidationError(f"{path}.material_ids must be unique and sorted")
        if result.wording_candidate_id != generate_wording_candidate_id(result):
            raise Phase2ValidationError(f"{path}.wording_candidate_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "wording_candidate_id": self.wording_candidate_id,
            "section_type": self.section_type.value,
            "candidate_type": self.candidate_type.value,
            "source_record_id": self.source_record_id,
            "material_ids": list(self.material_ids),
            "wording_text": self.wording_text,
            "origin": self.origin.value,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, eq=True)
class WordingReviewRecord:
    review_id: str
    wording_candidate_id: str
    decision: WordingReviewDecision
    reviewer_type: WordingReviewerType
    reviewed_at: str
    reason: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> WordingReviewRecord:
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        result = cls(
            review_id=_stable_id(data.get("review_id"), f"{path}.review_id"),
            wording_candidate_id=_stable_id(
                data.get("wording_candidate_id"), f"{path}.wording_candidate_id"
            ),
            decision=_enum(
                data.get("decision"), WordingReviewDecision, f"{path}.decision"
            ),
            reviewer_type=_enum(
                data.get("reviewer_type"), WordingReviewerType,
                f"{path}.reviewer_type",
            ),
            reviewed_at=_iso_datetime(data.get("reviewed_at"), f"{path}.reviewed_at"),
            reason=_optional_text(data.get("reason"), f"{path}.reason"),
        )
        if result.review_id != generate_wording_review_id(result):
            raise Phase2ValidationError(f"{path}.review_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "wording_candidate_id": self.wording_candidate_id,
            "decision": self.decision.value,
            "reviewer_type": self.reviewer_type.value,
            "reviewed_at": self.reviewed_at,
            "reason": self.reason,
        }


@dataclass(frozen=True, eq=True)
class ResumeWordingSummary:
    candidate_count: int
    confirmed_count: int
    rejected_count: int
    deferred_count: int
    bullet_count: int
    structural_entry_count: int
    resume_selectable_count: int

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> ResumeWordingSummary:
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        return cls(**{
            name: _count(data.get(name), f"{path}.{name}")
            for name in cls.__dataclass_fields__
        })

    def to_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, eq=True)
class ResumeWordingReviewArtifact:
    wording_review_id: str
    resume_material_artifact_id: str
    resume_material_schema_version: int
    profile_fingerprint: str
    mapping_artifact_id: str
    mapping_schema_version: int
    binding_review_artifact_id: str | None
    binding_review_schema_version: int | None
    allocation_review_artifact_id: str | None
    allocation_review_schema_version: int | None
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
    rubric_schema_version: int
    catalog_version: str
    catalog_schema_version: int
    supersedes_wording_review_id: str | None
    created_at: str
    updated_at: str
    candidates: tuple[WordingCandidate, ...]
    reviews: tuple[WordingReviewRecord, ...]
    summary: ResumeWordingSummary
    schema: str = RESUME_WORDING_REVIEW_SCHEMA
    schema_version: int = RESUME_WORDING_REVIEW_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ResumeWordingReviewArtifact:
        data = _mapping(value, "resume_wording_review")
        _reject_unknown(data, set(cls.__dataclass_fields__), "resume_wording_review")
        if data.get("schema") != RESUME_WORDING_REVIEW_SCHEMA or data.get("schema_version") != 1:
            raise Phase2ValidationError("unsupported Resume Wording Review schema")
        raw_candidates = data.get("candidates")
        raw_reviews = data.get("reviews")
        if not isinstance(raw_candidates, list) or not isinstance(raw_reviews, list):
            raise Phase2ValidationError("Resume Wording candidates and reviews must be lists")
        result = cls(
            wording_review_id=_stable_id(
                data.get("wording_review_id"), "resume_wording_review.wording_review_id"
            ),
            resume_material_artifact_id=_stable_id(
                data.get("resume_material_artifact_id"),
                "resume_wording_review.resume_material_artifact_id",
            ),
            resume_material_schema_version=_required_version(
                data.get("resume_material_schema_version"),
                "resume_wording_review.resume_material_schema_version", 1,
            ),
            profile_fingerprint=_fingerprint(
                data.get("profile_fingerprint"), "resume_wording_review.profile_fingerprint"
            ),
            mapping_artifact_id=_stable_id(
                data.get("mapping_artifact_id"), "resume_wording_review.mapping_artifact_id"
            ),
            mapping_schema_version=_required_version(
                data.get("mapping_schema_version"),
                "resume_wording_review.mapping_schema_version", 5,
            ),
            binding_review_artifact_id=_optional_id(
                data.get("binding_review_artifact_id"),
                "resume_wording_review.binding_review_artifact_id",
            ),
            binding_review_schema_version=_optional_version(
                data.get("binding_review_schema_version"),
                "resume_wording_review.binding_review_schema_version",
            ),
            allocation_review_artifact_id=_optional_id(
                data.get("allocation_review_artifact_id"),
                "resume_wording_review.allocation_review_artifact_id",
            ),
            allocation_review_schema_version=_optional_version(
                data.get("allocation_review_schema_version"),
                "resume_wording_review.allocation_review_schema_version",
            ),
            recommendation_set_id=_stable_id(
                data.get("recommendation_set_id"),
                "resume_wording_review.recommendation_set_id",
            ),
            recommendation_schema_version=_required_version(
                data.get("recommendation_schema_version"),
                "resume_wording_review.recommendation_schema_version", 7,
            ),
            decision_id=_stable_id(data.get("decision_id"), "resume_wording_review.decision_id"),
            decision_schema_version=_required_version(
                data.get("decision_schema_version"),
                "resume_wording_review.decision_schema_version", 2,
            ),
            gap_analysis_id=_stable_id(
                data.get("gap_analysis_id"), "resume_wording_review.gap_analysis_id"
            ),
            gap_analysis_schema_version=_required_version(
                data.get("gap_analysis_schema_version"),
                "resume_wording_review.gap_analysis_schema_version", 2,
            ),
            evidence_bank_id=_stable_id(
                data.get("evidence_bank_id"), "resume_wording_review.evidence_bank_id"
            ),
            evidence_bank_schema_version=_required_version(
                data.get("evidence_bank_schema_version"),
                "resume_wording_review.evidence_bank_schema_version", 1,
            ),
            enrichment_artifact_id=_stable_id(
                data.get("enrichment_artifact_id"),
                "resume_wording_review.enrichment_artifact_id",
            ),
            enrichment_schema_version=_required_version(
                data.get("enrichment_schema_version"),
                "resume_wording_review.enrichment_schema_version", 1,
            ),
            rubric_version=_version(
                data.get("rubric_version"), "resume_wording_review.rubric_version"
            ),
            rubric_fingerprint=_fingerprint(
                data.get("rubric_fingerprint"),
                "resume_wording_review.rubric_fingerprint",
            ),
            rubric_schema_version=_required_version(
                data.get("rubric_schema_version"),
                "resume_wording_review.rubric_schema_version", 2,
            ),
            catalog_version=_version(
                data.get("catalog_version"), "resume_wording_review.catalog_version"
            ),
            catalog_schema_version=_required_version(
                data.get("catalog_schema_version"),
                "resume_wording_review.catalog_schema_version", 1,
            ),
            supersedes_wording_review_id=_optional_id(
                data.get("supersedes_wording_review_id"),
                "resume_wording_review.supersedes_wording_review_id",
            ),
            created_at=_iso_datetime(data.get("created_at"), "resume_wording_review.created_at"),
            updated_at=_iso_datetime(data.get("updated_at"), "resume_wording_review.updated_at"),
            candidates=tuple(
                WordingCandidate.from_dict(item, f"resume_wording_review.candidates[{index}]")
                for index, item in enumerate(raw_candidates)
            ),
            reviews=tuple(
                WordingReviewRecord.from_dict(item, f"resume_wording_review.reviews[{index}]")
                for index, item in enumerate(raw_reviews)
            ),
            summary=ResumeWordingSummary.from_dict(
                data.get("summary"), "resume_wording_review.summary"
            ),
        )
        result._validate_shape()
        if result.wording_review_id != generate_resume_wording_artifact_id(result):
            raise Phase2ValidationError("Resume Wording Review artifact ID is not deterministic")
        return result

    def _validate_shape(self) -> None:
        if _parsed_datetime(self.updated_at) < _parsed_datetime(self.created_at):
            raise Phase2ValidationError("Resume Wording Review updated_at cannot precede created_at")
        if (self.binding_review_artifact_id is None) != (
            self.binding_review_schema_version is None
        ):
            raise Phase2ValidationError(
                "Binding Review ID and schema version must appear together"
            )
        if (self.allocation_review_artifact_id is None) != (
            self.allocation_review_schema_version is None
        ):
            raise Phase2ValidationError(
                "Allocation Review ID and schema version must appear together"
            )
        candidate_ids = tuple(item.wording_candidate_id for item in self.candidates)
        review_ids = tuple(item.review_id for item in self.reviews)
        if candidate_ids != tuple(sorted(set(candidate_ids))):
            raise Phase2ValidationError("Resume Wording candidates must be unique and sorted")
        if review_ids != tuple(sorted(set(review_ids))):
            raise Phase2ValidationError("Resume Wording reviews must be unique and sorted")
        reviewed_candidates = tuple(item.wording_candidate_id for item in self.reviews)
        if len(reviewed_candidates) != len(set(reviewed_candidates)):
            raise Phase2ValidationError("each Wording Candidate must have one Review")
        if set(reviewed_candidates) != set(candidate_ids):
            raise Phase2ValidationError("each Wording Candidate must have exactly one Review")
        candidate_by_id = {
            item.wording_candidate_id: item for item in self.candidates
        }
        artifact_created = _parsed_datetime(self.created_at)
        artifact_updated = _parsed_datetime(self.updated_at)
        for candidate in self.candidates:
            created = _parsed_datetime(candidate.created_at)
            if created < artifact_created or created > artifact_updated:
                raise Phase2ValidationError(
                    "Wording Candidate timestamp must fall within the artifact revision"
                )
        for review in self.reviews:
            reviewed = _parsed_datetime(review.reviewed_at)
            candidate_created = _parsed_datetime(
                candidate_by_id[review.wording_candidate_id].created_at
            )
            if reviewed < candidate_created:
                raise Phase2ValidationError("Wording Review cannot precede its Candidate")
            if reviewed > artifact_updated:
                raise Phase2ValidationError("Wording Review cannot follow artifact updated_at")
        if self.summary != _summary(self.candidates, self.reviews):
            raise Phase2ValidationError("Resume Wording summary is not deterministic")

    def validate(self, *, resume_material: ResumeMaterialArtifact,
                 superseded_wording_review: ResumeWordingReviewArtifact | None = None,
                 **context: Any) -> None:
        resume_material.validate(**context)
        _validate_provenance(self, resume_material)
        _validate_candidates(
            self, resume_material, context["evidence_enrichment"]
        )
        _validate_revision(self, superseded_wording_review)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "wording_review_id": self.wording_review_id,
            "resume_material_artifact_id": self.resume_material_artifact_id,
            "resume_material_schema_version": self.resume_material_schema_version,
            "profile_fingerprint": self.profile_fingerprint,
            "mapping_artifact_id": self.mapping_artifact_id,
            "mapping_schema_version": self.mapping_schema_version,
            "binding_review_artifact_id": self.binding_review_artifact_id,
            "binding_review_schema_version": self.binding_review_schema_version,
            "allocation_review_artifact_id": self.allocation_review_artifact_id,
            "allocation_review_schema_version": self.allocation_review_schema_version,
            "recommendation_set_id": self.recommendation_set_id,
            "recommendation_schema_version": self.recommendation_schema_version,
            "decision_id": self.decision_id,
            "decision_schema_version": self.decision_schema_version,
            "gap_analysis_id": self.gap_analysis_id,
            "gap_analysis_schema_version": self.gap_analysis_schema_version,
            "evidence_bank_id": self.evidence_bank_id,
            "evidence_bank_schema_version": self.evidence_bank_schema_version,
            "enrichment_artifact_id": self.enrichment_artifact_id,
            "enrichment_schema_version": self.enrichment_schema_version,
            "rubric_version": self.rubric_version,
            "rubric_fingerprint": self.rubric_fingerprint,
            "rubric_schema_version": self.rubric_schema_version,
            "catalog_version": self.catalog_version,
            "catalog_schema_version": self.catalog_schema_version,
            "supersedes_wording_review_id": self.supersedes_wording_review_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "candidates": [item.to_dict() for item in self.candidates],
            "reviews": [item.to_dict() for item in self.reviews],
            "summary": self.summary.to_dict(),
        }


def _required_version(value: Any, path: str, expected: int) -> int:
    if value != expected:
        raise Phase2ValidationError(f"{path} must equal {expected}")
    return expected


def _optional_version(value: Any, path: str) -> int | None:
    return None if value is None else _required_version(value, path, 1)


def generate_wording_candidate_id(value: WordingCandidate) -> str:
    data = value.to_dict()
    data.pop("wording_candidate_id")
    return _stable_hash("wording_candidate", data)


def generate_wording_review_id(value: WordingReviewRecord) -> str:
    data = value.to_dict()
    data.pop("review_id")
    return _stable_hash("wording_review", data)


def generate_resume_wording_artifact_id(value: ResumeWordingReviewArtifact) -> str:
    data = value.to_dict()
    data.pop("wording_review_id")
    return _stable_hash(RESUME_WORDING_REVIEW_ID_VERSION, data)


def create_wording_candidate(
    *, section_type: ResumeSectionType, candidate_type: WordingCandidateType,
    source_record_id: str, material_ids: Sequence[str], wording_text: str,
    origin: WordingOrigin, created_at: str,
) -> WordingCandidate:
    provisional = WordingCandidate(
        wording_candidate_id="wording_candidate_pending",
        section_type=section_type, candidate_type=candidate_type,
        source_record_id=source_record_id,
        material_ids=tuple(sorted(set(material_ids))), wording_text=wording_text,
        origin=origin, created_at=created_at,
    )
    return WordingCandidate.from_dict({
        **provisional.to_dict(),
        "wording_candidate_id": generate_wording_candidate_id(provisional),
    }, "wording_candidate")


def create_wording_review(
    candidate: WordingCandidate, *, decision: WordingReviewDecision,
    reviewed_at: str, reason: str | None = None,
) -> WordingReviewRecord:
    provisional = WordingReviewRecord(
        review_id="wording_review_pending",
        wording_candidate_id=candidate.wording_candidate_id,
        decision=decision, reviewer_type=WordingReviewerType.PROFILE_OWNER,
        reviewed_at=reviewed_at, reason=reason,
    )
    return WordingReviewRecord.from_dict({
        **provisional.to_dict(), "review_id": generate_wording_review_id(provisional)
    }, "wording_review")


def _summary(
    candidates: Sequence[WordingCandidate], reviews: Sequence[WordingReviewRecord],
) -> ResumeWordingSummary:
    decisions = {item.wording_candidate_id: item.decision for item in reviews}
    return ResumeWordingSummary(
        candidate_count=len(candidates),
        confirmed_count=sum(item == WordingReviewDecision.CONFIRMED for item in decisions.values()),
        rejected_count=sum(item == WordingReviewDecision.REJECTED for item in decisions.values()),
        deferred_count=sum(item == WordingReviewDecision.DEFERRED for item in decisions.values()),
        bullet_count=sum(item.candidate_type == WordingCandidateType.BULLET for item in candidates),
        structural_entry_count=sum(
            item.candidate_type == WordingCandidateType.STRUCTURAL_ENTRY for item in candidates
        ),
        resume_selectable_count=sum(item == WordingReviewDecision.CONFIRMED for item in decisions.values()),
    )


def _validate_provenance(
    value: ResumeWordingReviewArtifact, material: ResumeMaterialArtifact,
) -> None:
    expected = (
        material.resume_material_id, material.profile_fingerprint,
        material.mapping_artifact_id, material.binding_review_artifact_id,
        material.binding_review_schema_version,
        material.allocation_review_artifact_id,
        material.allocation_review_schema_version, material.recommendation_set_id,
        material.decision_id, material.gap_analysis_id, material.evidence_bank_id,
        material.enrichment_artifact_id, material.rubric_version,
        material.rubric_fingerprint, material.catalog_version,
    )
    actual = (
        value.resume_material_artifact_id, value.profile_fingerprint,
        value.mapping_artifact_id, value.binding_review_artifact_id,
        value.binding_review_schema_version,
        value.allocation_review_artifact_id,
        value.allocation_review_schema_version, value.recommendation_set_id,
        value.decision_id, value.gap_analysis_id, value.evidence_bank_id,
        value.enrichment_artifact_id, value.rubric_version,
        value.rubric_fingerprint, value.catalog_version,
    )
    if actual != expected:
        raise Phase2ValidationError("Resume Wording Review provenance is stale")


def _validate_candidates(
    value: ResumeWordingReviewArtifact, resume_material: ResumeMaterialArtifact,
    evidence_enrichment: EvidenceEnrichmentArtifact,
) -> None:
    material_by_id = {item.material_id: item for item in resume_material.materials}
    claims_by_id = {item.claim_id: item for item in evidence_enrichment.claims}
    review_by_candidate = {item.wording_candidate_id: item for item in value.reviews}
    consumed: set[str] = set()
    for candidate in value.candidates:
        try:
            materials = [material_by_id[item] for item in candidate.material_ids]
        except KeyError as error:
            raise Phase2ValidationError("Wording Candidate references unknown Resume Material") from error
        _validate_candidate_materials(candidate, materials, claims_by_id)
        if review_by_candidate[candidate.wording_candidate_id].decision == WordingReviewDecision.CONFIRMED:
            overlap = consumed.intersection(candidate.material_ids)
            if overlap:
                raise Phase2ValidationError("confirmed wording cannot consume material twice")
            consumed.update(candidate.material_ids)


def _validate_candidate_materials(
    candidate: WordingCandidate, materials: Sequence[Any],
    claims_by_id: Mapping[str, Any],
) -> None:
        if any(item.source_record_id != candidate.source_record_id for item in materials):
            raise Phase2ValidationError("Wording Candidate cannot combine Source Records")
        if any(item.section != candidate.section_type for item in materials):
            raise Phase2ValidationError("Wording Candidate section does not match its materials")
        structural = [
            item for item in materials
            if item.eligibility == ResumeMaterialEligibility.STRUCTURAL_ONLY
        ]
        if candidate.candidate_type == WordingCandidateType.BULLET:
            if structural:
                raise Phase2ValidationError("structural material cannot support a bullet")
            if not any(
                item.eligibility == ResumeMaterialEligibility.ACHIEVEMENT_COMPONENT
                for item in materials
            ):
                raise Phase2ValidationError("a bullet requires an achievement component")
        elif len(structural) != len(materials):
            raise Phase2ValidationError("a structural entry may use only structural material")
        if candidate.origin == WordingOrigin.EXACT_MATERIAL and (
            len(materials) != 1 or candidate.wording_text != materials[0].exact_text
        ):
            raise Phase2ValidationError("exact_material wording must equal one exact material")
        claims = [
            claims_by_id[claim_id]
            for material in materials
            for claim_id in material.enrichment_claim_ids
        ]
        normalized_wording = candidate.wording_text.casefold()
        if any(item.temporality == EnrichmentTemporality.ONGOING for item in claims):
            completed_markers = (
                r"\bcompleted\b", r"\bfinished\b", r"\blaunched\b",
                r"\bshipped\b", r"\bdelivered\b",
            )
            states_completion = any(
                re.search(pattern, normalized_wording) for pattern in completed_markers
            )
            states_ongoing = any(
                re.search(pattern, normalized_wording)
                for pattern in (r"\bongoing\b", r"\bcontinue(?:s|d)?\b", r"\bcontinuing\b")
            )
            has_completed_support = any(
                item.temporality == EnrichmentTemporality.COMPLETED for item in claims
            )
            if states_completion and (
                not has_completed_support or not states_ongoing
            ):
                raise Phase2ValidationError(
                    "ongoing material cannot be worded as completed"
                )
        team_claims = [item for item in claims if item.scope == EnrichmentScope.TEAM_CONTEXT]
        personal_claims = [
            item for item in claims
            if item.scope == EnrichmentScope.PERSONAL_CONTRIBUTION
        ]
        if team_claims and not personal_claims:
            team_markers = (
                r"\bteam\b", r"\bcontribut(?:e|ed|ing)\b",
                r"\bcollaborat(?:e|ed|ing|ion)\b", r"\bsupport(?:ed|ing)?\b",
                r"\bassist(?:ed|ing)?\b", r"\bparticipat(?:e|ed|ing)\b",
            )
            if not any(re.search(pattern, normalized_wording) for pattern in team_markers):
                raise Phase2ValidationError(
                    "team-context material must preserve the contribution boundary"
                )


def _validate_revision(
    value: ResumeWordingReviewArtifact,
    previous: ResumeWordingReviewArtifact | None,
) -> None:
    if value.supersedes_wording_review_id is None:
        if previous is not None:
            raise Phase2ValidationError("Resume Wording revision source was not referenced")
        return
    if previous is None:
        raise Phase2ValidationError("Resume Wording revision requires its superseded artifact")
    if value.supersedes_wording_review_id != previous.wording_review_id:
        raise Phase2ValidationError("Resume Wording revision references the wrong artifact")
    if value.created_at != previous.created_at:
        raise Phase2ValidationError("Resume Wording revision must preserve created_at")
    if _parsed_datetime(value.updated_at) <= _parsed_datetime(previous.updated_at):
        raise Phase2ValidationError("Resume Wording revision updated_at must increase")
    provenance = (
        "resume_material_artifact_id", "profile_fingerprint", "mapping_artifact_id",
        "binding_review_artifact_id", "binding_review_schema_version",
        "allocation_review_artifact_id", "allocation_review_schema_version",
        "recommendation_set_id", "decision_id", "gap_analysis_id",
        "evidence_bank_id", "enrichment_artifact_id", "rubric_version",
        "rubric_fingerprint", "catalog_version",
    )
    if any(getattr(value, name) != getattr(previous, name) for name in provenance):
        raise Phase2ValidationError("Resume Wording revision provenance is stale")


def build_resume_wording_review(
    *, resume_material: ResumeMaterialArtifact,
    candidates: Sequence[WordingCandidate], reviews: Sequence[WordingReviewRecord],
    created_at: str, superseded_wording_review: ResumeWordingReviewArtifact | None = None,
    **context: Any,
) -> ResumeWordingReviewArtifact:
    resume_material.validate(**context)
    ordered_candidates = tuple(sorted(candidates, key=lambda item: item.wording_candidate_id))
    ordered_reviews = tuple(sorted(reviews, key=lambda item: item.review_id))
    provisional = ResumeWordingReviewArtifact(
        wording_review_id="resume_wording_review_pending",
        resume_material_artifact_id=resume_material.resume_material_id,
        resume_material_schema_version=1,
        profile_fingerprint=resume_material.profile_fingerprint,
        mapping_artifact_id=resume_material.mapping_artifact_id,
        mapping_schema_version=5,
        binding_review_artifact_id=resume_material.binding_review_artifact_id,
        binding_review_schema_version=resume_material.binding_review_schema_version,
        allocation_review_artifact_id=resume_material.allocation_review_artifact_id,
        allocation_review_schema_version=resume_material.allocation_review_schema_version,
        recommendation_set_id=resume_material.recommendation_set_id,
        recommendation_schema_version=7,
        decision_id=resume_material.decision_id,
        decision_schema_version=2,
        gap_analysis_id=resume_material.gap_analysis_id,
        gap_analysis_schema_version=2,
        evidence_bank_id=resume_material.evidence_bank_id,
        evidence_bank_schema_version=1,
        enrichment_artifact_id=resume_material.enrichment_artifact_id,
        enrichment_schema_version=1,
        rubric_version=resume_material.rubric_version,
        rubric_fingerprint=resume_material.rubric_fingerprint,
        rubric_schema_version=2,
        catalog_version=resume_material.catalog_version,
        catalog_schema_version=1,
        supersedes_wording_review_id=(
            None if superseded_wording_review is None
            else superseded_wording_review.wording_review_id
        ),
        created_at=(
            created_at if superseded_wording_review is None
            else superseded_wording_review.created_at
        ),
        updated_at=created_at,
        candidates=ordered_candidates,
        reviews=ordered_reviews,
        summary=_summary(ordered_candidates, ordered_reviews),
    )
    result = ResumeWordingReviewArtifact.from_dict({
        **provisional.to_dict(),
        "wording_review_id": generate_resume_wording_artifact_id(provisional),
    })
    result.validate(
        resume_material=resume_material,
        superseded_wording_review=superseded_wording_review,
        **context,
    )
    return result


def save_resume_wording_review(
    value: ResumeWordingReviewArtifact, path: str | Path, **context: Any,
) -> Path:
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"Resume Wording Review artifacts are immutable: {target}")
    value.validate(**context)
    return save_phase2_json(
        ResumeWordingReviewArtifact.from_dict(value.to_dict()).to_dict(), target
    )


def load_resume_wording_review(
    path: str | Path, **context: Any,
) -> ResumeWordingReviewArtifact:
    value = ResumeWordingReviewArtifact.from_dict(load_phase2_json(path))
    value.validate(**context)
    return value


def load_resume_wording_review_revision_source(
    path: str | Path, *, resume_material: ResumeMaterialArtifact, **context: Any,
) -> ResumeWordingReviewArtifact:
    value = ResumeWordingReviewArtifact.from_dict(load_phase2_json(path))
    resume_material.validate(**context)
    _validate_provenance(value, resume_material)
    _validate_candidates(value, resume_material, context["evidence_enrichment"])
    return value


def select_confirmed_resume_wording(
    value: ResumeWordingReviewArtifact, **context: Any,
) -> tuple[WordingCandidate, ...]:
    value.validate(**context)
    decisions = {item.wording_candidate_id: item.decision for item in value.reviews}
    return tuple(
        item for item in value.candidates
        if decisions[item.wording_candidate_id] == WordingReviewDecision.CONFIRMED
    )
