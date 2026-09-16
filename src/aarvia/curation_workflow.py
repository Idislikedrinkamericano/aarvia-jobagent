"""Schema 7 curation workflow with audited Cluster and Logic Group decisions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json
from typing import Any, Mapping, Sequence

from .jd_curation import (
    CURATION_SCHEMA,
    CandidateLifecycleStatus,
    CandidateLogicGroup,
    CandidateLogicGroupReviewAction,
    CandidateReviewAction,
    CandidateReviewRecord,
    CurationArtifactV3,
    CurationArtifactV6,
    EvidenceLocator,
    RequirementCandidateV5,
    RequirementClusterV5,
    ReviewerDecision,
    generate_candidate_logic_group_id,
    generate_cluster_v5_id,
)
from .jd_sources import JDSourceCollectionV3, content_sha256, normalize_jd_content
from .requirement_logic import RequirementLogicOperator, RequirementModality
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


CURATION_SCHEMA_V7_VERSION = 7
CLUSTER_REVIEW_ID_VERSION = "cluster-review-v1"
LOGIC_RESOLUTION_ID_VERSION = "logic-resolution-v1"


class ClusterReviewAction(str, Enum):
    CONFIRM = "confirm"
    REJECT = "reject"


class LogicGroupResolutionAction(str, Enum):
    CONFIRM = "confirm"
    QUARANTINE = "quarantine"
    REJECT_MEMBERS = "reject_members"
    RELEASE_MEMBERS = "release_members"
    REVISE = "revise"
    SPLIT = "split"


class LogicGroupStatusV7(str, Enum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    QUARANTINED = "quarantined"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, eq=True)
class ProposedClusterSpec:
    normalized_name: str
    normalized_description: str
    role_id: str
    specialization_id: str | None
    category: RequirementCategory
    importance: RequirementImportance
    candidate_ids: tuple[str, ...]


def _cluster_snapshot(cluster: RequirementClusterV5) -> dict[str, Any]:
    return {
        "cluster_id": cluster.cluster_id,
        "normalized_name": cluster.normalized_name,
        "normalized_description": cluster.normalized_description,
        "role_id": cluster.role_id,
        "specialization_id": cluster.specialization_id,
        "category": cluster.category.value,
        "importance": cluster.importance.value,
        "candidate_ids": list(cluster.candidate_ids),
        "role_assignment_references": list(cluster.role_assignment_references),
    }


def cluster_snapshot_sha256(cluster: RequirementClusterV5) -> str:
    payload = json.dumps(
        _cluster_snapshot(cluster), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def generate_cluster_review_id(
    *,
    cluster_reference: str,
    action: ClusterReviewAction,
    cluster_snapshot_hash: str,
    reviewer_reference: str,
    reviewed_at: str,
    decision_reason: str,
) -> str:
    values = (
        CLUSTER_REVIEW_ID_VERSION,
        _stable_id(cluster_reference, "cluster_reference"),
        action.value,
        _text(cluster_snapshot_hash, "cluster_snapshot_hash"),
        _stable_id(reviewer_reference, "reviewer_reference"),
        _iso_datetime(reviewed_at, "reviewed_at"),
        _text(decision_reason, "decision_reason"),
    )
    return f"cluster_review_{hashlib.sha256('|'.join(values).encode()).hexdigest()[:24]}"


@dataclass(frozen=True, eq=True)
class ClusterReviewRecord:
    review_id: str
    cluster_reference: str
    action: ClusterReviewAction
    cluster_snapshot_hash: str
    reviewer_reference: str
    reviewed_at: str
    decision_reason: str

    @classmethod
    def create(
        cls,
        *,
        cluster: RequirementClusterV5,
        action: ClusterReviewAction,
        reviewer_reference: str,
        reviewed_at: str,
        decision_reason: str,
    ) -> ClusterReviewRecord:
        snapshot = cluster_snapshot_sha256(cluster)
        return cls.from_dict(
            {
                "review_id": generate_cluster_review_id(
                    cluster_reference=cluster.cluster_id,
                    action=action,
                    cluster_snapshot_hash=snapshot,
                    reviewer_reference=reviewer_reference,
                    reviewed_at=reviewed_at,
                    decision_reason=decision_reason,
                ),
                "cluster_reference": cluster.cluster_id,
                "action": action.value,
                "cluster_snapshot_hash": snapshot,
                "reviewer_reference": reviewer_reference,
                "reviewed_at": reviewed_at,
                "decision_reason": decision_reason,
            }
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "cluster_review") -> ClusterReviewRecord:
        data = _mapping(value, path)
        _reject_unknown(
            data,
            {
                "review_id", "cluster_reference", "action", "cluster_snapshot_hash",
                "reviewer_reference", "reviewed_at", "decision_reason",
            },
            path,
        )
        result = cls(
            review_id=_stable_id(data.get("review_id"), f"{path}.review_id"),
            cluster_reference=_stable_id(data.get("cluster_reference"), f"{path}.cluster_reference"),
            action=_enum(data.get("action"), ClusterReviewAction, f"{path}.action"),
            cluster_snapshot_hash=_text(data.get("cluster_snapshot_hash"), f"{path}.cluster_snapshot_hash"),
            reviewer_reference=_stable_id(data.get("reviewer_reference"), f"{path}.reviewer_reference"),
            reviewed_at=_iso_datetime(data.get("reviewed_at"), f"{path}.reviewed_at"),
            decision_reason=_text(data.get("decision_reason"), f"{path}.decision_reason"),
        )
        if not result.cluster_snapshot_hash.startswith("sha256:"):
            raise Phase2ValidationError(f"{path}.cluster_snapshot_hash must be SHA-256")
        expected = generate_cluster_review_id(
            cluster_reference=result.cluster_reference,
            action=result.action,
            cluster_snapshot_hash=result.cluster_snapshot_hash,
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
            "cluster_reference": self.cluster_reference,
            "action": self.action.value,
            "cluster_snapshot_hash": self.cluster_snapshot_hash,
            "reviewer_reference": self.reviewer_reference,
            "reviewed_at": self.reviewed_at,
            "decision_reason": self.decision_reason,
        }


@dataclass(frozen=True, eq=True)
class CandidateLogicGroupV7:
    logic_group_id: str
    operator: RequirementLogicOperator
    member_candidate_references: tuple[str, ...]
    source_reference: str
    capture_reference: str
    source_content_hash: str
    role_assignment_reference: str
    mapped_role_id: str
    mapped_specialization_id: str | None
    evidence: EvidenceLocator
    modality: RequirementModality
    status: LogicGroupStatusV7
    predecessor_group_reference: str | None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "logic_group") -> CandidateLogicGroupV7:
        data = _mapping(value, path)
        _reject_unknown(
            data,
            {
                "logic_group_id", "operator", "member_candidate_references",
                "source_reference", "capture_reference", "source_content_hash",
                "role_assignment_reference", "mapped_role_id",
                "mapped_specialization_id", "evidence", "modality", "status",
                "predecessor_group_reference",
            },
            path,
        )
        raw_members = data.get("member_candidate_references")
        if not isinstance(raw_members, list) or len(raw_members) < 2:
            raise Phase2ValidationError(f"{path} requires at least two members")
        members = tuple(_stable_id(item, f"{path}.member_candidate_references") for item in raw_members)
        if members != tuple(sorted(set(members))):
            raise Phase2ValidationError(f"{path} members must be sorted and unique")
        evidence = EvidenceLocator.from_dict(data.get("evidence"), f"{path}.evidence")
        result = cls(
            logic_group_id=_stable_id(data.get("logic_group_id"), f"{path}.logic_group_id"),
            operator=_enum(data.get("operator"), RequirementLogicOperator, f"{path}.operator"),
            member_candidate_references=members,
            source_reference=_stable_id(data.get("source_reference"), f"{path}.source_reference"),
            capture_reference=_stable_id(data.get("capture_reference"), f"{path}.capture_reference"),
            source_content_hash=_text(data.get("source_content_hash"), f"{path}.source_content_hash"),
            role_assignment_reference=_stable_id(data.get("role_assignment_reference"), f"{path}.role_assignment_reference"),
            mapped_role_id=_stable_id(data.get("mapped_role_id"), f"{path}.mapped_role_id"),
            mapped_specialization_id=None if data.get("mapped_specialization_id") is None else _stable_id(data.get("mapped_specialization_id"), f"{path}.mapped_specialization_id"),
            evidence=evidence,
            modality=_enum(data.get("modality"), RequirementModality, f"{path}.modality"),
            status=_enum(data.get("status"), LogicGroupStatusV7, f"{path}.status"),
            predecessor_group_reference=(
                None
                if data.get("predecessor_group_reference") is None
                else _stable_id(
                    data.get("predecessor_group_reference"),
                    f"{path}.predecessor_group_reference",
                )
            ),
        )
        expected = generate_candidate_logic_group_id(
            operator=result.operator,
            member_candidate_references=result.member_candidate_references,
            source_reference=result.source_reference,
            capture_reference=result.capture_reference,
            source_content_hash=result.source_content_hash,
            evidence_start=result.evidence.start_offset,
            evidence_end=result.evidence.end_offset,
            role_assignment_reference=result.role_assignment_reference,
            modality=result.modality,
        )
        if result.logic_group_id != expected:
            raise Phase2ValidationError(f"{path}.logic_group_id was not generated by Aarvia")
        return result

    @classmethod
    def from_v6(cls, value: CandidateLogicGroup) -> CandidateLogicGroupV7:
        data = value.to_dict()
        data["status"] = value.status.value
        data["predecessor_group_reference"] = None
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "logic_group_id": self.logic_group_id,
            "operator": self.operator.value,
            "member_candidate_references": list(self.member_candidate_references),
            "source_reference": self.source_reference,
            "capture_reference": self.capture_reference,
            "source_content_hash": self.source_content_hash,
            "role_assignment_reference": self.role_assignment_reference,
            "mapped_role_id": self.mapped_role_id,
            "mapped_specialization_id": self.mapped_specialization_id,
            "evidence": self.evidence.to_dict(),
            "modality": self.modality.value,
            "status": self.status.value,
            "predecessor_group_reference": self.predecessor_group_reference,
        }


def generate_logic_resolution_id(
    *,
    logic_group_reference: str,
    action: LogicGroupResolutionAction,
    successor_group_references: Sequence[str],
    reviewer_reference: str,
    reviewed_at: str,
    decision_reason: str,
) -> str:
    successors = tuple(sorted(_stable_id(item, "successor_group_reference") for item in successor_group_references))
    values = (
        LOGIC_RESOLUTION_ID_VERSION,
        _stable_id(logic_group_reference, "logic_group_reference"),
        action.value,
        *successors,
        _stable_id(reviewer_reference, "reviewer_reference"),
        _iso_datetime(reviewed_at, "reviewed_at"),
        _text(decision_reason, "decision_reason"),
    )
    return f"logic_resolution_{hashlib.sha256('|'.join(values).encode()).hexdigest()[:24]}"


@dataclass(frozen=True, eq=True)
class LogicGroupResolutionRecord:
    review_id: str
    logic_group_reference: str
    action: LogicGroupResolutionAction
    successor_group_references: tuple[str, ...]
    reviewer_reference: str
    reviewed_at: str
    decision_reason: str

    @classmethod
    def create(
        cls,
        *,
        logic_group_reference: str,
        action: LogicGroupResolutionAction,
        successor_group_references: Sequence[str] = (),
        reviewer_reference: str,
        reviewed_at: str,
        decision_reason: str,
    ) -> LogicGroupResolutionRecord:
        successors = tuple(sorted(successor_group_references))
        return cls.from_dict(
            {
                "review_id": generate_logic_resolution_id(
                    logic_group_reference=logic_group_reference,
                    action=action,
                    successor_group_references=successors,
                    reviewer_reference=reviewer_reference,
                    reviewed_at=reviewed_at,
                    decision_reason=decision_reason,
                ),
                "logic_group_reference": logic_group_reference,
                "action": action.value,
                "successor_group_references": list(successors),
                "reviewer_reference": reviewer_reference,
                "reviewed_at": reviewed_at,
                "decision_reason": decision_reason,
            }
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "logic_group_review") -> LogicGroupResolutionRecord:
        data = _mapping(value, path)
        _reject_unknown(
            data,
            {
                "review_id", "logic_group_reference", "action",
                "successor_group_references", "reviewer_reference",
                "reviewed_at", "decision_reason",
            },
            path,
        )
        raw_successors = data.get("successor_group_references")
        if not isinstance(raw_successors, list):
            raise Phase2ValidationError(f"{path}.successor_group_references must be a list")
        successors = tuple(_stable_id(item, f"{path}.successor_group_references") for item in raw_successors)
        if successors != tuple(sorted(set(successors))):
            raise Phase2ValidationError(f"{path} successors must be sorted and unique")
        action = _enum(data.get("action"), LogicGroupResolutionAction, f"{path}.action")
        if action == LogicGroupResolutionAction.REVISE and len(successors) != 1:
            raise Phase2ValidationError(f"{path} revise requires exactly one successor")
        if action == LogicGroupResolutionAction.SPLIT and len(successors) < 2:
            raise Phase2ValidationError(f"{path} split requires at least two successors")
        if action not in {LogicGroupResolutionAction.REVISE, LogicGroupResolutionAction.SPLIT} and successors:
            raise Phase2ValidationError(f"{path} action cannot contain successors")
        group = _stable_id(data.get("logic_group_reference"), f"{path}.logic_group_reference")
        reviewer = _stable_id(data.get("reviewer_reference"), f"{path}.reviewer_reference")
        reviewed = _iso_datetime(data.get("reviewed_at"), f"{path}.reviewed_at")
        reason = _text(data.get("decision_reason"), f"{path}.decision_reason")
        review_id = _stable_id(data.get("review_id"), f"{path}.review_id")
        expected = generate_logic_resolution_id(
            logic_group_reference=group,
            action=action,
            successor_group_references=successors,
            reviewer_reference=reviewer,
            reviewed_at=reviewed,
            decision_reason=reason,
        )
        if review_id != expected:
            raise Phase2ValidationError(f"{path}.review_id was not generated by Aarvia")
        return cls(review_id, group, action, successors, reviewer, reviewed, reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "logic_group_reference": self.logic_group_reference,
            "action": self.action.value,
            "successor_group_references": list(self.successor_group_references),
            "reviewer_reference": self.reviewer_reference,
            "reviewed_at": self.reviewed_at,
            "decision_reason": self.decision_reason,
        }


@dataclass(frozen=True, eq=True)
class LogicGroupRevision:
    operator: RequirementLogicOperator
    member_candidate_references: tuple[str, ...]
    evidence: EvidenceLocator
    modality: RequirementModality


@dataclass(frozen=True, eq=True)
class CurationArtifactV7:
    artifact_id: str
    artifact_type: CatalogType
    source_collection_id: str
    catalog_version: str
    created_at: str
    candidates: tuple[RequirementCandidateV5, ...]
    clusters: tuple[RequirementClusterV5, ...]
    review_records: tuple[CandidateReviewRecord, ...]
    role_assignment_artifact_id: str
    logic_groups: tuple[CandidateLogicGroupV7, ...]
    logic_group_review_records: tuple[LogicGroupResolutionRecord, ...]
    cluster_review_records: tuple[ClusterReviewRecord, ...]
    schema: str = CURATION_SCHEMA
    schema_version: int = CURATION_SCHEMA_V7_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CurationArtifactV7:
        data = _mapping(value, "curation")
        _reject_unknown(
            data,
            {
                "schema", "schema_version", "artifact_id", "artifact_type",
                "source_collection_id", "catalog_version", "created_at", "candidates",
                "clusters", "review_records", "role_assignment_artifact_id",
                "logic_groups", "logic_group_review_records", "cluster_review_records",
            },
            "curation",
        )
        if data.get("schema") != CURATION_SCHEMA or data.get("schema_version") != CURATION_SCHEMA_V7_VERSION:
            raise Phase2ValidationError("unsupported Curation schema version")
        list_fields = (
            "candidates", "clusters", "review_records", "logic_groups",
            "logic_group_review_records", "cluster_review_records",
        )
        if any(not isinstance(data.get(name), list) for name in list_fields):
            raise Phase2ValidationError("Curation schema 7 collections must be lists")
        return cls(
            artifact_id=_stable_id(data.get("artifact_id"), "curation.artifact_id"),
            artifact_type=_enum(data.get("artifact_type"), CatalogType, "curation.artifact_type"),
            source_collection_id=_stable_id(data.get("source_collection_id"), "curation.source_collection_id"),
            catalog_version=_version(data.get("catalog_version"), "curation.catalog_version"),
            created_at=_iso_datetime(data.get("created_at"), "curation.created_at"),
            candidates=tuple(RequirementCandidateV5.from_dict(item, f"curation.candidates[{i}]") for i, item in enumerate(data["candidates"])),
            clusters=tuple(RequirementClusterV5.from_dict(item, f"curation.clusters[{i}]") for i, item in enumerate(data["clusters"])),
            review_records=tuple(CandidateReviewRecord.from_dict(item, f"curation.review_records[{i}]") for i, item in enumerate(data["review_records"])),
            role_assignment_artifact_id=_stable_id(data.get("role_assignment_artifact_id"), "curation.role_assignment_artifact_id"),
            logic_groups=tuple(CandidateLogicGroupV7.from_dict(item, f"curation.logic_groups[{i}]") for i, item in enumerate(data["logic_groups"])),
            logic_group_review_records=tuple(LogicGroupResolutionRecord.from_dict(item, f"curation.logic_group_review_records[{i}]") for i, item in enumerate(data["logic_group_review_records"])),
            cluster_review_records=tuple(ClusterReviewRecord.from_dict(item, f"curation.cluster_review_records[{i}]") for i, item in enumerate(data["cluster_review_records"])),
        )

    def validate(self, sources: JDSourceCollectionV3, catalog: RoleCatalog, assignments: Any, capture_contents: Mapping[str, str]) -> None:
        if CurationArtifactV7.from_dict(self.to_dict()) != self:
            raise Phase2ValidationError("Curation schema 7 artifact contains non-canonical data")
        if self.source_collection_id != sources.collection_id or self.source_collection_id != assignments.source_collection_id:
            raise Phase2ValidationError("Curation artifact references another Source Collection")
        if self.catalog_version != catalog.catalog_version or self.catalog_version != assignments.catalog_version:
            raise Phase2ValidationError("Curation artifact Catalog version does not match")
        if self.role_assignment_artifact_id != assignments.artifact_id:
            raise Phase2ValidationError("Curation artifact references another Role Assignment artifact")
        if self.artifact_type == CatalogType.PRODUCTION and any(item.is_test_fixture for item in sources.sources):
            raise Phase2ValidationError("production Curation artifact cannot use test fixtures")
        candidate_map = {item.candidate_id: item for item in self.candidates}
        cluster_map = {item.cluster_id: item for item in self.clusters}
        if len(candidate_map) != len(self.candidates) or len(cluster_map) != len(self.clusters):
            raise Phase2ValidationError("Curation artifact contains duplicate IDs")
        if len({item.review_id for item in self.review_records}) != len(self.review_records):
            raise Phase2ValidationError("Curation artifact contains duplicate Candidate Review IDs")
        for candidate in self.candidates:
            candidate.validate(sources, catalog, assignments)
            if candidate.cluster_id is not None and candidate.cluster_id not in cluster_map:
                raise Phase2ValidationError(f"candidate {candidate.candidate_id} references unknown cluster")
        CurationArtifactV3._validate_lineage(self, candidate_map)  # type: ignore[arg-type]
        active_membership: dict[str, str] = {}
        reviews_by_cluster: dict[str, list[ClusterReviewRecord]] = {}
        for review in self.cluster_review_records:
            if review.cluster_reference not in cluster_map:
                raise Phase2ValidationError(f"cluster review {review.review_id} references unknown Cluster")
            reviews_by_cluster.setdefault(review.cluster_reference, []).append(review)
        for cluster in self.clusters:
            if RequirementClusterV5.from_dict(cluster.to_dict(), f"cluster {cluster.cluster_id}") != cluster:
                raise Phase2ValidationError(f"cluster {cluster.cluster_id} is not canonical")
            catalog.role(cluster.role_id)
            members = []
            for candidate_id in cluster.candidate_ids:
                candidate = candidate_map.get(candidate_id)
                if candidate is None:
                    raise Phase2ValidationError(f"cluster {cluster.cluster_id} references unknown Candidate")
                if candidate.mapped_role_id != cluster.role_id or candidate.mapped_specialization_id != cluster.specialization_id or candidate.proposed_category != cluster.category:
                    raise Phase2ValidationError(f"cluster {cluster.cluster_id} mixes incompatible Candidate semantics")
                members.append(candidate)
            expected_assignments = tuple(sorted({item.role_assignment_reference for item in members}))
            if cluster.role_assignment_references != expected_assignments:
                raise Phase2ValidationError(f"cluster {cluster.cluster_id} Role Assignment references do not match members")
            terminal_reviews = reviews_by_cluster.get(cluster.cluster_id, [])
            if cluster.status == cluster.status.PROPOSED:
                if terminal_reviews or cluster.reviewer_decision != ReviewerDecision.PENDING or cluster.decision_reason is not None or cluster.reviewed_at is not None:
                    raise Phase2ValidationError(f"proposed cluster {cluster.cluster_id} cannot contain terminal review")
                if any(
                    item.status
                    in {
                        CandidateLifecycleStatus.REJECTED,
                        CandidateLifecycleStatus.SUPERSEDED,
                    }
                    for item in members
                ):
                    raise Phase2ValidationError(
                        f"proposed cluster {cluster.cluster_id} contains inactive Candidate"
                    )
            else:
                if len(terminal_reviews) != 1:
                    raise Phase2ValidationError(f"terminal cluster {cluster.cluster_id} requires exactly one Cluster Review")
                review = terminal_reviews[0]
                expected_action = ClusterReviewAction.CONFIRM if cluster.status == cluster.status.CONFIRMED else ClusterReviewAction.REJECT
                if review.action != expected_action or review.cluster_snapshot_hash != cluster_snapshot_sha256(cluster):
                    raise Phase2ValidationError(f"cluster {cluster.cluster_id} Review snapshot or action is invalid")
                expected_decision = ReviewerDecision.APPROVE if expected_action == ClusterReviewAction.CONFIRM else ReviewerDecision.REJECT
                if cluster.reviewer_decision != expected_decision or cluster.decision_reason != review.decision_reason or cluster.reviewed_at != review.reviewed_at:
                    raise Phase2ValidationError(f"cluster {cluster.cluster_id} review metadata is inconsistent")
                if cluster.status == cluster.status.CONFIRMED and any(item.status != CandidateLifecycleStatus.APPROVED for item in members):
                    raise Phase2ValidationError(f"confirmed cluster {cluster.cluster_id} requires approved leaf Candidates")
            if cluster.status in {cluster.status.PROPOSED, cluster.status.CONFIRMED}:
                for candidate in members:
                    if candidate.candidate_id in active_membership:
                        raise Phase2ValidationError(f"candidate {candidate.candidate_id} belongs to multiple current Clusters")
                    active_membership[candidate.candidate_id] = cluster.cluster_id
                    if candidate.cluster_id != cluster.cluster_id:
                        raise Phase2ValidationError(f"cluster {cluster.cluster_id} has inconsistent Candidate reference")
        for candidate in self.candidates:
            expected = active_membership.get(candidate.candidate_id)
            if expected is not None and candidate.cluster_id != expected:
                raise Phase2ValidationError(f"candidate {candidate.candidate_id} current Cluster reference is inconsistent")
            if expected is None and candidate.cluster_id is not None:
                raise Phase2ValidationError(
                    f"candidate {candidate.candidate_id} has a stale Cluster reference"
                )
        self._validate_logic(sources, catalog, assignments, capture_contents, candidate_map)

    def _validate_logic(self, sources, catalog, assignments, capture_contents, candidate_map) -> None:
        group_map = {item.logic_group_id: item for item in self.logic_groups}
        if len(group_map) != len(self.logic_groups):
            raise Phase2ValidationError("Curation artifact contains duplicate Logic Group IDs")
        records_by_group: dict[str, list[LogicGroupResolutionRecord]] = {}
        incoming: dict[str, str] = {}
        edges: dict[str, tuple[str, ...]] = {}
        for record in self.logic_group_review_records:
            if record.logic_group_reference not in group_map:
                raise Phase2ValidationError(f"Logic Group Review {record.review_id} references unknown Group")
            records_by_group.setdefault(record.logic_group_reference, []).append(record)
            if record.action in {LogicGroupResolutionAction.REVISE, LogicGroupResolutionAction.SPLIT}:
                edges[record.logic_group_reference] = record.successor_group_references
                for successor in record.successor_group_references:
                    if successor not in group_map:
                        raise Phase2ValidationError(f"Logic Group Review {record.review_id} references missing successor")
                    if successor in incoming:
                        raise Phase2ValidationError(f"Logic Group {successor} has multiple predecessors")
                    incoming[successor] = record.logic_group_reference
        for group in self.logic_groups:
            if group.predecessor_group_reference != incoming.get(group.logic_group_id):
                raise Phase2ValidationError(
                    f"Logic Group {group.logic_group_id} predecessor lineage is incomplete"
                )
        _reject_cycles(edges, "Logic Group lineage")
        current_members: dict[str, str] = {}
        for group in self.logic_groups:
            _validate_group_structure(group, candidate_map, sources, catalog, assignments, capture_contents, allow_inactive=group.status in {LogicGroupStatusV7.REJECTED, LogicGroupStatusV7.SUPERSEDED})
            records = sorted(records_by_group.get(group.logic_group_id, []), key=lambda item: item.reviewed_at)
            quarantines = [item for item in records if item.action == LogicGroupResolutionAction.QUARANTINE]
            finals = [item for item in records if item.action != LogicGroupResolutionAction.QUARANTINE]
            if len(quarantines) > 1 or len(finals) > 1 or (quarantines and finals and quarantines[0].reviewed_at >= finals[0].reviewed_at):
                raise Phase2ValidationError(f"Logic Group {group.logic_group_id} has invalid resolution history")
            if group.status == LogicGroupStatusV7.PROPOSED and records:
                raise Phase2ValidationError(f"proposed Logic Group {group.logic_group_id} cannot have review")
            if group.status == LogicGroupStatusV7.QUARANTINED and (len(quarantines) != 1 or finals):
                raise Phase2ValidationError(f"quarantined Logic Group {group.logic_group_id} requires one quarantine review")
            if group.status in {LogicGroupStatusV7.CONFIRMED, LogicGroupStatusV7.REJECTED, LogicGroupStatusV7.SUPERSEDED}:
                if len(finals) != 1:
                    raise Phase2ValidationError(f"terminal Logic Group {group.logic_group_id} requires one final resolution")
                action = finals[0].action
                expected = {
                    LogicGroupResolutionAction.CONFIRM: LogicGroupStatusV7.CONFIRMED,
                    LogicGroupResolutionAction.REJECT_MEMBERS: LogicGroupStatusV7.REJECTED,
                    LogicGroupResolutionAction.RELEASE_MEMBERS: LogicGroupStatusV7.SUPERSEDED,
                    LogicGroupResolutionAction.REVISE: LogicGroupStatusV7.SUPERSEDED,
                    LogicGroupResolutionAction.SPLIT: LogicGroupStatusV7.SUPERSEDED,
                }.get(action)
                if group.status != expected:
                    raise Phase2ValidationError(f"Logic Group {group.logic_group_id} status contradicts resolution")
                if action == LogicGroupResolutionAction.REJECT_MEMBERS:
                    for candidate_id in group.member_candidate_references:
                        candidate = candidate_map[candidate_id]
                        if candidate.status != CandidateLifecycleStatus.REJECTED:
                            raise Phase2ValidationError(f"rejected Logic Group {group.logic_group_id} retains active member")
                        review = next((item for item in self.review_records if item.parent_candidate_id == candidate_id), None)
                        if review is None or review.action != CandidateReviewAction.REJECT or review.reviewer_reference != finals[0].reviewer_reference or review.reviewed_at != finals[0].reviewed_at:
                            raise Phase2ValidationError(f"rejected Logic Group {group.logic_group_id} member lacks matching Candidate Review")
                if action in {LogicGroupResolutionAction.REVISE, LogicGroupResolutionAction.SPLIT}:
                    successors = [group_map[item] for item in finals[0].successor_group_references]
                    for successor in successors:
                        if successor.status != LogicGroupStatusV7.PROPOSED or not _same_group_provenance(group, successor) or not _evidence_within(group.evidence, successor.evidence):
                            raise Phase2ValidationError(f"Logic Group {group.logic_group_id} has invalid successor")
                    if action == LogicGroupResolutionAction.SPLIT:
                        flattened = [item for successor in successors for item in successor.member_candidate_references]
                        if len(flattened) != len(set(flattened)) or not set(group.member_candidate_references).issubset(flattened):
                            raise Phase2ValidationError(f"Logic Group {group.logic_group_id} split is partial or overlapping")
            if group.status in {LogicGroupStatusV7.PROPOSED, LogicGroupStatusV7.CONFIRMED, LogicGroupStatusV7.QUARANTINED}:
                for member in group.member_candidate_references:
                    if member in current_members:
                        raise Phase2ValidationError(f"candidate {member} belongs to multiple current Logic Groups")
                    current_members[member] = group.logic_group_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type.value,
            "source_collection_id": self.source_collection_id,
            "catalog_version": self.catalog_version,
            "created_at": self.created_at,
            "role_assignment_artifact_id": self.role_assignment_artifact_id,
            "candidates": [item.to_dict() for item in self.candidates],
            "clusters": [item.to_dict() for item in self.clusters],
            "review_records": [item.to_dict() for item in self.review_records],
            "logic_groups": [item.to_dict() for item in self.logic_groups],
            "logic_group_review_records": [item.to_dict() for item in self.logic_group_review_records],
            "cluster_review_records": [item.to_dict() for item in self.cluster_review_records],
        }


def _reject_cycles(edges: Mapping[str, Sequence[str]], label: str) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(item: str) -> None:
        if item in visiting:
            raise Phase2ValidationError(f"{label} contains a cycle")
        if item in visited:
            return
        visiting.add(item)
        for child in edges.get(item, ()):
            visit(child)
        visiting.remove(item)
        visited.add(item)
    for item in edges:
        visit(item)


def _same_group_provenance(first: CandidateLogicGroupV7, second: CandidateLogicGroupV7) -> bool:
    return (
        first.source_reference == second.source_reference
        and first.capture_reference == second.capture_reference
        and first.source_content_hash == second.source_content_hash
        and first.role_assignment_reference == second.role_assignment_reference
        and first.mapped_role_id == second.mapped_role_id
        and first.mapped_specialization_id == second.mapped_specialization_id
    )


def _evidence_within(parent: EvidenceLocator, child: EvidenceLocator) -> bool:
    return (
        child.source_content_hash == parent.source_content_hash
        and child.section == parent.section
        and parent.start_offset is not None
        and parent.end_offset is not None
        and child.start_offset is not None
        and child.end_offset is not None
        and parent.start_offset <= child.start_offset < child.end_offset <= parent.end_offset
    )


def _validate_group_structure(group, candidates, sources, catalog, assignments, capture_contents, *, allow_inactive):
    if CandidateLogicGroupV7.from_dict(group.to_dict()) != group:
        raise Phase2ValidationError(f"Logic Group {group.logic_group_id} is not canonical")
    source = sources.source(group.source_reference)
    capture = sources.capture(group.capture_reference)
    if capture.source_reference != source.source_id or capture.content_hash != group.source_content_hash or group.evidence.source_content_hash != capture.content_hash:
        raise Phase2ValidationError(f"Logic Group {group.logic_group_id} capture provenance is invalid")
    if not capture.contains_offsets(group.evidence.start_offset, group.evidence.end_offset):
        raise Phase2ValidationError(f"Logic Group {group.logic_group_id} evidence is outside capture")
    raw = capture_contents.get(capture.capture_id)
    if raw is None or content_sha256(raw, version=capture.hash_normalization_version) != capture.content_hash:
        raise Phase2ValidationError(f"Logic Group {group.logic_group_id} capture content is invalid")
    normalized = normalize_jd_content(raw, version=capture.hash_normalization_version)
    if normalized[group.evidence.start_offset:group.evidence.end_offset] != group.evidence.minimal_excerpt:
        raise Phase2ValidationError(f"Logic Group {group.logic_group_id} evidence excerpt is invalid")
    assignment = assignments.current_for_source(group.source_reference, sources)
    if group.role_assignment_reference != assignment.assignment_id or group.mapped_role_id != assignment.role_id or group.mapped_specialization_id != assignment.specialization_id:
        raise Phase2ValidationError(f"Logic Group {group.logic_group_id} Role Assignment is invalid")
    catalog.role(group.mapped_role_id)
    for candidate_id in group.member_candidate_references:
        candidate = candidates.get(candidate_id)
        if candidate is None:
            raise Phase2ValidationError(f"Logic Group {group.logic_group_id} references unknown Candidate")
        if not allow_inactive and candidate.status in {CandidateLifecycleStatus.REJECTED, CandidateLifecycleStatus.SUPERSEDED}:
            raise Phase2ValidationError(f"current Logic Group {group.logic_group_id} references inactive Candidate")
        if candidate.source_id != group.source_reference or candidate.capture_id != group.capture_reference or candidate.source_content_hash != group.source_content_hash or candidate.role_assignment_reference != group.role_assignment_reference:
            raise Phase2ValidationError(f"Logic Group {group.logic_group_id} member provenance is invalid")
        if candidate.evidence.start_offset < group.evidence.start_offset or candidate.evidence.end_offset > group.evidence.end_offset:
            raise Phase2ValidationError(f"Logic Group {group.logic_group_id} does not contain member evidence")


def effective_logic_group_bindings(artifact: CurationArtifactV7) -> tuple[CandidateLogicGroupV7, ...]:
    if not isinstance(artifact, CurationArtifactV7):
        raise TypeError("artifact must be a CurationArtifactV7")
    return tuple(sorted(
        (item for item in artifact.logic_groups if item.status in {
            LogicGroupStatusV7.PROPOSED,
            LogicGroupStatusV7.CONFIRMED,
            LogicGroupStatusV7.QUARANTINED,
        }),
        key=lambda item: item.logic_group_id,
    ))


def _candidate_map(artifact: CurationArtifactV7) -> dict[str, RequirementCandidateV5]:
    return {item.candidate_id: item for item in artifact.candidates}


def _validate_artifact_input(
    artifact: CurationArtifactV7, sources, catalog, assignments, capture_contents
) -> None:
    if not isinstance(artifact, CurationArtifactV7):
        raise TypeError("artifact must be a CurationArtifactV7")
    artifact.validate(sources, catalog, assignments, capture_contents)


def _cluster_spec_cluster(spec: ProposedClusterSpec, candidates: Mapping[str, RequirementCandidateV5]) -> RequirementClusterV5:
    ids = tuple(sorted(_stable_id(item, "candidate_id") for item in spec.candidate_ids))
    if not ids:
        raise Phase2ValidationError("proposed Cluster requires at least one Candidate")
    if len(ids) != len(set(ids)):
        raise Phase2ValidationError("proposed Cluster Candidate IDs must be unique")
    members = []
    for candidate_id in ids:
        candidate = candidates.get(candidate_id)
        if candidate is None:
            raise Phase2ValidationError(f"unknown Candidate ID: {candidate_id}")
        if candidate.status in {CandidateLifecycleStatus.REJECTED, CandidateLifecycleStatus.SUPERSEDED}:
            raise Phase2ValidationError("inactive Candidate cannot enter a proposed Cluster")
        if candidate.mapped_role_id != spec.role_id or candidate.mapped_specialization_id != spec.specialization_id or candidate.proposed_category != spec.category:
            raise Phase2ValidationError("Cluster members must share Role, specialization, and category")
        members.append(candidate)
    assignments = tuple(sorted({item.role_assignment_reference for item in members}))
    cluster_id = generate_cluster_v5_id(
        normalized_name=spec.normalized_name,
        role_id=spec.role_id,
        specialization_id=spec.specialization_id,
        category=spec.category,
        role_assignment_references=assignments,
    )
    return RequirementClusterV5.from_dict({
        "cluster_id": cluster_id,
        "normalized_name": spec.normalized_name,
        "normalized_description": spec.normalized_description,
        "role_id": spec.role_id,
        "specialization_id": spec.specialization_id,
        "category": spec.category.value,
        "importance": spec.importance.value,
        "candidate_ids": list(ids),
        "status": "proposed",
        "reviewer_decision": "pending",
        "decision_reason": None,
        "reviewed_at": None,
        "role_assignment_references": list(assignments),
    })


def _replace_proposed_clusters(
    artifact,
    removed_ids,
    new_clusters,
    candidate_targets,
    *,
    sources,
    catalog,
    assignments,
    capture_contents,
    validate_result=True,
    candidate_statuses=None,
):
    removed = set(removed_ids)
    statuses = candidate_statuses or {}
    result = replace(
        artifact,
        candidates=tuple(
            replace(
                item,
                cluster_id=candidate_targets.get(item.candidate_id, item.cluster_id),
                status=statuses.get(item.candidate_id, item.status),
            )
            for item in artifact.candidates
        ),
        clusters=tuple(item for item in artifact.clusters if item.cluster_id not in removed) + tuple(new_clusters),
    )
    if validate_result:
        result.validate(sources, catalog, assignments, capture_contents)
    return result


def detach_inactive_candidate_for_review(
    artifact: CurationArtifactV7, candidate_id: str
) -> CurationArtifactV7:
    """Detach a newly rejected/superseded Candidate without validating mid-operation."""
    candidate = _candidate_map(artifact).get(candidate_id)
    if candidate is None or candidate.cluster_id is None:
        return artifact
    cluster = next(
        item for item in artifact.clusters if item.cluster_id == candidate.cluster_id
    )
    if cluster.status == cluster.status.CONFIRMED:
        return artifact
    if cluster.status == cluster.status.REJECTED:
        return replace(
            artifact,
            candidates=tuple(
                replace(item, cluster_id=None)
                if item.candidate_id == candidate_id
                else item
                for item in artifact.candidates
            ),
        )
    remaining = tuple(
        item for item in cluster.candidate_ids if item != candidate_id
    )
    replacements: tuple[RequirementClusterV5, ...] = ()
    targets: dict[str, str | None] = {candidate_id: None}
    if remaining:
        spec = ProposedClusterSpec(
            cluster.normalized_name,
            cluster.normalized_description,
            cluster.role_id,
            cluster.specialization_id,
            cluster.category,
            cluster.importance,
            remaining,
        )
        rebuilt = _cluster_spec_cluster(spec, _candidate_map(artifact))
        replacements = (rebuilt,)
        targets.update({item: rebuilt.cluster_id for item in remaining})
    return _replace_proposed_clusters(
        artifact,
        (cluster.cluster_id,),
        replacements,
        targets,
        sources=None,
        catalog=None,
        assignments=None,
        capture_contents=None,
        validate_result=False,
    )


def mark_candidate_normalized(
    artifact: CurationArtifactV7,
    candidate_id: str,
    *,
    sources,
    catalog,
    assignments,
    capture_contents,
) -> CurationArtifactV7:
    _validate_artifact_input(
        artifact, sources, catalog, assignments, capture_contents
    )
    candidate = _candidate_map(artifact).get(candidate_id)
    if candidate is None:
        raise Phase2ValidationError(f"unknown Candidate ID: {candidate_id}")
    if candidate.status != CandidateLifecycleStatus.CANDIDATE_EXTRACTED:
        raise Phase2ValidationError(
            "normalization requires a candidate_extracted Candidate"
        )
    result = replace(
        artifact,
        candidates=tuple(
            replace(item, status=CandidateLifecycleStatus.NORMALIZED)
            if item.candidate_id == candidate_id
            else item
            for item in artifact.candidates
        ),
    )
    result.validate(sources, catalog, assignments, capture_contents)
    return result


def mark_candidate_review_pending(
    artifact: CurationArtifactV7,
    candidate_id: str,
    *,
    sources,
    catalog,
    assignments,
    capture_contents,
) -> CurationArtifactV7:
    _validate_artifact_input(
        artifact, sources, catalog, assignments, capture_contents
    )
    candidate = _candidate_map(artifact).get(candidate_id)
    if candidate is None:
        raise Phase2ValidationError(f"unknown Candidate ID: {candidate_id}")
    if candidate.status != CandidateLifecycleStatus.CLUSTERED:
        raise Phase2ValidationError(
            "review queue requires a clustered Candidate"
        )
    result = replace(
        artifact,
        candidates=tuple(
            replace(item, status=CandidateLifecycleStatus.REVIEW_PENDING)
            if item.candidate_id == candidate_id
            else item
            for item in artifact.candidates
        ),
    )
    result.validate(sources, catalog, assignments, capture_contents)
    return result


def create_proposed_cluster(artifact: CurationArtifactV7, spec: ProposedClusterSpec, *, sources, catalog, assignments, capture_contents) -> CurationArtifactV7:
    _validate_artifact_input(artifact, sources, catalog, assignments, capture_contents)
    candidates = _candidate_map(artifact)
    for candidate_id in spec.candidate_ids:
        current = candidates.get(candidate_id)
        if current is None:
            raise Phase2ValidationError(f"unknown Candidate ID: {candidate_id}")
        if current.cluster_id is not None:
            cluster = next(item for item in artifact.clusters if item.cluster_id == current.cluster_id)
            if cluster.status != cluster.status.REJECTED:
                raise Phase2ValidationError(f"candidate {candidate_id} already belongs to a current Cluster")
    cluster = _cluster_spec_cluster(spec, candidates)
    if any(item.cluster_id == cluster.cluster_id for item in artifact.clusters):
        raise Phase2ValidationError("proposed Cluster deterministic ID already exists")
    statuses = {
        item: CandidateLifecycleStatus.CLUSTERED
        for item in spec.candidate_ids
        if candidates[item].status
        in {
            CandidateLifecycleStatus.CANDIDATE_EXTRACTED,
            CandidateLifecycleStatus.NORMALIZED,
        }
    }
    return _replace_proposed_clusters(artifact, (), (cluster,), {item: cluster.cluster_id for item in spec.candidate_ids}, sources=sources, catalog=catalog, assignments=assignments, capture_contents=capture_contents, candidate_statuses=statuses)


def assign_candidates_to_cluster(artifact: CurationArtifactV7, cluster_id: str, candidate_ids: Sequence[str], *, sources, catalog, assignments, capture_contents) -> CurationArtifactV7:
    _validate_artifact_input(artifact, sources, catalog, assignments, capture_contents)
    cluster = next((item for item in artifact.clusters if item.cluster_id == cluster_id), None)
    if cluster is None or cluster.status != cluster.status.PROPOSED:
        raise Phase2ValidationError("Candidate assignment requires a proposed Cluster")
    candidates = _candidate_map(artifact)
    for candidate_id in candidate_ids:
        candidate = candidates.get(candidate_id)
        if candidate is None:
            raise Phase2ValidationError(f"unknown Candidate ID: {candidate_id}")
        if candidate.cluster_id is not None and candidate.cluster_id != cluster_id:
            existing = next(item for item in artifact.clusters if item.cluster_id == candidate.cluster_id)
            if existing.status != existing.status.REJECTED:
                raise Phase2ValidationError(f"candidate {candidate_id} already belongs to a current Cluster")
    spec = ProposedClusterSpec(cluster.normalized_name, cluster.normalized_description, cluster.role_id, cluster.specialization_id, cluster.category, cluster.importance, tuple(sorted(set(cluster.candidate_ids) | set(candidate_ids))))
    rebuilt = _cluster_spec_cluster(spec, candidates)
    statuses = {
        item: CandidateLifecycleStatus.CLUSTERED
        for item in candidate_ids
        if candidates[item].status
        in {
            CandidateLifecycleStatus.CANDIDATE_EXTRACTED,
            CandidateLifecycleStatus.NORMALIZED,
        }
    }
    return _replace_proposed_clusters(artifact, (cluster_id,), (rebuilt,), {item: rebuilt.cluster_id for item in rebuilt.candidate_ids}, sources=sources, catalog=catalog, assignments=assignments, capture_contents=capture_contents, candidate_statuses=statuses)


def remove_candidates_from_cluster(artifact: CurationArtifactV7, cluster_id: str, candidate_ids: Sequence[str], *, sources, catalog, assignments, capture_contents) -> CurationArtifactV7:
    _validate_artifact_input(artifact, sources, catalog, assignments, capture_contents)
    cluster = next((item for item in artifact.clusters if item.cluster_id == cluster_id), None)
    if cluster is None or cluster.status != cluster.status.PROPOSED:
        raise Phase2ValidationError("Candidate removal requires a proposed Cluster")
    removal = set(candidate_ids)
    if not removal or not removal.issubset(cluster.candidate_ids):
        raise Phase2ValidationError("Candidate removal must identify current Cluster members")
    remaining = tuple(item for item in cluster.candidate_ids if item not in removal)
    targets = {item: None for item in removal}
    replacements = ()
    if remaining:
        spec = ProposedClusterSpec(cluster.normalized_name, cluster.normalized_description, cluster.role_id, cluster.specialization_id, cluster.category, cluster.importance, remaining)
        rebuilt = _cluster_spec_cluster(spec, _candidate_map(artifact))
        replacements = (rebuilt,)
        targets.update({item: rebuilt.cluster_id for item in remaining})
    statuses = {
        item: CandidateLifecycleStatus.NORMALIZED
        for item in removal
        if _candidate_map(artifact)[item].status
        in {
            CandidateLifecycleStatus.CLUSTERED,
            CandidateLifecycleStatus.REVIEW_PENDING,
        }
    }
    return _replace_proposed_clusters(artifact, (cluster_id,), replacements, targets, sources=sources, catalog=catalog, assignments=assignments, capture_contents=capture_contents, candidate_statuses=statuses)


def merge_proposed_clusters(artifact: CurationArtifactV7, cluster_ids: Sequence[str], spec: ProposedClusterSpec, *, sources, catalog, assignments, capture_contents) -> CurationArtifactV7:
    _validate_artifact_input(artifact, sources, catalog, assignments, capture_contents)
    ids = tuple(sorted(set(cluster_ids)))
    if len(ids) < 2:
        raise Phase2ValidationError("Cluster merge requires at least two proposed Clusters")
    clusters = [next((item for item in artifact.clusters if item.cluster_id == cluster_id), None) for cluster_id in ids]
    if any(item is None or item.status != item.status.PROPOSED for item in clusters):
        raise Phase2ValidationError("Cluster merge accepts only proposed Clusters")
    members = {candidate for cluster in clusters for candidate in cluster.candidate_ids}
    if set(spec.candidate_ids) != members:
        raise Phase2ValidationError("merged Cluster must contain every source Cluster member")
    rebuilt = _cluster_spec_cluster(spec, _candidate_map(artifact))
    return _replace_proposed_clusters(artifact, ids, (rebuilt,), {item: rebuilt.cluster_id for item in members}, sources=sources, catalog=catalog, assignments=assignments, capture_contents=capture_contents)


def split_proposed_cluster(artifact: CurationArtifactV7, cluster_id: str, specs: Sequence[ProposedClusterSpec], *, sources, catalog, assignments, capture_contents) -> CurationArtifactV7:
    _validate_artifact_input(artifact, sources, catalog, assignments, capture_contents)
    cluster = next((item for item in artifact.clusters if item.cluster_id == cluster_id), None)
    if cluster is None or cluster.status != cluster.status.PROPOSED or len(specs) < 2:
        raise Phase2ValidationError("Cluster split requires one proposed Cluster and at least two parts")
    flattened = [candidate for spec in specs for candidate in spec.candidate_ids]
    if len(flattened) != len(set(flattened)) or set(flattened) != set(cluster.candidate_ids):
        raise Phase2ValidationError("Cluster split parts must exactly partition current members")
    candidates = _candidate_map(artifact)
    replacements = tuple(_cluster_spec_cluster(spec, candidates) for spec in specs)
    if len({item.cluster_id for item in replacements}) != len(replacements):
        raise Phase2ValidationError("Cluster split produced duplicate deterministic IDs")
    targets = {candidate: item.cluster_id for item in replacements for candidate in item.candidate_ids}
    return _replace_proposed_clusters(artifact, (cluster_id,), replacements, targets, sources=sources, catalog=catalog, assignments=assignments, capture_contents=capture_contents)


def _review_cluster(artifact, cluster_id, action, *, reviewer_reference, reviewed_at, decision_reason, sources, catalog, assignments, capture_contents):
    _validate_artifact_input(artifact, sources, catalog, assignments, capture_contents)
    cluster = next((item for item in artifact.clusters if item.cluster_id == cluster_id), None)
    if cluster is None or cluster.status != cluster.status.PROPOSED:
        raise Phase2ValidationError("Cluster review requires a proposed Cluster")
    cmap = _candidate_map(artifact)
    if action == ClusterReviewAction.CONFIRM and any(cmap[item].status != CandidateLifecycleStatus.APPROVED for item in cluster.candidate_ids):
        raise Phase2ValidationError("confirmed Cluster requires approved leaf Candidates")
    status = cluster.status.CONFIRMED if action == ClusterReviewAction.CONFIRM else cluster.status.REJECTED
    decision = ReviewerDecision.APPROVE if action == ClusterReviewAction.CONFIRM else ReviewerDecision.REJECT
    reviewed = replace(cluster, status=status, reviewer_decision=decision, decision_reason=_text(decision_reason, "decision_reason"), reviewed_at=_iso_datetime(reviewed_at, "reviewed_at"))
    review = ClusterReviewRecord.create(cluster=reviewed, action=action, reviewer_reference=reviewer_reference, reviewed_at=reviewed_at, decision_reason=decision_reason)
    candidates = artifact.candidates
    if action == ClusterReviewAction.REJECT:
        member_ids = set(cluster.candidate_ids)
        candidates = tuple(
            replace(
                item,
                cluster_id=None,
                status=(
                    CandidateLifecycleStatus.NORMALIZED
                    if item.status
                    in {
                        CandidateLifecycleStatus.CLUSTERED,
                        CandidateLifecycleStatus.REVIEW_PENDING,
                    }
                    else item.status
                ),
            )
            if item.candidate_id in member_ids
            else item
            for item in artifact.candidates
        )
    result = replace(
        artifact,
        candidates=candidates,
        clusters=tuple(
            reviewed if item.cluster_id == cluster_id else item
            for item in artifact.clusters
        ),
        cluster_review_records=(*artifact.cluster_review_records, review),
    )
    result.validate(sources, catalog, assignments, capture_contents)
    return result


def confirm_cluster(artifact, cluster_id, **context):
    return _review_cluster(artifact, cluster_id, ClusterReviewAction.CONFIRM, **context)


def reject_cluster(artifact, cluster_id, **context):
    return _review_cluster(artifact, cluster_id, ClusterReviewAction.REJECT, **context)


def _group_by_id(artifact, group_id):
    group = next((item for item in artifact.logic_groups if item.logic_group_id == group_id), None)
    if group is None:
        raise Phase2ValidationError(f"unknown Logic Group ID: {group_id}")
    return group


def _resolve_group(artifact, group_id, action, *, successors=(), reviewer_reference, reviewed_at, decision_reason, sources, catalog, assignments, capture_contents):
    _validate_artifact_input(artifact, sources, catalog, assignments, capture_contents)
    group = _group_by_id(artifact, group_id)
    if group.status not in {LogicGroupStatusV7.PROPOSED, LogicGroupStatusV7.QUARANTINED}:
        raise Phase2ValidationError("Logic Group resolution requires proposed or quarantined Group")
    if group.status == LogicGroupStatusV7.QUARANTINED and action == LogicGroupResolutionAction.QUARANTINE:
        raise Phase2ValidationError("Logic Group is already quarantined")
    successor_ids = tuple(item.logic_group_id for item in successors)
    record = LogicGroupResolutionRecord.create(logic_group_reference=group_id, action=action, successor_group_references=successor_ids, reviewer_reference=reviewer_reference, reviewed_at=reviewed_at, decision_reason=decision_reason)
    status = {
        LogicGroupResolutionAction.CONFIRM: LogicGroupStatusV7.CONFIRMED,
        LogicGroupResolutionAction.QUARANTINE: LogicGroupStatusV7.QUARANTINED,
        LogicGroupResolutionAction.REJECT_MEMBERS: LogicGroupStatusV7.REJECTED,
        LogicGroupResolutionAction.RELEASE_MEMBERS: LogicGroupStatusV7.SUPERSEDED,
        LogicGroupResolutionAction.REVISE: LogicGroupStatusV7.SUPERSEDED,
        LogicGroupResolutionAction.SPLIT: LogicGroupStatusV7.SUPERSEDED,
    }[action]
    result = replace(artifact, logic_groups=tuple(replace(item, status=status) if item.logic_group_id == group_id else item for item in artifact.logic_groups) + tuple(successors), logic_group_review_records=(*artifact.logic_group_review_records, record))
    result.validate(sources, catalog, assignments, capture_contents)
    return result


def confirm_candidate_logic_group_v7(artifact, group_id, **context):
    return _resolve_group(artifact, group_id, LogicGroupResolutionAction.CONFIRM, **context)


def quarantine_candidate_logic_group(artifact, group_id, **context):
    return _resolve_group(artifact, group_id, LogicGroupResolutionAction.QUARANTINE, **context)


def release_logic_group_members(artifact, group_id, **context):
    return _resolve_group(artifact, group_id, LogicGroupResolutionAction.RELEASE_MEMBERS, **context)


def _candidate_reject_record(candidate_id, reviewer_reference, reviewed_at, decision_reason):
    return CandidateReviewRecord.create(parent_candidate_id=candidate_id, action=CandidateReviewAction.REJECT, successor_candidate_ids=(), reviewer_reference=reviewer_reference, reviewed_at=reviewed_at, decision_reason=decision_reason)


def reject_logic_group_and_members(artifact, group_id, *, reviewer_reference, reviewed_at, decision_reason, sources, catalog, assignments, capture_contents):
    _validate_artifact_input(artifact, sources, catalog, assignments, capture_contents)
    group = _group_by_id(artifact, group_id)
    if group.status not in {LogicGroupStatusV7.PROPOSED, LogicGroupStatusV7.QUARANTINED}:
        raise Phase2ValidationError("Logic Group member rejection requires proposed or quarantined Group")
    member_ids = set(group.member_candidate_references)
    cmap = _candidate_map(artifact)
    for candidate_id in member_ids:
        candidate = cmap[candidate_id]
        if candidate.status in {CandidateLifecycleStatus.APPROVED, CandidateLifecycleStatus.REJECTED, CandidateLifecycleStatus.SUPERSEDED} or any(review.parent_candidate_id == candidate_id for review in artifact.review_records):
            raise Phase2ValidationError("Logic Group member is not independently reviewable")
        if candidate.cluster_id is not None:
            cluster = next(item for item in artifact.clusters if item.cluster_id == candidate.cluster_id)
            if cluster.status != cluster.status.PROPOSED:
                raise Phase2ValidationError("Logic Group member belongs to a terminal Cluster")
    working = artifact
    for cluster in tuple(working.clusters):
        selected = member_ids.intersection(cluster.candidate_ids)
        if selected:
            remaining = tuple(item for item in cluster.candidate_ids if item not in selected)
            targets = {item: None for item in selected}
            replacements = ()
            if remaining:
                spec = ProposedClusterSpec(
                    cluster.normalized_name, cluster.normalized_description,
                    cluster.role_id, cluster.specialization_id, cluster.category,
                    cluster.importance, remaining,
                )
                rebuilt = _cluster_spec_cluster(spec, _candidate_map(working))
                replacements = (rebuilt,)
                targets.update({item: rebuilt.cluster_id for item in remaining})
            working = _replace_proposed_clusters(
                working, (cluster.cluster_id,), replacements, targets,
                sources=sources, catalog=catalog, assignments=assignments,
                capture_contents=capture_contents, validate_result=False,
            )
    reason = _text(decision_reason, "decision_reason")
    reviewed = _iso_datetime(reviewed_at, "reviewed_at")
    candidates = tuple(replace(item, status=CandidateLifecycleStatus.REJECTED, cluster_id=None, reviewer_decision=ReviewerDecision.REJECT, decision_reason=reason, reviewed_at=reviewed) if item.candidate_id in member_ids else item for item in working.candidates)
    candidate_reviews = tuple(_candidate_reject_record(item, reviewer_reference, reviewed_at, decision_reason) for item in sorted(member_ids))
    record = LogicGroupResolutionRecord.create(logic_group_reference=group_id, action=LogicGroupResolutionAction.REJECT_MEMBERS, reviewer_reference=reviewer_reference, reviewed_at=reviewed_at, decision_reason=decision_reason)
    result = replace(working, candidates=candidates, review_records=(*working.review_records, *candidate_reviews), logic_groups=tuple(replace(item, status=LogicGroupStatusV7.REJECTED) if item.logic_group_id == group_id else item for item in working.logic_groups), logic_group_review_records=(*working.logic_group_review_records, record))
    result.validate(sources, catalog, assignments, capture_contents)
    return result


def _successor_group(parent: CandidateLogicGroupV7, revision: LogicGroupRevision) -> CandidateLogicGroupV7:
    data = {
        "logic_group_id": generate_candidate_logic_group_id(operator=revision.operator, member_candidate_references=revision.member_candidate_references, source_reference=parent.source_reference, capture_reference=parent.capture_reference, source_content_hash=parent.source_content_hash, evidence_start=revision.evidence.start_offset, evidence_end=revision.evidence.end_offset, role_assignment_reference=parent.role_assignment_reference, modality=revision.modality),
        "operator": revision.operator.value,
        "member_candidate_references": list(sorted(revision.member_candidate_references)),
        "source_reference": parent.source_reference,
        "capture_reference": parent.capture_reference,
        "source_content_hash": parent.source_content_hash,
        "role_assignment_reference": parent.role_assignment_reference,
        "mapped_role_id": parent.mapped_role_id,
        "mapped_specialization_id": parent.mapped_specialization_id,
        "evidence": revision.evidence.to_dict(),
        "modality": revision.modality.value,
        "status": "proposed",
        "predecessor_group_reference": parent.logic_group_id,
    }
    return CandidateLogicGroupV7.from_dict(data)


def revise_candidate_logic_group(artifact, group_id, revision: LogicGroupRevision, **context):
    parent = _group_by_id(artifact, group_id)
    return _resolve_group(artifact, group_id, LogicGroupResolutionAction.REVISE, successors=(_successor_group(parent, revision),), **context)


def split_candidate_logic_group(artifact, group_id, revisions: Sequence[LogicGroupRevision], **context):
    if len(revisions) < 2:
        raise Phase2ValidationError("Logic Group split requires at least two successors")
    parent = _group_by_id(artifact, group_id)
    successors = tuple(_successor_group(parent, item) for item in revisions)
    if len({item.logic_group_id for item in successors}) != len(successors):
        raise Phase2ValidationError("Logic Group split produced duplicate successors")
    return _resolve_group(artifact, group_id, LogicGroupResolutionAction.SPLIT, successors=successors, **context)


def migrate_curation_v6_to_v7(value: CurationArtifactV6, *, sources, catalog, assignments, capture_contents, rejected_group_resolutions: Mapping[str, LogicGroupResolutionAction] | None = None) -> CurationArtifactV7:
    if not isinstance(value, CurationArtifactV6):
        raise TypeError("value must be a CurationArtifactV6")
    value.validate(sources, catalog, assignments, capture_contents)
    if any(item.status != item.status.PROPOSED for item in value.clusters):
        raise Phase2ValidationError("terminal schema 6 Clusters lack trusted Cluster Review provenance")
    old_reviews = {item.logic_group_reference: item for item in value.logic_group_review_records}
    resolutions = rejected_group_resolutions or {}
    groups = []
    reviews = []
    for group in value.logic_groups:
        converted = CandidateLogicGroupV7.from_v6(group)
        if group.status.value == "proposed":
            groups.append(converted)
            continue
        old = old_reviews[group.logic_group_id]
        if group.status.value == "confirmed":
            action = LogicGroupResolutionAction.CONFIRM
            status = LogicGroupStatusV7.CONFIRMED
        else:
            action = resolutions.get(group.logic_group_id)
            if action not in {LogicGroupResolutionAction.QUARANTINE, LogicGroupResolutionAction.RELEASE_MEMBERS}:
                raise Phase2ValidationError("rejected schema 6 Logic Group requires explicit non-fabricated resolution")
            status = LogicGroupStatusV7.QUARANTINED if action == LogicGroupResolutionAction.QUARANTINE else LogicGroupStatusV7.SUPERSEDED
        groups.append(replace(converted, status=status))
        reviews.append(LogicGroupResolutionRecord.create(logic_group_reference=group.logic_group_id, action=action, reviewer_reference=old.reviewer_reference, reviewed_at=old.reviewed_at, decision_reason=old.decision_reason))
    result = CurationArtifactV7(
        artifact_id=value.artifact_id,
        artifact_type=value.artifact_type,
        source_collection_id=value.source_collection_id,
        catalog_version=value.catalog_version,
        created_at=value.created_at,
        candidates=value.candidates,
        clusters=value.clusters,
        review_records=value.review_records,
        role_assignment_artifact_id=value.role_assignment_artifact_id,
        logic_groups=tuple(groups),
        logic_group_review_records=tuple(reviews),
        cluster_review_records=(),
    )
    result.validate(sources, catalog, assignments, capture_contents)
    return result
