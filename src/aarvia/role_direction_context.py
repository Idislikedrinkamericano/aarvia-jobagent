"""Cross-artifact validation and controlled Role reclassification."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from .jd_curation import (
    CandidateLifecycleStatus,
    ClusterLifecycleStatus,
    CurationArtifactV5,
    CurationArtifactV6,
    RequirementClusterV5,
    generate_cluster_v5_id,
)
from .jd_sources import JDSourceCollectionV3
from .live_jobs import LiveJobCollectionV4
from .role_assignments import (
    RoleAssignmentArtifact,
    RoleEvidenceReference,
    add_reclassified_assignment,
)
from .role_catalog import Phase2ValidationError, RoleCatalog


class RoleReclassificationBlockerCode(str, Enum):
    TERMINAL_CANDIDATE = "terminal_candidate"
    CANDIDATE_REVIEW_EXISTS = "candidate_review_exists"
    CANDIDATE_LINEAGE_EXISTS = "candidate_lineage_exists"
    CONFIRMED_CLUSTER = "confirmed_cluster"
    NON_PROPOSED_CLUSTER = "non_proposed_cluster"
    MIXED_JOB_CLUSTER = "mixed_job_cluster"
    CONFLICTING_ASSIGNMENT = "conflicting_current_assignment"
    AMBIGUOUS_CLUSTER_REBUILD = "ambiguous_cluster_rebuild"


@dataclass(frozen=True, eq=True)
class RoleReclassificationBlocker:
    code: RoleReclassificationBlockerCode
    message: str
    references: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "references": list(self.references),
        }


@dataclass(frozen=True, eq=True)
class RoleReclassificationResult:
    assignments: RoleAssignmentArtifact | None
    live_jobs: LiveJobCollectionV4 | None
    curation: CurationArtifactV5 | CurationArtifactV6 | None
    invalidated_derived_references: tuple[str, ...]
    blockers: tuple[RoleReclassificationBlocker, ...]

    @property
    def succeeded(self) -> bool:
        return not self.blockers and self.assignments is not None


def validate_role_direction_context(
    *,
    assignments: RoleAssignmentArtifact,
    live_jobs: LiveJobCollectionV4,
    curation: Any,
    sources: JDSourceCollectionV3,
    catalog: RoleCatalog,
    capture_contents: Mapping[str, str],
) -> None:
    if not isinstance(assignments, RoleAssignmentArtifact):
        raise TypeError("assignments must be a RoleAssignmentArtifact")
    if not isinstance(live_jobs, LiveJobCollectionV4):
        raise Phase2ValidationError("Role direction context requires Live Job schema 4")
    from .curation_workflow import CurationArtifactV7

    if not isinstance(curation, (CurationArtifactV5, CurationArtifactV7)):
        raise Phase2ValidationError(
            "Role direction context requires Curation schema 5, 6, or 7"
        )
    assignments.validate(sources, catalog, capture_contents)
    live_jobs.validate(catalog, sources, assignments)
    if isinstance(curation, (CurationArtifactV6, CurationArtifactV7)):
        curation.validate(sources, catalog, assignments, capture_contents)
    else:
        curation.validate(sources, catalog, assignments)
    if live_jobs.role_assignment_artifact_id != assignments.artifact_id or curation.role_assignment_artifact_id != assignments.artifact_id:
        raise Phase2ValidationError("Role direction artifacts reference different assignments")
    job_ids = {item.canonical_job_id for item in live_jobs.jobs}
    current_ids = {
        item.canonical_job_id
        for item in assignments.assignments
        if item.status.value == "current"
    }
    if job_ids != current_ids:
        raise Phase2ValidationError("Live Jobs and current Role Assignments do not cover the same canonical jobs")
    for candidate in curation.candidates:
        source = sources.source(candidate.source_id)
        if source.canonical_job_id not in job_ids:
            raise Phase2ValidationError(
                f"candidate {candidate.candidate_id} has no Live Job in Role direction context"
            )


def _candidate_job_id(candidate: Any, sources: JDSourceCollectionV3) -> str:
    job_id = sources.source(candidate.source_id).canonical_job_id
    if job_id is None:
        raise Phase2ValidationError(
            f"candidate {candidate.candidate_id} source has no canonical job"
        )
    return job_id


def reclassify_job_role(
    *,
    assignments: RoleAssignmentArtifact,
    live_jobs: LiveJobCollectionV4,
    curation: CurationArtifactV5 | CurationArtifactV6,
    sources: JDSourceCollectionV3,
    catalog: RoleCatalog,
    capture_contents: Mapping[str, str],
    current_assignment_id: str,
    new_role_id: str,
    new_specialization_id: str | None,
    evidence_references: Sequence[RoleEvidenceReference],
    reviewer_reference: str,
    reviewed_at: str,
    decision_reason: str,
) -> RoleReclassificationResult:
    validate_role_direction_context(
        assignments=assignments,
        live_jobs=live_jobs,
        curation=curation,
        sources=sources,
        catalog=catalog,
        capture_contents=capture_contents,
    )
    current = next(
        (
            item for item in assignments.assignments
            if item.assignment_id == current_assignment_id and item.status.value == "current"
        ),
        None,
    )
    if current is None:
        return RoleReclassificationResult(
            None, None, None, (),
            (RoleReclassificationBlocker(
                RoleReclassificationBlockerCode.CONFLICTING_ASSIGNMENT,
                "reclassification requires the current Role Assignment",
                (current_assignment_id,),
            ),),
        )
    affected = tuple(
        item for item in curation.candidates
        if _candidate_job_id(item, sources) == current.canonical_job_id
    )
    affected_ids = {item.candidate_id for item in affected}
    blockers: list[RoleReclassificationBlocker] = []
    for candidate in affected:
        if candidate.status != CandidateLifecycleStatus.REVIEW_PENDING:
            blockers.append(RoleReclassificationBlocker(
                RoleReclassificationBlockerCode.TERMINAL_CANDIDATE,
                "Role reclassification only supports review_pending Candidates",
                (candidate.candidate_id,),
            ))
    for review in curation.review_records:
        if review.parent_candidate_id in affected_ids:
            blockers.append(RoleReclassificationBlocker(
                RoleReclassificationBlockerCode.CANDIDATE_REVIEW_EXISTS,
                "affected Candidate already has human review provenance",
                (review.review_id, review.parent_candidate_id),
            ))
        if affected_ids.intersection(review.successor_candidate_ids):
            blockers.append(RoleReclassificationBlocker(
                RoleReclassificationBlockerCode.CANDIDATE_LINEAGE_EXISTS,
                "affected Candidate participates in revise/split lineage",
                (review.review_id,),
            ))
    affected_clusters = tuple(
        cluster for cluster in curation.clusters
        if affected_ids.intersection(cluster.candidate_ids)
    )
    candidate_map = {item.candidate_id: item for item in curation.candidates}
    for cluster in affected_clusters:
        if cluster.status == ClusterLifecycleStatus.CONFIRMED:
            blockers.append(RoleReclassificationBlocker(
                RoleReclassificationBlockerCode.CONFIRMED_CLUSTER,
                "confirmed Cluster cannot be automatically reclassified",
                (cluster.cluster_id,),
            ))
        elif cluster.status != ClusterLifecycleStatus.PROPOSED:
            blockers.append(RoleReclassificationBlocker(
                RoleReclassificationBlockerCode.NON_PROPOSED_CLUSTER,
                "only proposed Clusters can be reclassified automatically",
                (cluster.cluster_id,),
            ))
        jobs = {
            _candidate_job_id(candidate_map[item], sources)
            for item in cluster.candidate_ids
        }
        if jobs != {current.canonical_job_id}:
            blockers.append(RoleReclassificationBlocker(
                RoleReclassificationBlockerCode.MIXED_JOB_CLUSTER,
                "Cluster contains Candidates from multiple jobs and cannot be reclassified safely",
                (cluster.cluster_id, *sorted(jobs)),
            ))
    if blockers:
        return RoleReclassificationResult(None, None, None, (), tuple(blockers))

    new_assignments = add_reclassified_assignment(
        assignments,
        current_assignment_id=current.assignment_id,
        role_id=new_role_id,
        specialization_id=new_specialization_id,
        evidence_references=evidence_references,
        reviewer_reference=reviewer_reference,
        reviewed_at=reviewed_at,
        decision_reason=decision_reason,
        sources=sources,
        catalog=catalog,
        capture_contents=capture_contents,
    )
    new_assignment = new_assignments.current_for_job(current.canonical_job_id)
    new_jobs = replace(
        live_jobs,
        jobs=tuple(
            replace(
                item,
                mapped_role_id=new_assignment.role_id,
                mapped_specialization_id=new_assignment.specialization_id,
                role_assignment_reference=new_assignment.assignment_id,
            )
            if item.canonical_job_id == current.canonical_job_id else item
            for item in live_jobs.jobs
        ),
    )
    changed_candidates = tuple(
        replace(
            item,
            mapped_role_id=new_assignment.role_id,
            mapped_specialization_id=new_assignment.specialization_id,
            role_assignment_reference=new_assignment.assignment_id,
        )
        if item.candidate_id in affected_ids else item
        for item in curation.candidates
    )
    cluster_id_map: dict[str, str] = {}
    changed_clusters: list[RequirementClusterV5] = []
    rebuilt_ids = {
        cluster.cluster_id
        for cluster in curation.clusters
        if cluster not in affected_clusters
    }
    for cluster in curation.clusters:
        if cluster not in affected_clusters:
            changed_clusters.append(cluster)
            continue
        new_id = generate_cluster_v5_id(
            normalized_name=cluster.normalized_name,
            role_id=new_assignment.role_id,
            specialization_id=new_assignment.specialization_id,
            category=cluster.category,
            role_assignment_references=(new_assignment.assignment_id,),
        )
        if new_id in rebuilt_ids:
            return RoleReclassificationResult(
                None, None, None, (),
                (RoleReclassificationBlocker(
                    RoleReclassificationBlockerCode.AMBIGUOUS_CLUSTER_REBUILD,
                    "proposed Clusters cannot be rebuilt with unique deterministic IDs",
                    (cluster.cluster_id, new_id),
                ),),
            )
        rebuilt_ids.add(new_id)
        cluster_id_map[cluster.cluster_id] = new_id
        changed_clusters.append(replace(
            cluster,
            cluster_id=new_id,
            role_id=new_assignment.role_id,
            specialization_id=new_assignment.specialization_id,
            role_assignment_references=(new_assignment.assignment_id,),
        ))
    changed_candidates = tuple(
        replace(item, cluster_id=cluster_id_map.get(item.cluster_id, item.cluster_id))
        for item in changed_candidates
    )
    new_curation = replace(
        curation,
        candidates=changed_candidates,
        clusters=tuple(changed_clusters),
    )
    validate_role_direction_context(
        assignments=new_assignments,
        live_jobs=new_jobs,
        curation=new_curation,
        sources=sources,
        catalog=catalog,
        capture_contents=capture_contents,
    )
    return RoleReclassificationResult(
        assignments=new_assignments,
        live_jobs=new_jobs,
        curation=new_curation,
        invalidated_derived_references=(
            "clause_coverage_audit",
            "concept_matrix",
            "role_sample_counts",
        ),
        blockers=(),
    )


def save_role_reclassification(
    result: RoleReclassificationResult,
    *,
    assignment_path: str | Path,
    live_jobs_path: str | Path,
    curation_path: str | Path,
    sources: JDSourceCollectionV3,
    catalog: RoleCatalog,
    capture_contents: Mapping[str, str],
) -> tuple[Path, Path, Path]:
    if not result.succeeded or result.assignments is None or result.live_jobs is None or result.curation is None:
        raise Phase2ValidationError("cannot save blocked Role reclassification")
    validate_role_direction_context(
        assignments=result.assignments,
        live_jobs=result.live_jobs,
        curation=result.curation,
        sources=sources,
        catalog=catalog,
        capture_contents=capture_contents,
    )
    from .phase2_storage import save_phase2_json_transaction
    paths = (Path(assignment_path), Path(live_jobs_path), Path(curation_path))
    save_phase2_json_transaction(
        (
            (paths[0], result.assignments.to_dict()),
            (paths[1], result.live_jobs.to_dict()),
            (paths[2], result.curation.to_dict()),
        )
    )
    return paths
