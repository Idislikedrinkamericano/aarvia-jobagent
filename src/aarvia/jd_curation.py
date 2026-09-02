"""Schema 2 contracts for requirement extraction candidates and human curation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from pathlib import Path
import re
from typing import Any, Mapping

from .jd_sources import JDSourceCollection
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
CANDIDATE_ID_VERSION = "requirement-candidate-v1"


class CandidateLifecycleStatus(str, Enum):
    CANDIDATE_EXTRACTED = "candidate_extracted"
    NORMALIZED = "normalized"
    CLUSTERED = "clustered"
    REVIEW_PENDING = "review_pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ClusterLifecycleStatus(str, Enum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class ReviewerDecision(str, Enum):
    PENDING = "pending"
    APPROVE = "approve"
    REJECT = "reject"


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
    def from_dict(cls, value: Mapping[str, Any], path: str = "candidate") -> RequirementCandidate:
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
        candidate._validate_review(path)
        return candidate

    def _validate_review(self, path: str) -> None:
        decided = self.status in {CandidateLifecycleStatus.APPROVED, CandidateLifecycleStatus.REJECTED}
        if decided:
            expected = ReviewerDecision.APPROVE if self.status == CandidateLifecycleStatus.APPROVED else ReviewerDecision.REJECT
            if self.reviewer_decision != expected or self.decision_reason is None or self.reviewed_at is None:
                raise Phase2ValidationError(f"{path} decided candidate requires matching reviewer decision, reason, and time")
        elif self.reviewer_decision != ReviewerDecision.PENDING or self.decision_reason is not None or self.reviewed_at is not None:
            raise Phase2ValidationError(f"{path} undecided candidate cannot contain review approval fields")
        if self.status in {CandidateLifecycleStatus.CLUSTERED, CandidateLifecycleStatus.REVIEW_PENDING, CandidateLifecycleStatus.APPROVED} and self.cluster_id is None:
            raise Phase2ValidationError(f"{path} clustered candidate requires cluster_id")

    def validate(self, sources: JDSourceCollection, catalog: RoleCatalog) -> None:
        canonical = RequirementCandidate.from_dict(self.to_dict(), f"candidate {self.candidate_id}")
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
        self._validate_review(f"candidate {self.candidate_id}")

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


def save_curation_artifact(value: CurationArtifact, path: str | Path, *, sources: JDSourceCollection, catalog: RoleCatalog) -> Path:
    value.validate(sources, catalog)
    from .phase2_storage import save_phase2_json
    return save_phase2_json(CurationArtifact.from_dict(value.to_dict()).to_dict(), path)


def load_curation_artifact(path: str | Path, *, sources: JDSourceCollection, catalog: RoleCatalog) -> CurationArtifact:
    from .phase2_storage import load_phase2_json
    value = CurationArtifact.from_dict(load_phase2_json(path))
    value.validate(sources, catalog)
    return value
