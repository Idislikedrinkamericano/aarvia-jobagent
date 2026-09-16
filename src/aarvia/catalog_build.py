"""Deterministic Phase 2B-1 deduplication, sampling, and Catalog draft building."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from pathlib import Path
import unicodedata
from typing import Any, Iterable, Mapping

from .jd_curation import (
    CandidateLifecycleStatus,
    ClusterLifecycleStatus,
    CurationArtifact,
    CurationArtifactV3,
    CurationArtifactV4,
    CurationArtifactV5,
    CurationArtifactV6,
    CandidateLogicGroup,
    CandidateLogicGroupStatus,
    RequirementCandidate,
    ReviewerDecision,
)
from .curation_workflow import (
    CurationArtifactV7,
    LogicGroupStatusV7,
    effective_logic_group_bindings,
)
from .jd_sources import (
    JDSource,
    JDSourceCollection,
    JDSourceCollectionV3,
    JDSourceType,
    SourceLifecycleStatus,
    SourceTier,
)
from .role_catalog import (
    Phase2ValidationError,
    RequirementCategory,
    RequirementImportance,
    RequirementPrevalence,
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


CATALOG_DRAFT_SCHEMA = "aarvia.catalog_draft"
CATALOG_DRAFT_SCHEMA_VERSION = 2


class CatalogLifecycleStatus(str, Enum):
    DRAFT = "catalog_draft"
    VALIDATED = "validated"
    PUBLISHED = "published"


class BuildBlockerCode(str, Enum):
    CURATION_SCHEMA_UPGRADE_REQUIRED = "curation_schema_upgrade_required"
    NO_SAMPLES = "no_samples"
    INSUFFICIENT_COMPANIES = "insufficient_companies"
    INSUFFICIENT_TIER_A_SHARE = "insufficient_tier_a_share"
    INELIGIBLE_SAMPLE = "ineligible_sample"
    DUPLICATE_COMPANY_SAMPLE = "duplicate_company_sample"
    NON_CANONICAL_SAMPLE = "non_canonical_sample"
    CANDIDATE_OUTSIDE_SAMPLE = "candidate_outside_sample"
    NO_APPROVED_EVIDENCE = "no_approved_evidence"
    TIER_C_ONLY_EVIDENCE = "tier_c_only_evidence"
    UNAPPROVED_CANDIDATE = "unapproved_candidate"
    UNCONFIRMED_CLUSTER = "unconfirmed_cluster"
    ROLE_ASSIGNMENT_REQUIRED = "role_assignment_required"
    ROLE_SAMPLE_MISMATCH = "role_sample_mismatch"
    DUPLICATE_CANONICAL_JOB = "duplicate_canonical_job"
    UNCONFIRMED_LOGIC_GROUP = "unconfirmed_logic_group"
    PRODUCTION_REQUIREMENT_LOGIC_CONTRACT_REQUIRED = "production_requirement_logic_contract_required"
    REJECTED_LOGIC_GROUP_MEMBER_REQUIRES_RESOLUTION = (
        "rejected_logic_group_member_requires_resolution"
    )
    QUARANTINED_LOGIC_GROUP = "quarantined_logic_group"
    APPROVED_CANDIDATE_UNCLUSTERED = "approved_candidate_unclustered"


@dataclass(frozen=True, eq=True)
class BuildBlocker:
    code: BuildBlockerCode
    message: str
    references: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "references": list(self.references)}


def _logic_group_blocker(group: CandidateLogicGroup | Any) -> BuildBlocker:
    if group.status in {CandidateLogicGroupStatus.PROPOSED, LogicGroupStatusV7.PROPOSED}:
        return BuildBlocker(
            BuildBlockerCode.UNCONFIRMED_LOGIC_GROUP,
            "Candidate Logic Group requires human confirmation before Catalog building",
            (group.logic_group_id,),
        )
    if group.status in {CandidateLogicGroupStatus.CONFIRMED, LogicGroupStatusV7.CONFIRMED}:
        return BuildBlocker(
            BuildBlockerCode.PRODUCTION_REQUIREMENT_LOGIC_CONTRACT_REQUIRED,
            "Catalog schema 1 cannot preserve a confirmed Candidate Logic Group",
            (group.logic_group_id,),
        )
    if group.status == LogicGroupStatusV7.QUARANTINED:
        return BuildBlocker(
            BuildBlockerCode.QUARANTINED_LOGIC_GROUP,
            "Quarantined Candidate Logic Group requires an explicit final resolution",
            (group.logic_group_id,),
        )
    return BuildBlocker(
        BuildBlockerCode.REJECTED_LOGIC_GROUP_MEMBER_REQUIRES_RESOLUTION,
        "Rejected Candidate Logic Group members require an independent reviewed resolution",
        (group.logic_group_id,),
    )


@dataclass(frozen=True, eq=True)
class DedupReviewCandidate:
    first_source_id: str
    second_source_id: str
    reason: str


@dataclass(frozen=True, eq=True)
class DeduplicationResult:
    canonical_groups: tuple[tuple[str, ...], ...]
    review_candidates: tuple[DedupReviewCandidate, ...]


def _normalized_label(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def deduplicate_sources(sources: Iterable[JDSource]) -> DeduplicationResult:
    items = tuple(sources)
    groups: dict[str, list[str]] = {}
    for source in items:
        if source.canonical_job_id is not None:
            groups.setdefault(source.canonical_job_id, []).append(source.source_id)
    reviews: list[DedupReviewCandidate] = []
    for index, first in enumerate(items):
        for second in items[index + 1:]:
            if first.canonical_job_id == second.canonical_job_id and first.canonical_job_id is not None:
                continue
            if (
                first.company_id == second.company_id
                and _normalized_label(first.exact_job_title) == _normalized_label(second.exact_job_title)
                and _normalized_label(first.location) == _normalized_label(second.location)
                and first.exact_job_title is not None
            ):
                reviews.append(
                    DedupReviewCandidate(
                        first.source_id,
                        second.source_id,
                        "same normalized company, title, and location but different stable identity",
                    )
                )
    canonical_groups = tuple(tuple(sorted(group)) for _, group in sorted(groups.items()))
    return DeduplicationResult(canonical_groups, tuple(reviews))


def prevalence_for_counts(company_count: int, supporting_company_count: int) -> RequirementPrevalence:
    if company_count < 0 or supporting_company_count < 0 or supporting_company_count > company_count:
        raise Phase2ValidationError("prevalence counts are invalid")
    if company_count < 6:
        return RequirementPrevalence.UNKNOWN
    if supporting_company_count * 100 >= company_count * 70:
        return RequirementPrevalence.COMMON
    if supporting_company_count * 100 >= company_count * 40:
        return RequirementPrevalence.FREQUENT
    return RequirementPrevalence.VARIABLE


def tier_a_share_is_sufficient(tier_a_count: int, total_count: int) -> bool:
    if total_count <= 0 or tier_a_count < 0 or tier_a_count > total_count:
        return False
    return tier_a_count * 100 >= total_count * 60


@dataclass(frozen=True, eq=True)
class RoleSample:
    source_id: str
    role_id: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "sample") -> RoleSample:
        data = _mapping(value, path)
        _reject_unknown(data, {"source_id", "role_id"}, path)
        return cls(
            _stable_id(data.get("source_id"), f"{path}.source_id"),
            _stable_id(data.get("role_id"), f"{path}.role_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"source_id": self.source_id, "role_id": self.role_id}


@dataclass(frozen=True, eq=True)
class DraftRequirement:
    requirement_id: str
    role_id: str
    cluster_id: str
    name: str
    description: str
    category: RequirementCategory
    importance: RequirementImportance
    prevalence: RequirementPrevalence
    supporting_company_count: int
    sampled_company_count: int
    source_references: tuple[str, ...]
    candidate_references: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "draft_requirement") -> DraftRequirement:
        data = _mapping(value, path)
        allowed = {
            "requirement_id", "role_id", "cluster_id", "name", "description", "category",
            "importance", "prevalence", "supporting_company_count", "sampled_company_count",
            "source_references", "candidate_references",
        }
        _reject_unknown(data, allowed, path)
        counts = []
        for field in ("supporting_company_count", "sampled_company_count"):
            item = data.get(field)
            if not isinstance(item, int) or isinstance(item, bool) or item < 0:
                raise Phase2ValidationError(f"{path}.{field} must be a non-negative integer")
            counts.append(item)
        result = cls(
            requirement_id=_stable_id(data.get("requirement_id"), f"{path}.requirement_id"),
            role_id=_stable_id(data.get("role_id"), f"{path}.role_id"),
            cluster_id=_stable_id(data.get("cluster_id"), f"{path}.cluster_id"),
            name=_text(data.get("name"), f"{path}.name"),
            description=_text(data.get("description"), f"{path}.description"),
            category=_enum(data.get("category"), RequirementCategory, f"{path}.category"),
            importance=_enum(data.get("importance"), RequirementImportance, f"{path}.importance"),
            prevalence=_enum(data.get("prevalence"), RequirementPrevalence, f"{path}.prevalence"),
            supporting_company_count=counts[0],
            sampled_company_count=counts[1],
            source_references=_string_tuple(data.get("source_references"), f"{path}.source_references", ids=True),
            candidate_references=_string_tuple(data.get("candidate_references"), f"{path}.candidate_references", ids=True),
        )
        if result.supporting_company_count > result.sampled_company_count:
            raise Phase2ValidationError(f"{path} support count exceeds sample count")
        expected = prevalence_for_counts(result.sampled_company_count, result.supporting_company_count)
        if result.prevalence != expected:
            raise Phase2ValidationError(f"{path} prevalence does not match deterministic counts")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "role_id": self.role_id,
            "cluster_id": self.cluster_id,
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "importance": self.importance.value,
            "prevalence": self.prevalence.value,
            "supporting_company_count": self.supporting_company_count,
            "sampled_company_count": self.sampled_company_count,
            "source_references": list(self.source_references),
            "candidate_references": list(self.candidate_references),
        }


@dataclass(frozen=True, eq=True)
class CatalogDraft:
    draft_id: str
    base_catalog_version: str
    target_catalog_version: str
    source_collection_id: str
    curation_artifact_id: str
    created_at: str
    status: CatalogLifecycleStatus
    publication_decision: ReviewerDecision
    publication_decision_reason: str | None
    publication_reviewed_at: str | None
    samples: tuple[RoleSample, ...]
    requirements: tuple[DraftRequirement, ...]
    schema: str = CATALOG_DRAFT_SCHEMA
    schema_version: int = CATALOG_DRAFT_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CatalogDraft:
        data = _mapping(value, "catalog_draft")
        _reject_unknown(data, {"schema", "schema_version", "draft_id", "base_catalog_version", "target_catalog_version", "source_collection_id", "curation_artifact_id", "created_at", "status", "publication_decision", "publication_decision_reason", "publication_reviewed_at", "samples", "requirements"}, "catalog_draft")
        if data.get("schema") != CATALOG_DRAFT_SCHEMA or data.get("schema_version") != CATALOG_DRAFT_SCHEMA_VERSION:
            raise Phase2ValidationError("unsupported Catalog Draft schema version")
        raw_samples = data.get("samples")
        raw_requirements = data.get("requirements")
        if not isinstance(raw_samples, list) or not isinstance(raw_requirements, list):
            raise Phase2ValidationError("Catalog Draft samples and requirements must be lists")
        result = cls(
            draft_id=_stable_id(data.get("draft_id"), "catalog_draft.draft_id"),
            base_catalog_version=_version(data.get("base_catalog_version"), "catalog_draft.base_catalog_version"),
            target_catalog_version=_version(data.get("target_catalog_version"), "catalog_draft.target_catalog_version"),
            source_collection_id=_stable_id(data.get("source_collection_id"), "catalog_draft.source_collection_id"),
            curation_artifact_id=_stable_id(data.get("curation_artifact_id"), "catalog_draft.curation_artifact_id"),
            created_at=_iso_datetime(data.get("created_at"), "catalog_draft.created_at"),
            status=_enum(data.get("status"), CatalogLifecycleStatus, "catalog_draft.status"),
            publication_decision=_enum(data.get("publication_decision"), ReviewerDecision, "catalog_draft.publication_decision"),
            publication_decision_reason=_text(data.get("publication_decision_reason"), "catalog_draft.publication_decision_reason", required=False),
            publication_reviewed_at=(None if data.get("publication_reviewed_at") is None else _iso_datetime(data.get("publication_reviewed_at"), "catalog_draft.publication_reviewed_at")),
            samples=tuple(RoleSample.from_dict(item, f"catalog_draft.samples[{index}]") for index, item in enumerate(raw_samples)),
            requirements=tuple(DraftRequirement.from_dict(item, f"catalog_draft.requirements[{index}]") for index, item in enumerate(raw_requirements)),
        )
        if result.status == CatalogLifecycleStatus.PUBLISHED:
            if (
                result.publication_decision != ReviewerDecision.APPROVE
                or result.publication_decision_reason is None
                or result.publication_reviewed_at is None
            ):
                raise Phase2ValidationError("published Catalog Draft requires human publication approval")
        elif (
            result.publication_decision != ReviewerDecision.PENDING
            or result.publication_decision_reason is not None
            or result.publication_reviewed_at is not None
        ):
            raise Phase2ValidationError("unpublished Catalog Draft cannot contain publication approval")
        base_parts = tuple(int(part) for part in result.base_catalog_version.split("."))
        target_parts = tuple(int(part) for part in result.target_catalog_version.split("."))
        if target_parts <= base_parts:
            raise Phase2ValidationError("Catalog Draft target version must be newer than its base Catalog")
        if not result.requirements:
            raise Phase2ValidationError("Catalog Draft requires at least one approved requirement")
        return result

    def validate(
        self,
        sources: JDSourceCollectionV3,
        curation: CurationArtifactV7,
        catalog: RoleCatalog,
        assignments: "RoleAssignmentArtifact",
        capture_contents: Mapping[str, str],
    ) -> None:
        canonical = CatalogDraft.from_dict(self.to_dict())
        if canonical != self:
            raise Phase2ValidationError("Catalog Draft contains non-canonical data")
        if not isinstance(curation, CurationArtifactV7):
            raise Phase2ValidationError(
                "Catalog Draft requires a validated Curation schema 7 artifact"
            )
        if not isinstance(sources, JDSourceCollectionV3):
            raise Phase2ValidationError(
                "Catalog Draft requires JD Source schema 3 capture provenance"
            )
        if self.base_catalog_version != catalog.catalog_version:
            raise Phase2ValidationError("Catalog Draft base version does not match Catalog")
        if self.source_collection_id != sources.collection_id or self.curation_artifact_id != curation.artifact_id:
            raise Phase2ValidationError("Catalog Draft context references do not match")
        assignments.validate(sources, catalog, capture_contents)
        curation.validate(sources, catalog, assignments, capture_contents)
        logic_blockers = tuple(
            _logic_group_blocker(group)
            for group in effective_logic_group_bindings(curation)
        )
        if logic_blockers:
            raise Phase2ValidationError(logic_blockers[0].message)
        candidate_map = {item.candidate_id: item for item in curation.candidates}
        cluster_map = {item.cluster_id: item for item in curation.clusters}
        confirmed_cluster_ids = {
            item.cluster_id
            for item in curation.clusters
            if item.status == ClusterLifecycleStatus.CONFIRMED
        }
        if any(
            item.status == ClusterLifecycleStatus.PROPOSED
            for item in curation.clusters
        ):
            raise Phase2ValidationError(
                "Catalog Draft context contains an unconfirmed Cluster"
            )
        for candidate in curation.candidates:
            if (
                candidate.status == CandidateLifecycleStatus.APPROVED
                and candidate.cluster_id not in confirmed_cluster_ids
            ):
                raise Phase2ValidationError(
                    f"approved Candidate {candidate.candidate_id} is not in a confirmed Cluster"
                )
        source_ids = {item.source_id for item in sources.sources}
        requirement_ids = [item.requirement_id for item in self.requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise Phase2ValidationError("Catalog Draft contains duplicate requirement IDs")
        samples_by_role: dict[str, list[JDSource]] = {}
        seen_company_role: set[tuple[str, str]] = set()
        seen_canonical_jobs: set[str] = set()
        for sample in self.samples:
            source = sources.source(sample.source_id)
            catalog.role(sample.role_id)
            if not _is_eligible_sample(source):
                raise Phase2ValidationError("Catalog Draft sample must be a verified Tier A or Tier B job posting")
            if source.canonical_job_id is None or source.canonical_source_reference not in {None, source.source_id}:
                raise Phase2ValidationError("Catalog Draft sample must use its canonical source")
            if source.canonical_job_id in seen_canonical_jobs:
                raise Phase2ValidationError("Catalog Draft contains duplicate canonical job sample")
            seen_canonical_jobs.add(source.canonical_job_id)
            assignment = assignments.current_for_job(source.canonical_job_id)
            if assignment.source_id != source.source_id or assignment.role_id != sample.role_id:
                raise Phase2ValidationError("Catalog Draft sample does not match current Role Assignment")
            company_role = (source.company_id, sample.role_id)
            if company_role in seen_company_role:
                raise Phase2ValidationError("Catalog Draft contains duplicate company sample")
            seen_company_role.add(company_role)
            samples_by_role.setdefault(sample.role_id, []).append(source)
        for role_id, role_sources in samples_by_role.items():
            if len(role_sources) < 6:
                raise Phase2ValidationError(f"Catalog Draft role {role_id} has fewer than six companies")
            tier_a = sum(item.source_tier == SourceTier.TIER_A_OFFICIAL for item in role_sources)
            if not tier_a_share_is_sufficient(tier_a, len(role_sources)):
                raise Phase2ValidationError(f"Catalog Draft role {role_id} has insufficient Tier A share")
        for requirement in self.requirements:
            catalog.role(requirement.role_id)
            if set(requirement.source_references) - source_ids:
                raise Phase2ValidationError(f"draft requirement {requirement.requirement_id} has unknown source")
            if set(requirement.candidate_references) - set(candidate_map):
                raise Phase2ValidationError(f"draft requirement {requirement.requirement_id} has unknown candidate")
            cluster = cluster_map.get(requirement.cluster_id)
            if cluster is None or cluster.status != ClusterLifecycleStatus.CONFIRMED:
                raise Phase2ValidationError(f"draft requirement {requirement.requirement_id} requires confirmed cluster")
            if cluster.role_id != requirement.role_id:
                raise Phase2ValidationError(f"draft requirement {requirement.requirement_id} cluster belongs to another role")
            if set(requirement.candidate_references) != set(cluster.candidate_ids):
                raise Phase2ValidationError(f"draft requirement {requirement.requirement_id} does not include its complete cluster")
            candidates = [candidate_map[item] for item in requirement.candidate_references]
            all_logic_bound_members = {
                candidate_id
                for group in effective_logic_group_bindings(curation)
                for candidate_id in group.member_candidate_references
            }
            if set(requirement.candidate_references) & all_logic_bound_members:
                raise Phase2ValidationError(
                    f"draft requirement {requirement.requirement_id} would flatten Candidate Logic Group members"
                )
            if any(
                item.status != CandidateLifecycleStatus.APPROVED
                or item.reviewer_decision != ReviewerDecision.APPROVE
                for item in candidates
            ):
                raise Phase2ValidationError(f"draft requirement {requirement.requirement_id} contains unapproved candidate")
            if set(requirement.source_references) != {item.source_id for item in candidates}:
                raise Phase2ValidationError(f"draft requirement {requirement.requirement_id} source evidence does not match candidates")
            role_sources = samples_by_role.get(requirement.role_id, [])
            sampled_job_ids = {item.canonical_job_id for item in role_sources}
            supporting_companies: set[str] = set()
            evidence_sources: list[JDSource] = []
            for candidate in candidates:
                evidence_source = sources.source(candidate.source_id)
                canonical = sources.source(evidence_source.canonical_source_reference) if evidence_source.canonical_source_reference else evidence_source
                if canonical.canonical_job_id in sampled_job_ids:
                    supporting_companies.add(canonical.company_id)
                    evidence_sources.append(evidence_source)
            if evidence_sources and all(item.source_tier == SourceTier.TIER_C_DISCOVERY_ONLY for item in evidence_sources):
                raise Phase2ValidationError(f"draft requirement {requirement.requirement_id} relies only on Tier C")
            if requirement.sampled_company_count != len(role_sources) or requirement.supporting_company_count != len(supporting_companies):
                raise Phase2ValidationError(f"draft requirement {requirement.requirement_id} company counts do not match evidence")
            if requirement.prevalence != prevalence_for_counts(len(role_sources), len(supporting_companies)):
                raise Phase2ValidationError(f"draft requirement {requirement.requirement_id} prevalence does not match evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "draft_id": self.draft_id,
            "base_catalog_version": self.base_catalog_version,
            "target_catalog_version": self.target_catalog_version,
            "source_collection_id": self.source_collection_id,
            "curation_artifact_id": self.curation_artifact_id,
            "created_at": self.created_at,
            "status": self.status.value,
            "publication_decision": self.publication_decision.value,
            "publication_decision_reason": self.publication_decision_reason,
            "publication_reviewed_at": self.publication_reviewed_at,
            "samples": [item.to_dict() for item in self.samples],
            "requirements": [item.to_dict() for item in self.requirements],
        }


@dataclass(frozen=True, eq=True)
class CatalogBuildResult:
    draft: CatalogDraft | None
    blockers: tuple[BuildBlocker, ...]


def _requirement_id(role_id: str, cluster_id: str) -> str:
    digest = hashlib.sha256(f"draft-requirement-v1|{role_id}|{cluster_id}".encode("utf-8")).hexdigest()[:20]
    return f"{role_id}.{digest}"


def _is_eligible_sample(source: JDSource) -> bool:
    if source.source_status != SourceLifecycleStatus.VERIFIED:
        return False
    return (
        source.source_tier == SourceTier.TIER_A_OFFICIAL
        and source.source_type == JDSourceType.OFFICIAL_JOB_POSTING
    ) or (
        source.source_tier == SourceTier.TIER_B_VERIFIED_PLATFORM
        and source.source_type == JDSourceType.PLATFORM_JOB_POSTING
    )


def build_catalog_draft(
    *,
    draft_id: str,
    target_catalog_version: str,
    created_at: str,
    samples: Iterable[RoleSample],
    sources: JDSourceCollectionV3,
    curation: CurationArtifact | CurationArtifactV3 | CurationArtifactV4 | CurationArtifactV5 | CurationArtifactV6 | CurationArtifactV7,
    catalog: RoleCatalog,
    assignments: "RoleAssignmentArtifact | None" = None,
    capture_contents: Mapping[str, str] | None = None,
) -> CatalogBuildResult:
    from .role_assignments import RoleAssignmentArtifact

    sources.validate()
    if not isinstance(curation, CurationArtifactV7):
        return CatalogBuildResult(
            None,
            (
                BuildBlocker(
                    BuildBlockerCode.CURATION_SCHEMA_UPGRADE_REQUIRED,
                    "Catalog building requires Curation schema 7 workflow provenance",
                    (curation.artifact_id, f"schema_version={curation.schema_version}"),
                ),
            ),
        )
    if not isinstance(assignments, RoleAssignmentArtifact) or capture_contents is None:
        return CatalogBuildResult(
            None,
            (BuildBlocker(
                BuildBlockerCode.ROLE_ASSIGNMENT_REQUIRED,
                "Catalog building requires validated Role Assignments and capture content",
                (curation.artifact_id,),
            ),),
        )
    if not isinstance(sources, JDSourceCollectionV3):
        raise Phase2ValidationError(
            "Catalog building requires JD Source schema 3 capture provenance"
        )
    assignments.validate(sources, catalog, capture_contents)
    curation.validate(sources, catalog, assignments, capture_contents)
    sample_items = tuple(samples)
    blockers: list[BuildBlocker] = []
    if not sample_items:
        blockers.append(BuildBlocker(BuildBlockerCode.NO_SAMPLES, "Catalog draft requires verified job samples"))
    by_role: dict[str, list[tuple[RoleSample, JDSource]]] = {}
    seen_company_role: set[tuple[str, str]] = set()
    seen_canonical_jobs: set[str] = set()
    for sample in sample_items:
        source = sources.source(sample.source_id)
        catalog.role(sample.role_id)
        if not _is_eligible_sample(source):
            blockers.append(BuildBlocker(
                BuildBlockerCode.INELIGIBLE_SAMPLE,
                "sample must be a verified Tier A official job posting or Tier B platform job posting",
                (sample.source_id,),
            ))
            continue
        if source.canonical_job_id is None or source.canonical_source_reference not in {None, source.source_id}:
            blockers.append(BuildBlocker(BuildBlockerCode.NON_CANONICAL_SAMPLE, "sample must use its canonical source", (sample.source_id,)))
            continue
        if source.canonical_job_id in seen_canonical_jobs:
            blockers.append(BuildBlocker(
                BuildBlockerCode.DUPLICATE_CANONICAL_JOB,
                "one canonical job may appear only once in a Catalog sample",
                (source.canonical_job_id,),
            ))
            continue
        seen_canonical_jobs.add(source.canonical_job_id)
        assignment = assignments.current_for_job(source.canonical_job_id)
        if assignment.source_id != source.source_id or assignment.role_id != sample.role_id:
            blockers.append(BuildBlocker(
                BuildBlockerCode.ROLE_SAMPLE_MISMATCH,
                "sample Role must match its current confirmed Role Assignment",
                (sample.source_id, sample.role_id, assignment.assignment_id),
            ))
            continue
        company_key = (source.company_id, sample.role_id)
        if company_key in seen_company_role:
            blockers.append(BuildBlocker(BuildBlockerCode.DUPLICATE_COMPANY_SAMPLE, "one company may contribute only one sample per Role Family", (source.company_id, sample.role_id)))
            continue
        seen_company_role.add(company_key)
        by_role.setdefault(sample.role_id, []).append((sample, source))

    approved = [
        item for item in curation.candidates
        if item.status == CandidateLifecycleStatus.APPROVED
        and item.reviewer_decision == ReviewerDecision.APPROVE
    ]
    candidate_map = {item.candidate_id: item for item in approved}
    cluster_map = {item.cluster_id: item for item in curation.clusters}
    requirements: list[DraftRequirement] = []
    ordered_logic_groups = effective_logic_group_bindings(curation)
    blockers.extend(_logic_group_blocker(group) for group in ordered_logic_groups)
    all_logic_bound_members = {
        candidate_id
        for group in ordered_logic_groups
        for candidate_id in group.member_candidate_references
    }
    active_cluster_ids = {
        item.cluster_id for item in curation.clusters
        if item.status in {ClusterLifecycleStatus.PROPOSED, ClusterLifecycleStatus.CONFIRMED}
    }
    for candidate in approved:
        if candidate.cluster_id not in active_cluster_ids:
            blockers.append(BuildBlocker(
                BuildBlockerCode.APPROVED_CANDIDATE_UNCLUSTERED,
                "approved Candidate requires a current proposed or confirmed Cluster before Catalog building",
                (candidate.candidate_id,),
            ))
    blockers.extend(
        BuildBlocker(
            BuildBlockerCode.UNCONFIRMED_CLUSTER,
            "only confirmed clusters can enter a Catalog draft",
            (cluster.cluster_id,),
        )
        for cluster in sorted(curation.clusters, key=lambda item: item.cluster_id)
        if cluster.status == ClusterLifecycleStatus.PROPOSED
    )
    for role_id, role_samples in by_role.items():
        total = len(role_samples)
        tier_a = sum(source.source_tier == SourceTier.TIER_A_OFFICIAL for _, source in role_samples)
        if total < 6:
            blockers.append(BuildBlocker(BuildBlockerCode.INSUFFICIENT_COMPANIES, "Role Family needs at least six independent companies", (role_id, str(total))))
        if not tier_a_share_is_sufficient(tier_a, total):
            blockers.append(BuildBlocker(BuildBlockerCode.INSUFFICIENT_TIER_A_SHARE, "Tier A canonical samples must be at least 60 percent", (role_id, str(tier_a), str(total))))
        sampled_jobs = {source.canonical_job_id: source for _, source in role_samples}
        role_clusters = [
            cluster
            for cluster in curation.clusters
            if cluster.role_id == role_id
            and cluster.status != ClusterLifecycleStatus.REJECTED
        ]
        if not role_clusters:
            blockers.append(BuildBlocker(
                BuildBlockerCode.NO_APPROVED_EVIDENCE,
                "Role Family has no curated requirement clusters",
                (role_id,),
            ))
        for cluster in role_clusters:
            if cluster.status != ClusterLifecycleStatus.CONFIRMED:
                continue
            cluster_candidates = [candidate_map[item] for item in cluster.candidate_ids if item in candidate_map]
            if len(cluster_candidates) != len(cluster.candidate_ids):
                blockers.append(BuildBlocker(BuildBlockerCode.UNAPPROVED_CANDIDATE, "cluster contains a candidate without complete approval", (cluster.cluster_id,)))
                continue
            grouped = tuple(
                sorted(set(cluster.candidate_ids) & all_logic_bound_members)
            )
            if grouped:
                continue
            sampled_job_ids = set(sampled_jobs)
            outside_sample: list[str] = []
            for candidate in cluster_candidates:
                evidence_source = sources.source(candidate.source_id)
                canonical = sources.source(evidence_source.canonical_source_reference) if evidence_source.canonical_source_reference else evidence_source
                if canonical.canonical_job_id not in sampled_job_ids:
                    outside_sample.append(candidate.candidate_id)
            if outside_sample:
                blockers.append(BuildBlocker(
                    BuildBlockerCode.CANDIDATE_OUTSIDE_SAMPLE,
                    "confirmed cluster contains evidence outside the selected company sample",
                    (cluster.cluster_id, *sorted(outside_sample)),
                ))
                continue
            supporting: dict[str, JDSource] = {}
            evidence_sources: list[JDSource] = []
            for candidate in cluster_candidates:
                source = sources.source(candidate.source_id)
                canonical = sources.source(source.canonical_source_reference) if source.canonical_source_reference else source
                if canonical.canonical_job_id in sampled_jobs:
                    supporting[canonical.company_id] = canonical
                    evidence_sources.append(source)
            if evidence_sources and all(item.source_tier == SourceTier.TIER_C_DISCOVERY_ONLY for item in evidence_sources):
                blockers.append(BuildBlocker(BuildBlockerCode.TIER_C_ONLY_EVIDENCE, "Tier C cannot be the only production evidence", (cluster.cluster_id,)))
                continue
            if not evidence_sources:
                blockers.append(BuildBlocker(
                    BuildBlockerCode.NO_APPROVED_EVIDENCE,
                    "confirmed cluster has no approved evidence in the selected sample",
                    (cluster.cluster_id,),
                ))
                continue
            category = cluster.category
            importance = cluster.importance
            requirements.append(
                DraftRequirement(
                    requirement_id=_requirement_id(role_id, cluster.cluster_id),
                    role_id=role_id,
                    cluster_id=cluster.cluster_id,
                    name=cluster.normalized_name,
                    description=cluster.normalized_description,
                    category=category,
                    importance=importance,
                    prevalence=prevalence_for_counts(total, len(supporting)),
                    supporting_company_count=len(supporting),
                    sampled_company_count=total,
                    source_references=tuple(sorted({item.source_id for item in evidence_sources})),
                    candidate_references=tuple(sorted(item.candidate_id for item in cluster_candidates)),
                )
            )
    if blockers:
        return CatalogBuildResult(None, tuple(blockers))
    draft = CatalogDraft(
        draft_id=_stable_id(draft_id, "draft_id"),
        base_catalog_version=catalog.catalog_version,
        target_catalog_version=_version(target_catalog_version, "target_catalog_version"),
        source_collection_id=sources.collection_id,
        curation_artifact_id=curation.artifact_id,
        created_at=_iso_datetime(created_at, "created_at"),
        status=CatalogLifecycleStatus.DRAFT,
        publication_decision=ReviewerDecision.PENDING,
        publication_decision_reason=None,
        publication_reviewed_at=None,
        samples=sample_items,
        requirements=tuple(sorted(requirements, key=lambda item: item.requirement_id)),
    )
    draft.validate(sources, curation, catalog, assignments, capture_contents)
    return CatalogBuildResult(draft, ())


def save_catalog_draft(value: CatalogDraft, path: str | Path, *, sources: JDSourceCollectionV3, curation: CurationArtifactV7, catalog: RoleCatalog, assignments: "RoleAssignmentArtifact", capture_contents: Mapping[str, str]) -> Path:
    value.validate(sources, curation, catalog, assignments, capture_contents)
    from .phase2_storage import save_phase2_json
    return save_phase2_json(CatalogDraft.from_dict(value.to_dict()).to_dict(), path)


def load_catalog_draft(path: str | Path, *, sources: JDSourceCollectionV3, curation: CurationArtifactV7, catalog: RoleCatalog, assignments: "RoleAssignmentArtifact", capture_contents: Mapping[str, str]) -> CatalogDraft:
    from .phase2_storage import load_phase2_json
    value = CatalogDraft.from_dict(load_phase2_json(path))
    value.validate(sources, curation, catalog, assignments, capture_contents)
    return value
