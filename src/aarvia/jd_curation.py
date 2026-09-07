"""Versioned contracts for requirement extraction and human curation lineage."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import hashlib
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .jd_sources import JDSourceCollection, JDSourceCollectionV3
from .role_catalog import (
    CatalogType,
    Phase2ValidationError,
    RequirementCategory,
    RequirementImportance,
    RoleCatalog,
    _enum,
    _iso_datetime,
    _mapping,
    _reject_unknown,
    _stable_id,
    _text,
    _version,
)


CURATION_SCHEMA = "aarvia.jd_curation"
CURATION_SCHEMA_VERSION = 2
CURATION_SCHEMA_V3_VERSION = 3
CURATION_SCHEMA_V4_VERSION = 4
CANDIDATE_ID_VERSION = "requirement-candidate-v1"
CANDIDATE_V4_ID_VERSION = "requirement-candidate-v2"
REVIEW_ID_VERSION = "candidate-review-v1"


class CandidateLifecycleStatus(str, Enum):
    CANDIDATE_EXTRACTED = "candidate_extracted"
    NORMALIZED = "normalized"
    CLUSTERED = "clustered"
    REVIEW_PENDING = "review_pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ClusterLifecycleStatus(str, Enum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class ReviewerDecision(str, Enum):
    PENDING = "pending"
    APPROVE = "approve"
    REJECT = "reject"


class CandidateReviewAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REVISE = "revise"
    SPLIT = "split"


@dataclass(frozen=True, eq=True)
class EvidenceLocator:
    source_content_hash: str
    section: str
    start_offset: int | None = None
    end_offset: int | None = None
    minimal_excerpt: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "evidence") -> EvidenceLocator:
        data = _mapping(value, path)
        _reject_unknown(
            data,
            {"source_content_hash", "section", "start_offset", "end_offset", "minimal_excerpt"},
            path,
        )
        digest = _text(data.get("source_content_hash"), f"{path}.source_content_hash")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise Phase2ValidationError(f"{path}.source_content_hash must be a SHA-256 digest")
        start = data.get("start_offset")
        end = data.get("end_offset")
        for name, item in (("start_offset", start), ("end_offset", end)):
            if item is not None and (not isinstance(item, int) or isinstance(item, bool) or item < 0):
                raise Phase2ValidationError(f"{path}.{name} must be a non-negative integer or null")
        if (start is None) != (end is None) or (start is not None and start >= end):
            raise Phase2ValidationError(f"{path} offsets must be a valid pair")
        excerpt = _text(data.get("minimal_excerpt"), f"{path}.minimal_excerpt", required=False)
        if excerpt is not None and len(excerpt) > 500:
            raise Phase2ValidationError(f"{path}.minimal_excerpt must not exceed 500 characters")
        return cls(digest, _text(data.get("section"), f"{path}.section"), start, end, excerpt)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_content_hash": self.source_content_hash,
            "section": self.section,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "minimal_excerpt": self.minimal_excerpt,
        }


@dataclass(frozen=True, eq=True)
class ExtractionMetadata:
    extractor: str
    provider: str
    model: str
    prompt_version: str
    extracted_at: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "extraction") -> ExtractionMetadata:
        data = _mapping(value, path)
        _reject_unknown(data, {"extractor", "provider", "model", "prompt_version", "extracted_at"}, path)
        return cls(
            extractor=_text(data.get("extractor"), f"{path}.extractor"),
            provider=_text(data.get("provider"), f"{path}.provider"),
            model=_text(data.get("model"), f"{path}.model"),
            prompt_version=_text(data.get("prompt_version"), f"{path}.prompt_version"),
            extracted_at=_iso_datetime(data.get("extracted_at"), f"{path}.extracted_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "extractor": self.extractor,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "extracted_at": self.extracted_at,
        }


def generate_candidate_id(source_id: str, evidence: EvidenceLocator, proposed_name: str) -> str:
    identity = "|".join(
        (
            CANDIDATE_ID_VERSION,
            _stable_id(source_id, "source_id"),
            evidence.source_content_hash,
            evidence.section.casefold().strip(),
            str(evidence.start_offset),
            str(evidence.end_offset),
            proposed_name.casefold().strip(),
        )
    )
    return f"candidate_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


@dataclass(frozen=True, eq=True)
class RequirementCandidate:
    candidate_id: str
    source_id: str
    source_content_hash: str
    evidence: EvidenceLocator
    proposed_name: str
    proposed_description: str
    proposed_category: RequirementCategory
    proposed_importance: RequirementImportance
    mapped_role_id: str
    mapped_specialization_id: str | None
    extraction: ExtractionMetadata
    status: CandidateLifecycleStatus
    cluster_id: str | None
    reviewer_decision: ReviewerDecision
    decision_reason: str | None
    reviewed_at: str | None

    @classmethod
    def from_extraction(
        cls,
        payload: Mapping[str, Any],
        *,
        source_id: str,
        source_content_hash: str,
        extraction: ExtractionMetadata,
    ) -> RequirementCandidate:
        data = _mapping(payload, "provider_candidate")
        allowed = {
            "evidence", "proposed_name", "proposed_description", "proposed_category",
            "proposed_importance", "mapped_role_id", "mapped_specialization_id",
        }
        _reject_unknown(data, allowed, "provider_candidate")
        evidence = EvidenceLocator.from_dict(data.get("evidence"), "provider_candidate.evidence")
        name = _text(data.get("proposed_name"), "provider_candidate.proposed_name")
        candidate = cls(
            candidate_id=generate_candidate_id(source_id, evidence, name),
            source_id=_stable_id(source_id, "source_id"),
            source_content_hash=_text(source_content_hash, "source_content_hash"),
            evidence=evidence,
            proposed_name=name,
            proposed_description=_text(data.get("proposed_description"), "provider_candidate.proposed_description"),
            proposed_category=_enum(data.get("proposed_category"), RequirementCategory, "provider_candidate.proposed_category"),
            proposed_importance=_enum(data.get("proposed_importance"), RequirementImportance, "provider_candidate.proposed_importance"),
            mapped_role_id=_stable_id(data.get("mapped_role_id"), "provider_candidate.mapped_role_id"),
            mapped_specialization_id=(None if data.get("mapped_specialization_id") is None else _stable_id(data.get("mapped_specialization_id"), "provider_candidate.mapped_specialization_id")),
            extraction=extraction,
            status=CandidateLifecycleStatus.CANDIDATE_EXTRACTED,
            cluster_id=None,
            reviewer_decision=ReviewerDecision.PENDING,
            decision_reason=None,
            reviewed_at=None,
        )
        return candidate

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        path: str = "candidate",
        *,
        schema_version: int = CURATION_SCHEMA_VERSION,
    ) -> RequirementCandidate:
        data = _mapping(value, path)
        allowed = {
            "candidate_id", "source_id", "source_content_hash", "evidence", "proposed_name",
            "proposed_description", "proposed_category", "proposed_importance", "mapped_role_id",
            "mapped_specialization_id", "extraction", "status", "cluster_id",
            "reviewer_decision", "decision_reason", "reviewed_at",
        }
        _reject_unknown(data, allowed, path)
        evidence = EvidenceLocator.from_dict(data.get("evidence"), f"{path}.evidence")
        name = _text(data.get("proposed_name"), f"{path}.proposed_name")
        candidate = cls(
            candidate_id=_stable_id(data.get("candidate_id"), f"{path}.candidate_id"),
            source_id=_stable_id(data.get("source_id"), f"{path}.source_id"),
            source_content_hash=_text(data.get("source_content_hash"), f"{path}.source_content_hash"),
            evidence=evidence,
            proposed_name=name,
            proposed_description=_text(data.get("proposed_description"), f"{path}.proposed_description"),
            proposed_category=_enum(data.get("proposed_category"), RequirementCategory, f"{path}.proposed_category"),
            proposed_importance=_enum(data.get("proposed_importance"), RequirementImportance, f"{path}.proposed_importance"),
            mapped_role_id=_stable_id(data.get("mapped_role_id"), f"{path}.mapped_role_id"),
            mapped_specialization_id=(None if data.get("mapped_specialization_id") is None else _stable_id(data.get("mapped_specialization_id"), f"{path}.mapped_specialization_id")),
            extraction=ExtractionMetadata.from_dict(data.get("extraction"), f"{path}.extraction"),
            status=_enum(data.get("status"), CandidateLifecycleStatus, f"{path}.status"),
            cluster_id=(None if data.get("cluster_id") is None else _stable_id(data.get("cluster_id"), f"{path}.cluster_id")),
            reviewer_decision=_enum(data.get("reviewer_decision"), ReviewerDecision, f"{path}.reviewer_decision"),
            decision_reason=_text(data.get("decision_reason"), f"{path}.decision_reason", required=False),
            reviewed_at=(None if data.get("reviewed_at") is None else _iso_datetime(data.get("reviewed_at"), f"{path}.reviewed_at")),
        )
        expected = generate_candidate_id(candidate.source_id, evidence, name)
        if candidate.candidate_id != expected:
            raise Phase2ValidationError(f"{path}.candidate_id was not generated by Aarvia")
        candidate._validate_review(path, schema_version=schema_version)
        return candidate

    def _validate_review(self, path: str, *, schema_version: int) -> None:
        if schema_version not in {
            CURATION_SCHEMA_VERSION,
            CURATION_SCHEMA_V3_VERSION,
            CURATION_SCHEMA_V4_VERSION,
        }:
            raise Phase2ValidationError(f"{path} uses unsupported Curation schema version")
        if (
            schema_version == CURATION_SCHEMA_VERSION
            and self.status == CandidateLifecycleStatus.SUPERSEDED
        ):
            raise Phase2ValidationError(f"{path} schema 2 does not support superseded candidates")
        decided = self.status in {CandidateLifecycleStatus.APPROVED, CandidateLifecycleStatus.REJECTED}
        if decided:
            expected = ReviewerDecision.APPROVE if self.status == CandidateLifecycleStatus.APPROVED else ReviewerDecision.REJECT
            if self.reviewer_decision != expected or self.decision_reason is None or self.reviewed_at is None:
                raise Phase2ValidationError(f"{path} decided candidate requires matching reviewer decision, reason, and time")
        elif self.reviewer_decision != ReviewerDecision.PENDING or self.decision_reason is not None or self.reviewed_at is not None:
            raise Phase2ValidationError(f"{path} undecided candidate cannot contain review approval fields")
        requires_cluster = {
            CandidateLifecycleStatus.CLUSTERED,
            CandidateLifecycleStatus.REVIEW_PENDING,
        }
        if schema_version == CURATION_SCHEMA_VERSION:
            requires_cluster.add(CandidateLifecycleStatus.APPROVED)
        if self.status in requires_cluster and self.cluster_id is None:
            raise Phase2ValidationError(f"{path} clustered candidate requires cluster_id")

    def validate(
        self,
        sources: JDSourceCollection,
        catalog: RoleCatalog,
        *,
        schema_version: int = CURATION_SCHEMA_VERSION,
    ) -> None:
        canonical = RequirementCandidate.from_dict(
            self.to_dict(),
            f"candidate {self.candidate_id}",
            schema_version=schema_version,
        )
        if canonical != self:
            raise Phase2ValidationError(f"candidate {self.candidate_id} is not canonical")
        source = sources.source(self.source_id)
        if source.content_hash is None or source.content_hash != self.source_content_hash:
            raise Phase2ValidationError(f"candidate {self.candidate_id} source content hash does not match")
        if self.evidence.source_content_hash != self.source_content_hash:
            raise Phase2ValidationError(f"candidate {self.candidate_id} evidence hash does not match source")
        role = catalog.role(self.mapped_role_id)
        if self.mapped_specialization_id is not None:
            allowed = {item.specialization_id for item in role.specializations}
            if self.mapped_specialization_id not in allowed:
                raise Phase2ValidationError(f"candidate {self.candidate_id} references unknown specialization")
        self._validate_review(
            f"candidate {self.candidate_id}", schema_version=schema_version
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_id": self.source_id,
            "source_content_hash": self.source_content_hash,
            "evidence": self.evidence.to_dict(),
            "proposed_name": self.proposed_name,
            "proposed_description": self.proposed_description,
            "proposed_category": self.proposed_category.value,
            "proposed_importance": self.proposed_importance.value,
            "mapped_role_id": self.mapped_role_id,
            "mapped_specialization_id": self.mapped_specialization_id,
            "extraction": self.extraction.to_dict(),
            "status": self.status.value,
            "cluster_id": self.cluster_id,
            "reviewer_decision": self.reviewer_decision.value,
            "decision_reason": self.decision_reason,
            "reviewed_at": self.reviewed_at,
        }


def generate_candidate_v4_id(
    source_id: str,
    capture_id: str,
    evidence: EvidenceLocator,
    proposed_name: str,
) -> str:
    identity = "|".join(
        (
            CANDIDATE_V4_ID_VERSION,
            _stable_id(source_id, "source_id"),
            _stable_id(capture_id, "capture_id"),
            evidence.source_content_hash,
            evidence.section.casefold().strip(),
            str(evidence.start_offset),
            str(evidence.end_offset),
            proposed_name.casefold().strip(),
        )
    )
    return f"candidate_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


@dataclass(frozen=True, eq=True)
class RequirementCandidateV4(RequirementCandidate):
    capture_id: str

    @classmethod
    def from_extraction(
        cls,
        payload: Mapping[str, Any],
        *,
        source_id: str,
        capture_id: str,
        source_content_hash: str,
        extraction: ExtractionMetadata,
    ) -> RequirementCandidateV4:
        data = _mapping(payload, "provider_candidate")
        allowed = {
            "evidence", "proposed_name", "proposed_description", "proposed_category",
            "proposed_importance", "mapped_role_id", "mapped_specialization_id",
        }
        _reject_unknown(data, allowed, "provider_candidate")
        evidence = EvidenceLocator.from_dict(data.get("evidence"), "provider_candidate.evidence")
        name = _text(data.get("proposed_name"), "provider_candidate.proposed_name")
        return cls(
            candidate_id=generate_candidate_v4_id(source_id, capture_id, evidence, name),
            source_id=_stable_id(source_id, "source_id"),
            source_content_hash=_text(source_content_hash, "source_content_hash"),
            evidence=evidence,
            proposed_name=name,
            proposed_description=_text(data.get("proposed_description"), "provider_candidate.proposed_description"),
            proposed_category=_enum(data.get("proposed_category"), RequirementCategory, "provider_candidate.proposed_category"),
            proposed_importance=_enum(data.get("proposed_importance"), RequirementImportance, "provider_candidate.proposed_importance"),
            mapped_role_id=_stable_id(data.get("mapped_role_id"), "provider_candidate.mapped_role_id"),
            mapped_specialization_id=(None if data.get("mapped_specialization_id") is None else _stable_id(data.get("mapped_specialization_id"), "provider_candidate.mapped_specialization_id")),
            extraction=extraction,
            status=CandidateLifecycleStatus.CANDIDATE_EXTRACTED,
            cluster_id=None,
            reviewer_decision=ReviewerDecision.PENDING,
            decision_reason=None,
            reviewed_at=None,
            capture_id=_stable_id(capture_id, "capture_id"),
        )

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], path: str = "candidate"
    ) -> RequirementCandidateV4:
        data = _mapping(value, path)
        capture_id = _stable_id(data.get("capture_id"), f"{path}.capture_id")
        provided_candidate_id = _stable_id(
            data.get("candidate_id"), f"{path}.candidate_id"
        )
        legacy_data = dict(data)
        legacy_data.pop("capture_id", None)
        evidence = EvidenceLocator.from_dict(data.get("evidence"), f"{path}.evidence")
        name = _text(data.get("proposed_name"), f"{path}.proposed_name")
        source_id = _stable_id(data.get("source_id"), f"{path}.source_id")
        legacy_data["candidate_id"] = generate_candidate_id(source_id, evidence, name)
        base = RequirementCandidate.from_dict(
            legacy_data, path, schema_version=CURATION_SCHEMA_V4_VERSION
        )
        expected = generate_candidate_v4_id(
            base.source_id, capture_id, base.evidence, base.proposed_name
        )
        if provided_candidate_id != expected:
            raise Phase2ValidationError(f"{path}.candidate_id was not generated by Aarvia")
        return cls(
            **{**base.__dict__, "candidate_id": provided_candidate_id},
            capture_id=capture_id,
        )

    def validate(self, sources: JDSourceCollectionV3, catalog: RoleCatalog) -> None:
        canonical = RequirementCandidateV4.from_dict(
            self.to_dict(), f"candidate {self.candidate_id}"
        )
        if canonical != self:
            raise Phase2ValidationError(f"candidate {self.candidate_id} is not canonical")
        source = sources.source(self.source_id)
        capture = sources.capture(self.capture_id)
        if capture.source_reference != source.source_id:
            raise Phase2ValidationError(
                f"candidate {self.candidate_id} capture belongs to another source"
            )
        if capture.content_hash != self.source_content_hash:
            raise Phase2ValidationError(
                f"candidate {self.candidate_id} source content hash does not match capture"
            )
        if self.evidence.source_content_hash != capture.content_hash:
            raise Phase2ValidationError(
                f"candidate {self.candidate_id} evidence hash does not match capture"
            )
        if self.evidence.start_offset is None or self.evidence.end_offset is None:
            raise Phase2ValidationError(
                f"candidate {self.candidate_id} schema 4 evidence requires explicit offsets"
            )
        if not capture.contains_offsets(
            self.evidence.start_offset, self.evidence.end_offset
        ):
            raise Phase2ValidationError(
                f"candidate {self.candidate_id} evidence offsets are outside capture"
            )
        role = catalog.role(self.mapped_role_id)
        if self.mapped_specialization_id is not None and self.mapped_specialization_id not in {
            item.specialization_id for item in role.specializations
        }:
            raise Phase2ValidationError(
                f"candidate {self.candidate_id} references unknown specialization"
            )
        self._validate_review(
            f"candidate {self.candidate_id}", schema_version=CURATION_SCHEMA_V4_VERSION
        )

    def to_dict(self) -> dict[str, Any]:
        result = super().to_dict()
        result["capture_id"] = self.capture_id
        return result


@dataclass(frozen=True, eq=True)
class RequirementCluster:
    cluster_id: str
    normalized_name: str
    normalized_description: str
    role_id: str
    specialization_id: str | None
    category: RequirementCategory
    importance: RequirementImportance
    candidate_ids: tuple[str, ...]
    status: ClusterLifecycleStatus
    reviewer_decision: ReviewerDecision
    decision_reason: str | None
    reviewed_at: str | None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "cluster") -> RequirementCluster:
        data = _mapping(value, path)
        _reject_unknown(data, {"cluster_id", "normalized_name", "normalized_description", "role_id", "specialization_id", "category", "importance", "candidate_ids", "status", "reviewer_decision", "decision_reason", "reviewed_at"}, path)
        raw_candidate_ids = data.get("candidate_ids")
        if not isinstance(raw_candidate_ids, list):
            raise Phase2ValidationError(f"{path}.candidate_ids must be a list")
        result = cls(
            cluster_id=_stable_id(data.get("cluster_id"), f"{path}.cluster_id"),
            normalized_name=_text(data.get("normalized_name"), f"{path}.normalized_name"),
            normalized_description=_text(data.get("normalized_description"), f"{path}.normalized_description"),
            role_id=_stable_id(data.get("role_id"), f"{path}.role_id"),
            specialization_id=(None if data.get("specialization_id") is None else _stable_id(data.get("specialization_id"), f"{path}.specialization_id")),
            category=_enum(data.get("category"), RequirementCategory, f"{path}.category"),
            importance=_enum(data.get("importance"), RequirementImportance, f"{path}.importance"),
            candidate_ids=tuple(_stable_id(item, f"{path}.candidate_ids") for item in raw_candidate_ids),
            status=_enum(data.get("status"), ClusterLifecycleStatus, f"{path}.status"),
            reviewer_decision=_enum(data.get("reviewer_decision"), ReviewerDecision, f"{path}.reviewer_decision"),
            decision_reason=_text(data.get("decision_reason"), f"{path}.decision_reason", required=False),
            reviewed_at=(None if data.get("reviewed_at") is None else _iso_datetime(data.get("reviewed_at"), f"{path}.reviewed_at")),
        )
        decided = result.status in {ClusterLifecycleStatus.CONFIRMED, ClusterLifecycleStatus.REJECTED}
        expected = ReviewerDecision.APPROVE if result.status == ClusterLifecycleStatus.CONFIRMED else ReviewerDecision.REJECT
        if decided:
            if result.reviewer_decision != expected or result.decision_reason is None or result.reviewed_at is None:
                raise Phase2ValidationError(f"{path} decided cluster requires matching human review metadata")
            if result.status == ClusterLifecycleStatus.CONFIRMED and not result.candidate_ids:
                raise Phase2ValidationError(f"{path} confirmed cluster requires candidates")
        elif result.reviewer_decision != ReviewerDecision.PENDING or result.decision_reason is not None or result.reviewed_at is not None:
            raise Phase2ValidationError(f"{path} proposed cluster cannot contain review approval fields")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "normalized_name": self.normalized_name,
            "normalized_description": self.normalized_description,
            "role_id": self.role_id,
            "specialization_id": self.specialization_id,
            "category": self.category.value,
            "importance": self.importance.value,
            "candidate_ids": list(self.candidate_ids),
            "status": self.status.value,
            "reviewer_decision": self.reviewer_decision.value,
            "decision_reason": self.decision_reason,
            "reviewed_at": self.reviewed_at,
        }


@dataclass(frozen=True, eq=True)
class CurationArtifact:
    artifact_id: str
    artifact_type: CatalogType
    source_collection_id: str
    catalog_version: str
    created_at: str
    candidates: tuple[RequirementCandidate, ...]
    clusters: tuple[RequirementCluster, ...]
    schema: str = CURATION_SCHEMA
    schema_version: int = CURATION_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CurationArtifact:
        data = _mapping(value, "curation")
        _reject_unknown(data, {"schema", "schema_version", "artifact_id", "artifact_type", "source_collection_id", "catalog_version", "created_at", "candidates", "clusters"}, "curation")
        if data.get("schema") != CURATION_SCHEMA or data.get("schema_version") != CURATION_SCHEMA_VERSION:
            raise Phase2ValidationError("unsupported Curation schema version")
        raw_candidates = data.get("candidates")
        raw_clusters = data.get("clusters")
        if not isinstance(raw_candidates, list) or not isinstance(raw_clusters, list):
            raise Phase2ValidationError("curation candidates and clusters must be lists")
        result = cls(
            artifact_id=_stable_id(data.get("artifact_id"), "curation.artifact_id"),
            artifact_type=_enum(data.get("artifact_type"), CatalogType, "curation.artifact_type"),
            source_collection_id=_stable_id(data.get("source_collection_id"), "curation.source_collection_id"),
            catalog_version=_version(data.get("catalog_version"), "curation.catalog_version"),
            created_at=_iso_datetime(data.get("created_at"), "curation.created_at"),
            candidates=tuple(RequirementCandidate.from_dict(item, f"curation.candidates[{index}]") for index, item in enumerate(raw_candidates)),
            clusters=tuple(RequirementCluster.from_dict(item, f"curation.clusters[{index}]") for index, item in enumerate(raw_clusters)),
        )
        return result

    def validate(self, sources: JDSourceCollection, catalog: RoleCatalog) -> None:
        for index, cluster in enumerate(self.clusters):
            if RequirementCluster.from_dict(cluster.to_dict(), f"curation.clusters[{index}]") != cluster:
                raise Phase2ValidationError("Curation artifact contains non-canonical cluster data")
        if self.source_collection_id != sources.collection_id:
            raise Phase2ValidationError("Curation artifact references another Source Collection")
        if self.catalog_version != catalog.catalog_version:
            raise Phase2ValidationError("Curation artifact Catalog version does not match")
        candidate_map = {item.candidate_id: item for item in self.candidates}
        cluster_map = {item.cluster_id: item for item in self.clusters}
        if len(candidate_map) != len(self.candidates) or len(cluster_map) != len(self.clusters):
            raise Phase2ValidationError("Curation artifact contains duplicate IDs")
        if self.artifact_type == CatalogType.PRODUCTION and any(source.is_test_fixture for source in sources.sources):
            raise Phase2ValidationError("production Curation artifact cannot use test fixtures")
        for candidate in self.candidates:
            candidate.validate(sources, catalog)
            if candidate.cluster_id is not None and candidate.cluster_id not in cluster_map:
                raise Phase2ValidationError(f"candidate {candidate.candidate_id} references unknown cluster")
        for cluster in self.clusters:
            role = catalog.role(cluster.role_id)
            if cluster.specialization_id is not None and cluster.specialization_id not in {item.specialization_id for item in role.specializations}:
                raise Phase2ValidationError(f"cluster {cluster.cluster_id} references unknown specialization")
            if len(set(cluster.candidate_ids)) != len(cluster.candidate_ids):
                raise Phase2ValidationError(f"cluster {cluster.cluster_id} contains duplicate candidates")
            for candidate_id in cluster.candidate_ids:
                candidate = candidate_map.get(candidate_id)
                if candidate is None or candidate.cluster_id != cluster.cluster_id:
                    raise Phase2ValidationError(f"cluster {cluster.cluster_id} has inconsistent candidate reference")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type.value,
            "source_collection_id": self.source_collection_id,
            "catalog_version": self.catalog_version,
            "created_at": self.created_at,
            "candidates": [item.to_dict() for item in self.candidates],
            "clusters": [item.to_dict() for item in self.clusters],
        }


@dataclass(frozen=True, eq=True)
class CandidateRevision:
    proposed_name: str
    proposed_description: str
    proposed_category: RequirementCategory
    proposed_importance: RequirementImportance
    evidence: EvidenceLocator | None = None


def generate_review_id(
    *,
    parent_candidate_id: str,
    action: CandidateReviewAction,
    successor_candidate_ids: Sequence[str],
    reviewer_reference: str,
    reviewed_at: str,
    decision_reason: str,
) -> str:
    parent = _stable_id(parent_candidate_id, "parent_candidate_id")
    reviewer = _stable_id(reviewer_reference, "reviewer_reference")
    reviewed = _iso_datetime(reviewed_at, "reviewed_at")
    reason = _text(decision_reason, "decision_reason")
    successors = tuple(
        _stable_id(item, "successor_candidate_id") for item in successor_candidate_ids
    )
    identity = "|".join(
        (
            REVIEW_ID_VERSION,
            parent,
            action.value,
            *successors,
            reviewer,
            reviewed,
            reason,
        )
    )
    return f"review_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


@dataclass(frozen=True, eq=True)
class CandidateReviewRecord:
    review_id: str
    parent_candidate_id: str
    action: CandidateReviewAction
    successor_candidate_ids: tuple[str, ...]
    reviewer_reference: str
    reviewed_at: str
    decision_reason: str

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], path: str = "candidate_review"
    ) -> CandidateReviewRecord:
        data = _mapping(value, path)
        _reject_unknown(
            data,
            {
                "review_id",
                "parent_candidate_id",
                "action",
                "successor_candidate_ids",
                "reviewer_reference",
                "reviewed_at",
                "decision_reason",
            },
            path,
        )
        raw_successors = data.get("successor_candidate_ids")
        if not isinstance(raw_successors, list):
            raise Phase2ValidationError(
                f"{path}.successor_candidate_ids must be a list"
            )
        action = _enum(data.get("action"), CandidateReviewAction, f"{path}.action")
        successors = tuple(
            _stable_id(item, f"{path}.successor_candidate_ids")
            for item in raw_successors
        )
        if len(successors) != len(set(successors)):
            raise Phase2ValidationError(f"{path} contains duplicate successor IDs")
        if action in {CandidateReviewAction.APPROVE, CandidateReviewAction.REJECT}:
            if successors:
                raise Phase2ValidationError(
                    f"{path} {action.value} action cannot contain successors"
                )
        elif action == CandidateReviewAction.REVISE and len(successors) != 1:
            raise Phase2ValidationError(
                f"{path} revise action requires exactly one successor"
            )
        elif action == CandidateReviewAction.SPLIT and len(successors) < 2:
            raise Phase2ValidationError(
                f"{path} split action requires at least two successors"
            )
        parent = _stable_id(data.get("parent_candidate_id"), f"{path}.parent_candidate_id")
        if parent in successors:
            raise Phase2ValidationError(f"{path} parent cannot be its own successor")
        reviewer = _stable_id(data.get("reviewer_reference"), f"{path}.reviewer_reference")
        reviewed = _iso_datetime(data.get("reviewed_at"), f"{path}.reviewed_at")
        reason = _text(data.get("decision_reason"), f"{path}.decision_reason")
        review_id = _stable_id(data.get("review_id"), f"{path}.review_id")
        expected = generate_review_id(
            parent_candidate_id=parent,
            action=action,
            successor_candidate_ids=successors,
            reviewer_reference=reviewer,
            reviewed_at=reviewed,
            decision_reason=reason,
        )
        if review_id != expected:
            raise Phase2ValidationError(f"{path}.review_id was not generated by Aarvia")
        return cls(review_id, parent, action, successors, reviewer, reviewed, reason)

    @classmethod
    def create(
        cls,
        *,
        parent_candidate_id: str,
        action: CandidateReviewAction,
        successor_candidate_ids: Sequence[str],
        reviewer_reference: str,
        reviewed_at: str,
        decision_reason: str,
    ) -> CandidateReviewRecord:
        data = {
            "review_id": generate_review_id(
                parent_candidate_id=parent_candidate_id,
                action=action,
                successor_candidate_ids=successor_candidate_ids,
                reviewer_reference=reviewer_reference,
                reviewed_at=reviewed_at,
                decision_reason=decision_reason,
            ),
            "parent_candidate_id": parent_candidate_id,
            "action": action.value,
            "successor_candidate_ids": list(successor_candidate_ids),
            "reviewer_reference": reviewer_reference,
            "reviewed_at": reviewed_at,
            "decision_reason": decision_reason,
        }
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "parent_candidate_id": self.parent_candidate_id,
            "action": self.action.value,
            "successor_candidate_ids": list(self.successor_candidate_ids),
            "reviewer_reference": self.reviewer_reference,
            "reviewed_at": self.reviewed_at,
            "decision_reason": self.decision_reason,
        }


def _evidence_is_same_or_within(parent: EvidenceLocator, child: EvidenceLocator) -> bool:
    if child.source_content_hash != parent.source_content_hash or child.section != parent.section:
        return False
    if child == parent:
        return True
    if (
        parent.start_offset is None
        or parent.end_offset is None
        or child.start_offset is None
        or child.end_offset is None
    ):
        return False
    return (
        parent.start_offset <= child.start_offset
        and child.end_offset <= parent.end_offset
    )


@dataclass(frozen=True, eq=True)
class CurationArtifactV3:
    artifact_id: str
    artifact_type: CatalogType
    source_collection_id: str
    catalog_version: str
    created_at: str
    candidates: tuple[RequirementCandidate, ...]
    clusters: tuple[RequirementCluster, ...]
    review_records: tuple[CandidateReviewRecord, ...]
    schema: str = CURATION_SCHEMA
    schema_version: int = CURATION_SCHEMA_V3_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CurationArtifactV3:
        data = _mapping(value, "curation")
        _reject_unknown(
            data,
            {
                "schema",
                "schema_version",
                "artifact_id",
                "artifact_type",
                "source_collection_id",
                "catalog_version",
                "created_at",
                "candidates",
                "clusters",
                "review_records",
            },
            "curation",
        )
        if (
            data.get("schema") != CURATION_SCHEMA
            or data.get("schema_version") != CURATION_SCHEMA_V3_VERSION
        ):
            raise Phase2ValidationError("unsupported Curation schema version")
        raw_candidates = data.get("candidates")
        raw_clusters = data.get("clusters")
        raw_reviews = data.get("review_records")
        if not isinstance(raw_candidates, list) or not isinstance(raw_clusters, list):
            raise Phase2ValidationError("curation candidates and clusters must be lists")
        if not isinstance(raw_reviews, list):
            raise Phase2ValidationError("curation review_records must be a list")
        return cls(
            artifact_id=_stable_id(data.get("artifact_id"), "curation.artifact_id"),
            artifact_type=_enum(
                data.get("artifact_type"), CatalogType, "curation.artifact_type"
            ),
            source_collection_id=_stable_id(
                data.get("source_collection_id"), "curation.source_collection_id"
            ),
            catalog_version=_version(
                data.get("catalog_version"), "curation.catalog_version"
            ),
            created_at=_iso_datetime(data.get("created_at"), "curation.created_at"),
            candidates=tuple(
                RequirementCandidate.from_dict(
                    item,
                    f"curation.candidates[{index}]",
                    schema_version=CURATION_SCHEMA_V3_VERSION,
                )
                for index, item in enumerate(raw_candidates)
            ),
            clusters=tuple(
                RequirementCluster.from_dict(item, f"curation.clusters[{index}]")
                for index, item in enumerate(raw_clusters)
            ),
            review_records=tuple(
                CandidateReviewRecord.from_dict(
                    item, f"curation.review_records[{index}]"
                )
                for index, item in enumerate(raw_reviews)
            ),
        )

    def validate(self, sources: JDSourceCollection, catalog: RoleCatalog) -> None:
        canonical = CurationArtifactV3.from_dict(self.to_dict())
        if canonical != self:
            raise Phase2ValidationError("Curation artifact contains non-canonical data")
        if self.source_collection_id != sources.collection_id:
            raise Phase2ValidationError("Curation artifact references another Source Collection")
        if self.catalog_version != catalog.catalog_version:
            raise Phase2ValidationError("Curation artifact Catalog version does not match")
        candidate_map = {item.candidate_id: item for item in self.candidates}
        cluster_map = {item.cluster_id: item for item in self.clusters}
        review_map = {item.review_id: item for item in self.review_records}
        if len(candidate_map) != len(self.candidates):
            raise Phase2ValidationError("Curation artifact contains duplicate candidate IDs")
        if len(cluster_map) != len(self.clusters):
            raise Phase2ValidationError("Curation artifact contains duplicate cluster IDs")
        if len(review_map) != len(self.review_records):
            raise Phase2ValidationError("Curation artifact contains duplicate review IDs")
        if self.artifact_type == CatalogType.PRODUCTION and any(
            source.is_test_fixture for source in sources.sources
        ):
            raise Phase2ValidationError(
                "production Curation artifact cannot use test fixtures"
            )
        for candidate in self.candidates:
            candidate.validate(
                sources, catalog, schema_version=CURATION_SCHEMA_V3_VERSION
            )
            if candidate.cluster_id is not None and candidate.cluster_id not in cluster_map:
                raise Phase2ValidationError(
                    f"candidate {candidate.candidate_id} references unknown cluster"
                )
        for cluster in self.clusters:
            role = catalog.role(cluster.role_id)
            if cluster.specialization_id is not None and cluster.specialization_id not in {
                item.specialization_id for item in role.specializations
            }:
                raise Phase2ValidationError(
                    f"cluster {cluster.cluster_id} references unknown specialization"
                )
            if len(set(cluster.candidate_ids)) != len(cluster.candidate_ids):
                raise Phase2ValidationError(
                    f"cluster {cluster.cluster_id} contains duplicate candidates"
                )
            for candidate_id in cluster.candidate_ids:
                candidate = candidate_map.get(candidate_id)
                if candidate is None or candidate.cluster_id != cluster.cluster_id:
                    raise Phase2ValidationError(
                        f"cluster {cluster.cluster_id} has inconsistent candidate reference"
                    )
        self._validate_lineage(candidate_map)

    def _validate_lineage(
        self, candidate_map: Mapping[str, RequirementCandidate]
    ) -> None:
        review_by_parent: dict[str, CandidateReviewRecord] = {}
        incoming: dict[str, CandidateReviewRecord] = {}
        edges: dict[str, tuple[str, ...]] = {}
        for review in self.review_records:
            parent = candidate_map.get(review.parent_candidate_id)
            if parent is None:
                raise Phase2ValidationError(
                    f"review {review.review_id} references missing parent candidate"
                )
            if review.parent_candidate_id in review_by_parent:
                raise Phase2ValidationError(
                    f"candidate {review.parent_candidate_id} has multiple terminal reviews"
                )
            review_by_parent[review.parent_candidate_id] = review
            edges[review.parent_candidate_id] = review.successor_candidate_ids
            expected_status = {
                CandidateReviewAction.APPROVE: CandidateLifecycleStatus.APPROVED,
                CandidateReviewAction.REJECT: CandidateLifecycleStatus.REJECTED,
                CandidateReviewAction.REVISE: CandidateLifecycleStatus.SUPERSEDED,
                CandidateReviewAction.SPLIT: CandidateLifecycleStatus.SUPERSEDED,
            }[review.action]
            if parent.status != expected_status:
                raise Phase2ValidationError(
                    f"candidate {parent.candidate_id} status contradicts review action"
                )
            if review.action in {
                CandidateReviewAction.APPROVE,
                CandidateReviewAction.REJECT,
            }:
                expected_decision = (
                    ReviewerDecision.APPROVE
                    if review.action == CandidateReviewAction.APPROVE
                    else ReviewerDecision.REJECT
                )
                if (
                    parent.reviewer_decision != expected_decision
                    or parent.decision_reason != review.decision_reason
                    or parent.reviewed_at != review.reviewed_at
                ):
                    raise Phase2ValidationError(
                        f"candidate {parent.candidate_id} review metadata contradicts review record"
                    )
            for successor_id in review.successor_candidate_ids:
                successor = candidate_map.get(successor_id)
                if successor is None:
                    raise Phase2ValidationError(
                        f"review {review.review_id} references missing successor candidate"
                    )
                if successor_id in incoming:
                    raise Phase2ValidationError(
                        f"candidate {successor_id} has multiple direct parents"
                    )
                incoming[successor_id] = review
                if successor.status not in {
                    CandidateLifecycleStatus.APPROVED,
                    CandidateLifecycleStatus.SUPERSEDED,
                }:
                    raise Phase2ValidationError(
                        f"review {review.review_id} successor must be an approved leaf or superseded node"
                    )
                if (
                    successor.source_id != parent.source_id
                    or successor.source_content_hash != parent.source_content_hash
                ):
                    raise Phase2ValidationError(
                        f"review {review.review_id} cannot cross source or content hash"
                    )
                if (
                    successor.mapped_role_id != parent.mapped_role_id
                    or successor.mapped_specialization_id
                    != parent.mapped_specialization_id
                ):
                    raise Phase2ValidationError(
                        f"review {review.review_id} successor has incompatible role mapping"
                    )
                if successor.extraction != parent.extraction:
                    raise Phase2ValidationError(
                        f"review {review.review_id} successor must preserve extraction provenance"
                    )
                if not _evidence_is_same_or_within(parent.evidence, successor.evidence):
                    raise Phase2ValidationError(
                        f"review {review.review_id} successor evidence is outside parent evidence"
                    )
                if successor.status == CandidateLifecycleStatus.APPROVED and (
                    successor.reviewer_decision != ReviewerDecision.APPROVE
                    or successor.decision_reason != review.decision_reason
                    or successor.reviewed_at != review.reviewed_at
                ):
                    raise Phase2ValidationError(
                        f"review {review.review_id} approved successor lacks matching review provenance"
                    )
        CurationArtifactV3._reject_lineage_cycles(edges)
        for candidate in self.candidates:
            own_review = review_by_parent.get(candidate.candidate_id)
            origin_review = incoming.get(candidate.candidate_id)
            if candidate.status == CandidateLifecycleStatus.APPROVED:
                if (own_review is None) == (origin_review is None):
                    raise Phase2ValidationError(
                        f"approved candidate {candidate.candidate_id} requires exactly one terminal provenance"
                    )
                if own_review is not None and own_review.action != CandidateReviewAction.APPROVE:
                    raise Phase2ValidationError(
                        f"approved candidate {candidate.candidate_id} has contradictory review action"
                    )
            elif candidate.status == CandidateLifecycleStatus.REJECTED:
                if (
                    own_review is None
                    or own_review.action != CandidateReviewAction.REJECT
                    or origin_review is not None
                ):
                    raise Phase2ValidationError(
                        f"rejected candidate {candidate.candidate_id} requires one reject review"
                    )
            elif candidate.status == CandidateLifecycleStatus.SUPERSEDED:
                if own_review is None or own_review.action not in {
                    CandidateReviewAction.REVISE,
                    CandidateReviewAction.SPLIT,
                }:
                    raise Phase2ValidationError(
                        f"superseded candidate {candidate.candidate_id} requires revise or split review"
                    )
            elif own_review is not None or origin_review is not None:
                raise Phase2ValidationError(
                    f"non-terminal candidate {candidate.candidate_id} cannot participate in lineage"
                )

    @staticmethod
    def _reject_lineage_cycles(edges: Mapping[str, tuple[str, ...]]) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(candidate_id: str) -> None:
            if candidate_id in visiting:
                raise Phase2ValidationError("Curation candidate lineage contains a cycle")
            if candidate_id in visited:
                return
            visiting.add(candidate_id)
            for successor_id in edges.get(candidate_id, ()):
                visit(successor_id)
            visiting.remove(candidate_id)
            visited.add(candidate_id)

        for candidate_id in edges:
            visit(candidate_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type.value,
            "source_collection_id": self.source_collection_id,
            "catalog_version": self.catalog_version,
            "created_at": self.created_at,
            "candidates": [item.to_dict() for item in self.candidates],
            "clusters": [item.to_dict() for item in self.clusters],
            "review_records": [item.to_dict() for item in self.review_records],
        }


@dataclass(frozen=True, eq=True)
class CurationArtifactV4:
    artifact_id: str
    artifact_type: CatalogType
    source_collection_id: str
    catalog_version: str
    created_at: str
    candidates: tuple[RequirementCandidateV4, ...]
    clusters: tuple[RequirementCluster, ...]
    review_records: tuple[CandidateReviewRecord, ...]
    schema: str = CURATION_SCHEMA
    schema_version: int = CURATION_SCHEMA_V4_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CurationArtifactV4:
        data = _mapping(value, "curation")
        _reject_unknown(
            data,
            {
                "schema", "schema_version", "artifact_id", "artifact_type",
                "source_collection_id", "catalog_version", "created_at",
                "candidates", "clusters", "review_records",
            },
            "curation",
        )
        if data.get("schema") != CURATION_SCHEMA or data.get("schema_version") != CURATION_SCHEMA_V4_VERSION:
            raise Phase2ValidationError("unsupported Curation schema version")
        raw_candidates = data.get("candidates")
        raw_clusters = data.get("clusters")
        raw_reviews = data.get("review_records")
        if not isinstance(raw_candidates, list) or not isinstance(raw_clusters, list) or not isinstance(raw_reviews, list):
            raise Phase2ValidationError("curation candidates, clusters, and review_records must be lists")
        return cls(
            artifact_id=_stable_id(data.get("artifact_id"), "curation.artifact_id"),
            artifact_type=_enum(data.get("artifact_type"), CatalogType, "curation.artifact_type"),
            source_collection_id=_stable_id(data.get("source_collection_id"), "curation.source_collection_id"),
            catalog_version=_version(data.get("catalog_version"), "curation.catalog_version"),
            created_at=_iso_datetime(data.get("created_at"), "curation.created_at"),
            candidates=tuple(RequirementCandidateV4.from_dict(item, f"curation.candidates[{index}]") for index, item in enumerate(raw_candidates)),
            clusters=tuple(RequirementCluster.from_dict(item, f"curation.clusters[{index}]") for index, item in enumerate(raw_clusters)),
            review_records=tuple(CandidateReviewRecord.from_dict(item, f"curation.review_records[{index}]") for index, item in enumerate(raw_reviews)),
        )

    def validate(self, sources: JDSourceCollectionV3, catalog: RoleCatalog) -> None:
        if not isinstance(sources, JDSourceCollectionV3):
            raise Phase2ValidationError("Curation schema 4 requires JD Source schema 3")
        canonical = CurationArtifactV4.from_dict(self.to_dict())
        if canonical != self:
            raise Phase2ValidationError("Curation artifact contains non-canonical data")
        if self.source_collection_id != sources.collection_id:
            raise Phase2ValidationError("Curation artifact references another Source Collection")
        if self.catalog_version != catalog.catalog_version:
            raise Phase2ValidationError("Curation artifact Catalog version does not match")
        candidate_map = {item.candidate_id: item for item in self.candidates}
        cluster_map = {item.cluster_id: item for item in self.clusters}
        review_map = {item.review_id: item for item in self.review_records}
        if len(candidate_map) != len(self.candidates):
            raise Phase2ValidationError("Curation artifact contains duplicate candidate IDs")
        if len(cluster_map) != len(self.clusters):
            raise Phase2ValidationError("Curation artifact contains duplicate cluster IDs")
        if len(review_map) != len(self.review_records):
            raise Phase2ValidationError("Curation artifact contains duplicate review IDs")
        if self.artifact_type == CatalogType.PRODUCTION and any(source.is_test_fixture for source in sources.sources):
            raise Phase2ValidationError("production Curation artifact cannot use test fixtures")
        for candidate in self.candidates:
            candidate.validate(sources, catalog)
            if candidate.cluster_id is not None and candidate.cluster_id not in cluster_map:
                raise Phase2ValidationError(f"candidate {candidate.candidate_id} references unknown cluster")
        for cluster in self.clusters:
            role = catalog.role(cluster.role_id)
            if cluster.specialization_id is not None and cluster.specialization_id not in {
                item.specialization_id for item in role.specializations
            }:
                raise Phase2ValidationError(f"cluster {cluster.cluster_id} references unknown specialization")
            if len(set(cluster.candidate_ids)) != len(cluster.candidate_ids):
                raise Phase2ValidationError(f"cluster {cluster.cluster_id} contains duplicate candidates")
            for candidate_id in cluster.candidate_ids:
                candidate = candidate_map.get(candidate_id)
                if candidate is None or candidate.cluster_id != cluster.cluster_id:
                    raise Phase2ValidationError(f"cluster {cluster.cluster_id} has inconsistent candidate reference")
        CurationArtifactV3._validate_lineage(self, candidate_map)  # type: ignore[arg-type]
        for review in self.review_records:
            parent = candidate_map[review.parent_candidate_id]
            for successor_id in review.successor_candidate_ids:
                if candidate_map[successor_id].capture_id != parent.capture_id:
                    raise Phase2ValidationError(
                        f"review {review.review_id} cannot rebase capture provenance"
                    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type.value,
            "source_collection_id": self.source_collection_id,
            "catalog_version": self.catalog_version,
            "created_at": self.created_at,
            "candidates": [item.to_dict() for item in self.candidates],
            "clusters": [item.to_dict() for item in self.clusters],
            "review_records": [item.to_dict() for item in self.review_records],
        }


def migrate_v3_to_v4(
    value: CurationArtifactV3,
    *,
    sources: JDSourceCollectionV3,
    catalog: RoleCatalog,
) -> CurationArtifactV4:
    if not isinstance(value, CurationArtifactV3):
        raise TypeError("value must be a schema 3 CurationArtifact")
    if CurationArtifactV3.from_dict(value.to_dict()) != value:
        raise Phase2ValidationError("schema 3 Curation artifact is not canonical")
    candidate_ids: dict[str, str] = {}
    migrated_candidates: list[RequirementCandidateV4] = []
    for candidate in value.candidates:
        matches = sources.matching_captures(
            candidate.source_id, candidate.source_content_hash
        )
        if len(matches) != 1:
            raise Phase2ValidationError(
                f"candidate {candidate.candidate_id} migration requires exactly one matching capture"
            )
        capture = matches[0]
        if not capture.contains_offsets(
            candidate.evidence.start_offset, candidate.evidence.end_offset
        ):
            raise Phase2ValidationError(
                f"candidate {candidate.candidate_id} evidence cannot be bounded by capture"
            )
        new_id = generate_candidate_v4_id(
            candidate.source_id,
            capture.capture_id,
            candidate.evidence,
            candidate.proposed_name,
        )
        candidate_ids[candidate.candidate_id] = new_id
        migrated_candidates.append(
            RequirementCandidateV4(
                **{**candidate.__dict__, "candidate_id": new_id},
                capture_id=capture.capture_id,
            )
        )

    def migrated_id(candidate_id: str, path: str) -> str:
        try:
            return candidate_ids[candidate_id]
        except KeyError as error:
            raise Phase2ValidationError(
                f"{path} references a missing Candidate during migration"
            ) from error

    migrated_clusters = tuple(
        replace(
            cluster,
            candidate_ids=tuple(
                migrated_id(item, f"cluster {cluster.cluster_id}")
                for item in cluster.candidate_ids
            ),
        )
        for cluster in value.clusters
    )
    migrated_reviews = tuple(
        CandidateReviewRecord.create(
            parent_candidate_id=migrated_id(
                review.parent_candidate_id, f"review {review.review_id}"
            ),
            action=review.action,
            successor_candidate_ids=tuple(
                migrated_id(item, f"review {review.review_id}")
                for item in review.successor_candidate_ids
            ),
            reviewer_reference=review.reviewer_reference,
            reviewed_at=review.reviewed_at,
            decision_reason=review.decision_reason,
        )
        for review in value.review_records
    )
    result = CurationArtifactV4(
        artifact_id=value.artifact_id,
        artifact_type=value.artifact_type,
        source_collection_id=value.source_collection_id,
        catalog_version=value.catalog_version,
        created_at=value.created_at,
        candidates=tuple(migrated_candidates),
        clusters=migrated_clusters,
        review_records=migrated_reviews,
    )
    result.validate(sources, catalog)
    return result


def migrate_v2_to_v3(value: CurationArtifact) -> CurationArtifactV3:
    if not isinstance(value, CurationArtifact):
        raise TypeError("value must be a schema 2 CurationArtifact")
    if any(
        candidate.status
        in {CandidateLifecycleStatus.APPROVED, CandidateLifecycleStatus.REJECTED}
        for candidate in value.candidates
    ):
        raise Phase2ValidationError(
            "schema 2 terminal candidates lack trusted review records and require re-review"
        )
    data = value.to_dict()
    data["schema_version"] = CURATION_SCHEMA_V3_VERSION
    data["review_records"] = []
    return CurationArtifactV3.from_dict(data)


def _reviewable_candidate(
    artifact: CurationArtifactV3 | CurationArtifactV4,
    candidate_id: str,
    *,
    allow_approved_successor: bool = False,
) -> RequirementCandidate | RequirementCandidateV4:
    matches = [item for item in artifact.candidates if item.candidate_id == candidate_id]
    if not matches:
        raise Phase2ValidationError(f"unknown Candidate ID: {candidate_id}")
    candidate = matches[0]
    has_own_review = any(
        review.parent_candidate_id == candidate_id for review in artifact.review_records
    )
    is_successor = any(
        candidate_id in review.successor_candidate_ids
        for review in artifact.review_records
    )
    if (
        has_own_review
        or candidate.status
        in {CandidateLifecycleStatus.REJECTED, CandidateLifecycleStatus.SUPERSEDED}
        or (
            candidate.status == CandidateLifecycleStatus.APPROVED
            and (not allow_approved_successor or not is_successor)
        )
    ):
        raise Phase2ValidationError(f"candidate {candidate_id} already has a terminal review")
    if candidate.cluster_id is not None:
        cluster = next(
            (item for item in artifact.clusters if item.cluster_id == candidate.cluster_id),
            None,
        )
        if cluster is not None and cluster.status == ClusterLifecycleStatus.CONFIRMED:
            raise Phase2ValidationError(
                f"candidate {candidate_id} belongs to a confirmed cluster and cannot be changed"
            )
    return candidate


def _review_record(
    candidate_id: str,
    action: CandidateReviewAction,
    successor_ids: Sequence[str],
    reviewer_reference: str,
    reviewed_at: str,
    decision_reason: str,
) -> CandidateReviewRecord:
    return CandidateReviewRecord.create(
        parent_candidate_id=candidate_id,
        action=action,
        successor_candidate_ids=successor_ids,
        reviewer_reference=reviewer_reference,
        reviewed_at=reviewed_at,
        decision_reason=decision_reason,
    )


def _finish_review(
    artifact: CurationArtifactV3 | CurationArtifactV4,
    *,
    parent: RequirementCandidate | RequirementCandidateV4,
    reviewed_parent: RequirementCandidate | RequirementCandidateV4,
    successors: Sequence[RequirementCandidate | RequirementCandidateV4],
    review: CandidateReviewRecord,
    sources: JDSourceCollection | JDSourceCollectionV3,
    catalog: RoleCatalog,
) -> CurationArtifactV3 | CurationArtifactV4:
    candidates = tuple(
        reviewed_parent if item.candidate_id == parent.candidate_id else item
        for item in artifact.candidates
    ) + tuple(successors)
    result = replace(
        artifact,
        candidates=candidates,
        review_records=(*artifact.review_records, review),
    )
    result.validate(sources, catalog)
    return result


def approve_candidate(
    artifact: CurationArtifactV3 | CurationArtifactV4,
    candidate_id: str,
    *,
    reviewer_reference: str,
    reviewed_at: str,
    decision_reason: str,
    sources: JDSourceCollection | JDSourceCollectionV3,
    catalog: RoleCatalog,
) -> CurationArtifactV3 | CurationArtifactV4:
    parent = _reviewable_candidate(artifact, candidate_id)
    if parent.cluster_id is None:
        raise Phase2ValidationError("approved candidate must belong to a proposed cluster")
    reviewed_parent = replace(
        parent,
        status=CandidateLifecycleStatus.APPROVED,
        reviewer_decision=ReviewerDecision.APPROVE,
        decision_reason=_text(decision_reason, "decision_reason"),
        reviewed_at=_iso_datetime(reviewed_at, "reviewed_at"),
    )
    review = _review_record(
        candidate_id,
        CandidateReviewAction.APPROVE,
        (),
        reviewer_reference,
        reviewed_at,
        decision_reason,
    )
    return _finish_review(
        artifact,
        parent=parent,
        reviewed_parent=reviewed_parent,
        successors=(),
        review=review,
        sources=sources,
        catalog=catalog,
    )


def reject_candidate(
    artifact: CurationArtifactV3 | CurationArtifactV4,
    candidate_id: str,
    *,
    reviewer_reference: str,
    reviewed_at: str,
    decision_reason: str,
    sources: JDSourceCollection | JDSourceCollectionV3,
    catalog: RoleCatalog,
) -> CurationArtifactV3 | CurationArtifactV4:
    parent = _reviewable_candidate(artifact, candidate_id)
    reviewed_parent = replace(
        parent,
        status=CandidateLifecycleStatus.REJECTED,
        reviewer_decision=ReviewerDecision.REJECT,
        decision_reason=_text(decision_reason, "decision_reason"),
        reviewed_at=_iso_datetime(reviewed_at, "reviewed_at"),
    )
    review = _review_record(
        candidate_id,
        CandidateReviewAction.REJECT,
        (),
        reviewer_reference,
        reviewed_at,
        decision_reason,
    )
    return _finish_review(
        artifact,
        parent=parent,
        reviewed_parent=reviewed_parent,
        successors=(),
        review=review,
        sources=sources,
        catalog=catalog,
    )


def _successor_candidate(
    parent: RequirementCandidate | RequirementCandidateV4,
    revision: CandidateRevision,
    *,
    decision_reason: str,
    reviewed_at: str,
) -> RequirementCandidate | RequirementCandidateV4:
    if not isinstance(revision, CandidateRevision):
        raise TypeError("revision must be a CandidateRevision")
    evidence = revision.evidence or parent.evidence
    name = _text(revision.proposed_name, "revision.proposed_name")
    description = _text(
        revision.proposed_description, "revision.proposed_description"
    )
    category = _enum(
        revision.proposed_category,
        RequirementCategory,
        "revision.proposed_category",
    )
    importance = _enum(
        revision.proposed_importance,
        RequirementImportance,
        "revision.proposed_importance",
    )
    candidate_id = (
        generate_candidate_v4_id(parent.source_id, parent.capture_id, evidence, name)
        if isinstance(parent, RequirementCandidateV4)
        else generate_candidate_id(parent.source_id, evidence, name)
    )
    candidate_type = RequirementCandidateV4 if isinstance(parent, RequirementCandidateV4) else RequirementCandidate
    kwargs: dict[str, Any] = {
        "candidate_id": candidate_id,
        "source_id": parent.source_id,
        "source_content_hash": parent.source_content_hash,
        "evidence": evidence,
        "proposed_name": name,
        "proposed_description": description,
        "proposed_category": category,
        "proposed_importance": importance,
        "mapped_role_id": parent.mapped_role_id,
        "mapped_specialization_id": parent.mapped_specialization_id,
        "extraction": parent.extraction,
        "status": CandidateLifecycleStatus.APPROVED,
        "cluster_id": None,
        "reviewer_decision": ReviewerDecision.APPROVE,
        "decision_reason": _text(decision_reason, "decision_reason"),
        "reviewed_at": _iso_datetime(reviewed_at, "reviewed_at"),
    }
    if isinstance(parent, RequirementCandidateV4):
        kwargs["capture_id"] = parent.capture_id
    return candidate_type(**kwargs)


def _replace_candidate(
    artifact: CurationArtifactV3 | CurationArtifactV4,
    candidate_id: str,
    revisions: Sequence[CandidateRevision],
    *,
    action: CandidateReviewAction,
    reviewer_reference: str,
    reviewed_at: str,
    decision_reason: str,
    sources: JDSourceCollection | JDSourceCollectionV3,
    catalog: RoleCatalog,
) -> CurationArtifactV3 | CurationArtifactV4:
    parent = _reviewable_candidate(
        artifact, candidate_id, allow_approved_successor=True
    )
    successors = tuple(
        _successor_candidate(
            parent,
            revision,
            decision_reason=decision_reason,
            reviewed_at=reviewed_at,
        )
        for revision in revisions
    )
    if len({item.candidate_id for item in successors}) != len(successors):
        raise Phase2ValidationError("review operation produced duplicate successor IDs")
    existing_ids = {item.candidate_id for item in artifact.candidates}
    if any(item.candidate_id in existing_ids for item in successors):
        raise Phase2ValidationError("review operation successor ID already exists")
    reviewed_parent = replace(
        parent,
        status=CandidateLifecycleStatus.SUPERSEDED,
        reviewer_decision=ReviewerDecision.PENDING,
        decision_reason=None,
        reviewed_at=None,
    )
    review = _review_record(
        candidate_id,
        action,
        tuple(item.candidate_id for item in successors),
        reviewer_reference,
        reviewed_at,
        decision_reason,
    )
    return _finish_review(
        artifact,
        parent=parent,
        reviewed_parent=reviewed_parent,
        successors=successors,
        review=review,
        sources=sources,
        catalog=catalog,
    )


def revise_candidate(
    artifact: CurationArtifactV3 | CurationArtifactV4,
    candidate_id: str,
    revision: CandidateRevision,
    *,
    reviewer_reference: str,
    reviewed_at: str,
    decision_reason: str,
    sources: JDSourceCollection | JDSourceCollectionV3,
    catalog: RoleCatalog,
) -> CurationArtifactV3 | CurationArtifactV4:
    return _replace_candidate(
        artifact,
        candidate_id,
        (revision,),
        action=CandidateReviewAction.REVISE,
        reviewer_reference=reviewer_reference,
        reviewed_at=reviewed_at,
        decision_reason=decision_reason,
        sources=sources,
        catalog=catalog,
    )


def split_candidate(
    artifact: CurationArtifactV3 | CurationArtifactV4,
    candidate_id: str,
    revisions: Sequence[CandidateRevision],
    *,
    reviewer_reference: str,
    reviewed_at: str,
    decision_reason: str,
    sources: JDSourceCollection | JDSourceCollectionV3,
    catalog: RoleCatalog,
) -> CurationArtifactV3 | CurationArtifactV4:
    if len(revisions) < 2:
        raise Phase2ValidationError("split action requires at least two successors")
    return _replace_candidate(
        artifact,
        candidate_id,
        tuple(revisions),
        action=CandidateReviewAction.SPLIT,
        reviewer_reference=reviewer_reference,
        reviewed_at=reviewed_at,
        decision_reason=decision_reason,
        sources=sources,
        catalog=catalog,
    )


CurationArtifactType = CurationArtifact | CurationArtifactV3 | CurationArtifactV4


def save_curation_artifact(value: CurationArtifactType, path: str | Path, *, sources: JDSourceCollection | JDSourceCollectionV3, catalog: RoleCatalog) -> Path:
    if not isinstance(value, (CurationArtifact, CurationArtifactV3, CurationArtifactV4)):
        raise TypeError("value must be a CurationArtifact")
    if isinstance(value, CurationArtifactV4) != isinstance(sources, JDSourceCollectionV3):
        raise Phase2ValidationError("Curation schema 4 requires JD Source schema 3; legacy Curation requires schema 2")
    value.validate(sources, catalog)
    from .phase2_storage import save_phase2_json
    if isinstance(value, CurationArtifactV4):
        canonical = CurationArtifactV4.from_dict(value.to_dict())
    elif isinstance(value, CurationArtifactV3):
        canonical = CurationArtifactV3.from_dict(value.to_dict())
    else:
        canonical = CurationArtifact.from_dict(value.to_dict())
    return save_phase2_json(canonical.to_dict(), path)


def load_curation_artifact(path: str | Path, *, sources: JDSourceCollection | JDSourceCollectionV3, catalog: RoleCatalog) -> CurationArtifactType:
    from .phase2_storage import load_phase2_json
    data = _mapping(load_phase2_json(path), "curation")
    schema_version = data.get("schema_version")
    if schema_version == CURATION_SCHEMA_VERSION:
        value: CurationArtifactType = CurationArtifact.from_dict(data)
    elif schema_version == CURATION_SCHEMA_V3_VERSION:
        value = CurationArtifactV3.from_dict(data)
    elif schema_version == CURATION_SCHEMA_V4_VERSION:
        value = CurationArtifactV4.from_dict(data)
    else:
        raise Phase2ValidationError("unsupported Curation schema version")
    if isinstance(value, CurationArtifactV4) != isinstance(sources, JDSourceCollectionV3):
        raise Phase2ValidationError("Curation schema 4 requires JD Source schema 3; legacy Curation requires schema 2")
    value.validate(sources, catalog)
    return value
