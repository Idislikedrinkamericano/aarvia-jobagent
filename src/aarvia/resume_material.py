"""Deterministic, role-neutral Resume Material contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .capability_rubric import CapabilityRubric, capability_rubric_fingerprint
from .career_direction import UserRoleDecision, profile_fingerprint
from .career_gap_analysis import CareerGapAnalysis
from .evidence_bank import (
    EvidenceBank,
    EvidenceItem,
    EvidenceSourceRecord,
    EvidenceSourceRecordType,
    ResumeEvidenceUse,
)
from .evidence_binding_review import EvidenceBindingReviewArtifact
from .evidence_enrichment import (
    EvidenceEnrichmentArtifact,
    ResumeEnrichmentUse,
    select_resume_enrichment_materials,
)
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
    _string_tuple,
    _text,
    _version,
)
from .role_recommendation import RoleRecommendationArtifact


RESUME_MATERIAL_SCHEMA = "aarvia.resume_material"
RESUME_MATERIAL_SCHEMA_VERSION = 1
RESUME_MATERIAL_ID_VERSION = "resume-material-v1"


class ResumeSectionType(str, Enum):
    CONTACT = "contact"
    EDUCATION = "education"
    SKILLS = "skills"
    EXPERIENCE = "experience"
    PROJECTS = "projects"


class ResumeMaterialType(str, Enum):
    PROFILE_EVIDENCE = "profile_evidence"
    ENRICHMENT_CLAIM = "enrichment_claim"


class ResumeMaterialEligibility(str, Enum):
    STRUCTURAL_ONLY = "structural_only"
    FACTUAL_CONTEXT = "factual_context"
    ACHIEVEMENT_COMPONENT = "achievement_component"


class ResumeMaterialReason(str, Enum):
    STRUCTURAL_PROFILE_FACT = "structural_profile_fact"
    CONFIRMED_PROFILE_FACT = "confirmed_profile_fact"
    CONFIRMED_CAPABILITY_EVIDENCE = "confirmed_capability_evidence"
    CONFIRMED_ENRICHMENT_CLAIM = "confirmed_enrichment_claim"


class ResumeCoverageReason(str, Enum):
    UNCOVERED_PROFILE_RECORD = "uncovered_profile_record"
    MISSING_CONTACT_FIELD = "missing_contact_field"
    UNAVAILABLE_VERIFIED_MATERIAL = "unavailable_verified_material"


class ResumeCoverageCategory(str, Enum):
    CONTACT = "contact"
    EDUCATION = "education"
    EXPERIENCE = "experience"
    PROJECT = "project"
    SKILL = "skill"


_CONTACT_FIELDS = (
    "name",
    "email",
    "phone",
    "linkedin_url",
    "github_url",
    "portfolio_url",
)


def _parsed_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _stable_hash(prefix: str, value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


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


def _verbatim(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Phase2ValidationError(f"{path} must be non-empty text")
    if "\x00" in value:
        raise Phase2ValidationError(f"{path} contains an invalid control character")
    return value


def _optional_id(value: Any, path: str) -> str | None:
    return None if value is None else _stable_id(value, path)


def _count(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise Phase2ValidationError(f"{path} must be a non-negative integer")
    return value


def _schema_version(value: Any, path: str, expected: int) -> int:
    if value != expected:
        raise Phase2ValidationError(f"{path} must equal {expected}")
    return expected


def _optional_schema_version(value: Any, path: str) -> int | None:
    if value is None:
        return None
    return _schema_version(value, path, 1)


@dataclass(frozen=True, eq=True)
class ResumeMaterial:
    material_id: str
    material_type: ResumeMaterialType
    section: ResumeSectionType
    eligibility: ResumeMaterialEligibility
    reason_codes: tuple[ResumeMaterialReason, ...]
    source_record_id: str
    evidence_item_ids: tuple[str, ...]
    enrichment_claim_ids: tuple[str, ...]
    exact_text: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> ResumeMaterial:
        data = _mapping(value, path)
        _reject_unknown(
            data,
            {
                "material_id", "material_type", "section", "eligibility",
                "reason_codes", "source_record_id", "evidence_item_ids",
                "enrichment_claim_ids", "exact_text",
            },
            path,
        )
        reasons = tuple(
            _enum(item, ResumeMaterialReason, f"{path}.reason_codes[{index}]")
            for index, item in enumerate(
                _string_tuple(data.get("reason_codes"), f"{path}.reason_codes")
            )
        )
        result = cls(
            material_id=_stable_id(data.get("material_id"), f"{path}.material_id"),
            material_type=_enum(
                data.get("material_type"), ResumeMaterialType, f"{path}.material_type"
            ),
            section=_enum(data.get("section"), ResumeSectionType, f"{path}.section"),
            eligibility=_enum(
                data.get("eligibility"), ResumeMaterialEligibility, f"{path}.eligibility"
            ),
            reason_codes=reasons,
            source_record_id=_stable_id(
                data.get("source_record_id"), f"{path}.source_record_id"
            ),
            evidence_item_ids=_string_tuple(
                data.get("evidence_item_ids"), f"{path}.evidence_item_ids"
            ),
            enrichment_claim_ids=_string_tuple(
                data.get("enrichment_claim_ids"), f"{path}.enrichment_claim_ids"
            ),
            exact_text=_verbatim(data.get("exact_text"), f"{path}.exact_text"),
        )
        if not result.evidence_item_ids:
            raise Phase2ValidationError(f"{path} must reference at least one Evidence Item")
        if result.evidence_item_ids != tuple(sorted(set(result.evidence_item_ids))):
            raise Phase2ValidationError(f"{path}.evidence_item_ids must be unique and sorted")
        if result.enrichment_claim_ids != tuple(sorted(set(result.enrichment_claim_ids))):
            raise Phase2ValidationError(f"{path}.enrichment_claim_ids must be unique and sorted")
        if not reasons or reasons != tuple(sorted(set(reasons), key=lambda item: item.value)):
            raise Phase2ValidationError(f"{path}.reason_codes must be unique and sorted")
        if (
            result.eligibility == ResumeMaterialEligibility.STRUCTURAL_ONLY
            and result.section not in {ResumeSectionType.SKILLS, ResumeSectionType.EDUCATION}
        ):
            raise Phase2ValidationError(
                f"{path} structural material is only allowed in Skills or Education"
            )
        if (
            result.material_type == ResumeMaterialType.PROFILE_EVIDENCE
            and result.enrichment_claim_ids
        ):
            raise Phase2ValidationError(f"{path} Profile evidence cannot reference Claims")
        if (
            result.material_type == ResumeMaterialType.ENRICHMENT_CLAIM
            and len(result.enrichment_claim_ids) != 1
        ):
            raise Phase2ValidationError(f"{path} Enrichment material must reference one Claim")
        if result.material_id != generate_resume_material_id(result):
            raise Phase2ValidationError(f"{path}.material_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "material_id": self.material_id,
            "material_type": self.material_type.value,
            "section": self.section.value,
            "eligibility": self.eligibility.value,
            "reason_codes": [item.value for item in self.reason_codes],
            "source_record_id": self.source_record_id,
            "evidence_item_ids": list(self.evidence_item_ids),
            "enrichment_claim_ids": list(self.enrichment_claim_ids),
            "exact_text": self.exact_text,
        }


@dataclass(frozen=True, eq=True)
class ResumeCoverageIssue:
    issue_id: str
    reason: ResumeCoverageReason
    category: ResumeCoverageCategory
    profile_path: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> ResumeCoverageIssue:
        data = _mapping(value, path)
        _reject_unknown(data, {"issue_id", "reason", "category", "profile_path"}, path)
        result = cls(
            issue_id=_stable_id(data.get("issue_id"), f"{path}.issue_id"),
            reason=_enum(data.get("reason"), ResumeCoverageReason, f"{path}.reason"),
            category=_enum(data.get("category"), ResumeCoverageCategory, f"{path}.category"),
            profile_path=_text(data.get("profile_path"), f"{path}.profile_path"),
        )
        if result.issue_id != generate_coverage_issue_id(result):
            raise Phase2ValidationError(f"{path}.issue_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, str]:
        return {
            "issue_id": self.issue_id,
            "reason": self.reason.value,
            "category": self.category.value,
            "profile_path": self.profile_path,
        }


@dataclass(frozen=True, eq=True)
class ResumeCoverageReport:
    profile_education_count: int
    covered_education_count: int
    profile_experience_count: int
    covered_experience_count: int
    profile_skill_count: int
    covered_skill_count: int
    available_contact_fields: tuple[str, ...]
    missing_contact_fields: tuple[str, ...]
    issues: tuple[ResumeCoverageIssue, ...]
    partial_coverage: bool

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> ResumeCoverageReport:
        data = _mapping(value, path)
        allowed = {
            "profile_education_count", "covered_education_count",
            "profile_experience_count", "covered_experience_count",
            "profile_skill_count", "covered_skill_count", "available_contact_fields",
            "missing_contact_fields", "issues", "partial_coverage",
        }
        _reject_unknown(data, allowed, path)
        raw_issues = data.get("issues")
        if not isinstance(raw_issues, list):
            raise Phase2ValidationError(f"{path}.issues must be a list")
        partial = data.get("partial_coverage")
        if not isinstance(partial, bool):
            raise Phase2ValidationError(f"{path}.partial_coverage must be boolean")
        result = cls(
            profile_education_count=_count(
                data.get("profile_education_count"), f"{path}.profile_education_count"
            ),
            covered_education_count=_count(
                data.get("covered_education_count"), f"{path}.covered_education_count"
            ),
            profile_experience_count=_count(
                data.get("profile_experience_count"), f"{path}.profile_experience_count"
            ),
            covered_experience_count=_count(
                data.get("covered_experience_count"), f"{path}.covered_experience_count"
            ),
            profile_skill_count=_count(
                data.get("profile_skill_count"), f"{path}.profile_skill_count"
            ),
            covered_skill_count=_count(
                data.get("covered_skill_count"), f"{path}.covered_skill_count"
            ),
            available_contact_fields=_string_tuple(
                data.get("available_contact_fields"), f"{path}.available_contact_fields"
            ),
            missing_contact_fields=_string_tuple(
                data.get("missing_contact_fields"), f"{path}.missing_contact_fields"
            ),
            issues=tuple(
                ResumeCoverageIssue.from_dict(item, f"{path}.issues[{index}]")
                for index, item in enumerate(raw_issues)
            ),
            partial_coverage=partial,
        )
        for name in ("available_contact_fields", "missing_contact_fields"):
            values = getattr(result, name)
            if values != tuple(sorted(set(values))):
                raise Phase2ValidationError(f"{path}.{name} must be unique and sorted")
        issue_ids = tuple(item.issue_id for item in result.issues)
        if issue_ids != tuple(sorted(set(issue_ids))):
            raise Phase2ValidationError(f"{path}.issues must be unique and sorted")
        if result.partial_coverage != bool(result.issues):
            raise Phase2ValidationError(f"{path}.partial_coverage is inconsistent")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_education_count": self.profile_education_count,
            "covered_education_count": self.covered_education_count,
            "profile_experience_count": self.profile_experience_count,
            "covered_experience_count": self.covered_experience_count,
            "profile_skill_count": self.profile_skill_count,
            "covered_skill_count": self.covered_skill_count,
            "available_contact_fields": list(self.available_contact_fields),
            "missing_contact_fields": list(self.missing_contact_fields),
            "issues": [item.to_dict() for item in self.issues],
            "partial_coverage": self.partial_coverage,
        }


@dataclass(frozen=True, eq=True)
class ResumeMaterialSummary:
    material_count: int
    source_count: int
    structural_count: int
    factual_context_count: int
    achievement_component_count: int

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> ResumeMaterialSummary:
        data = _mapping(value, path)
        names = set(cls.__dataclass_fields__)
        _reject_unknown(data, names, path)
        return cls(**{name: _count(data.get(name), f"{path}.{name}") for name in names})

    def to_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, eq=True)
class ResumeMaterialArtifact:
    resume_material_id: str
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
    supersedes_resume_material_id: str | None
    created_at: str
    updated_at: str
    materials: tuple[ResumeMaterial, ...]
    coverage: ResumeCoverageReport
    summary: ResumeMaterialSummary
    schema: str = RESUME_MATERIAL_SCHEMA
    schema_version: int = RESUME_MATERIAL_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ResumeMaterialArtifact:
        data = _mapping(value, "resume_material")
        allowed = {
            "schema", "schema_version", "resume_material_id", "profile_fingerprint",
            "mapping_artifact_id", "mapping_schema_version",
            "binding_review_artifact_id", "binding_review_schema_version",
            "allocation_review_artifact_id", "allocation_review_schema_version",
            "recommendation_set_id", "recommendation_schema_version", "decision_id",
            "decision_schema_version", "gap_analysis_id", "gap_analysis_schema_version",
            "evidence_bank_id", "evidence_bank_schema_version", "enrichment_artifact_id",
            "enrichment_schema_version", "rubric_version", "rubric_fingerprint",
            "rubric_schema_version", "catalog_version", "catalog_schema_version",
            "supersedes_resume_material_id",
            "created_at", "updated_at", "materials", "coverage", "summary",
        }
        _reject_unknown(data, allowed, "resume_material")
        if data.get("schema") != RESUME_MATERIAL_SCHEMA or data.get("schema_version") != 1:
            raise Phase2ValidationError("unsupported Resume Material schema")
        if data.get("recommendation_schema_version") != 7:
            raise Phase2ValidationError("Resume Material requires Recommendation schema 7")
        raw_materials = data.get("materials")
        if not isinstance(raw_materials, list):
            raise Phase2ValidationError("resume_material.materials must be a list")
        result = cls(
            resume_material_id=_stable_id(
                data.get("resume_material_id"), "resume_material.resume_material_id"
            ),
            profile_fingerprint=_fingerprint(
                data.get("profile_fingerprint"), "resume_material.profile_fingerprint"
            ),
            mapping_artifact_id=_stable_id(
                data.get("mapping_artifact_id"), "resume_material.mapping_artifact_id"
            ),
            mapping_schema_version=_schema_version(
                data.get("mapping_schema_version"),
                "resume_material.mapping_schema_version", 5,
            ),
            binding_review_artifact_id=_optional_id(
                data.get("binding_review_artifact_id"),
                "resume_material.binding_review_artifact_id",
            ),
            binding_review_schema_version=_optional_schema_version(
                data.get("binding_review_schema_version"),
                "resume_material.binding_review_schema_version",
            ),
            allocation_review_artifact_id=_optional_id(
                data.get("allocation_review_artifact_id"),
                "resume_material.allocation_review_artifact_id",
            ),
            allocation_review_schema_version=_optional_schema_version(
                data.get("allocation_review_schema_version"),
                "resume_material.allocation_review_schema_version",
            ),
            recommendation_set_id=_stable_id(
                data.get("recommendation_set_id"), "resume_material.recommendation_set_id"
            ),
            recommendation_schema_version=7,
            decision_id=_stable_id(data.get("decision_id"), "resume_material.decision_id"),
            decision_schema_version=_schema_version(
                data.get("decision_schema_version"),
                "resume_material.decision_schema_version", 2,
            ),
            gap_analysis_id=_stable_id(
                data.get("gap_analysis_id"), "resume_material.gap_analysis_id"
            ),
            gap_analysis_schema_version=_schema_version(
                data.get("gap_analysis_schema_version"),
                "resume_material.gap_analysis_schema_version", 2,
            ),
            evidence_bank_id=_stable_id(
                data.get("evidence_bank_id"), "resume_material.evidence_bank_id"
            ),
            evidence_bank_schema_version=_schema_version(
                data.get("evidence_bank_schema_version"),
                "resume_material.evidence_bank_schema_version", 1,
            ),
            enrichment_artifact_id=_stable_id(
                data.get("enrichment_artifact_id"),
                "resume_material.enrichment_artifact_id",
            ),
            enrichment_schema_version=_schema_version(
                data.get("enrichment_schema_version"),
                "resume_material.enrichment_schema_version", 1,
            ),
            rubric_version=_version(
                data.get("rubric_version"), "resume_material.rubric_version"
            ),
            rubric_fingerprint=_fingerprint(
                data.get("rubric_fingerprint"), "resume_material.rubric_fingerprint"
            ),
            rubric_schema_version=_schema_version(
                data.get("rubric_schema_version"),
                "resume_material.rubric_schema_version", 2,
            ),
            catalog_version=_version(
                data.get("catalog_version"), "resume_material.catalog_version"
            ),
            catalog_schema_version=_schema_version(
                data.get("catalog_schema_version"),
                "resume_material.catalog_schema_version", 1,
            ),
            supersedes_resume_material_id=_optional_id(
                data.get("supersedes_resume_material_id"),
                "resume_material.supersedes_resume_material_id",
            ),
            created_at=_iso_datetime(data.get("created_at"), "resume_material.created_at"),
            updated_at=_iso_datetime(data.get("updated_at"), "resume_material.updated_at"),
            materials=tuple(
                ResumeMaterial.from_dict(item, f"resume_material.materials[{index}]")
                for index, item in enumerate(raw_materials)
            ),
            coverage=ResumeCoverageReport.from_dict(
                data.get("coverage"), "resume_material.coverage"
            ),
            summary=ResumeMaterialSummary.from_dict(
                data.get("summary"), "resume_material.summary"
            ),
        )
        result._validate_shape()
        if result.resume_material_id != generate_resume_material_artifact_id(result):
            raise Phase2ValidationError("Resume Material artifact ID is not deterministic")
        return result

    def _validate_shape(self) -> None:
        if _parsed_datetime(self.updated_at) < _parsed_datetime(self.created_at):
            raise Phase2ValidationError("Resume Material updated_at cannot precede created_at")
        if (self.binding_review_artifact_id is None) != (
            self.binding_review_schema_version is None
        ):
            raise Phase2ValidationError(
                "Resume Material Binding Review ID and schema version must appear together"
            )
        if (self.allocation_review_artifact_id is None) != (
            self.allocation_review_schema_version is None
        ):
            raise Phase2ValidationError(
                "Resume Material Allocation Review ID and schema version must appear together"
            )
        ids = tuple(item.material_id for item in self.materials)
        if ids != tuple(sorted(set(ids))):
            raise Phase2ValidationError("Resume Material entries must be unique and sorted")
        if self.summary != _summary(self.materials):
            raise Phase2ValidationError("Resume Material summary is not deterministic")

    def validate(
        self, *, profile: CareerProfile, rubric: CapabilityRubric, catalog: RoleCatalog,
        mapping: ProfileDimensionMappingCandidateSet,
        recommendation: RoleRecommendationArtifact, decision: UserRoleDecision,
        gap_analysis: CareerGapAnalysis, evidence_bank: EvidenceBank,
        evidence_enrichment: EvidenceEnrichmentArtifact,
        evidence_reviews: EvidenceBindingReviewArtifact | None = None,
        allocation_reviews: EvidenceGroupAllocationReviewArtifact | None = None,
        superseded_resume_material: ResumeMaterialArtifact | None = None,
        superseded_decision: UserRoleDecision | None = None,
        superseded_enrichment: EvidenceEnrichmentArtifact | None = None,
    ) -> None:
        _validate_contents(
            self,
            profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
            recommendation=recommendation, decision=decision, gap_analysis=gap_analysis,
            evidence_bank=evidence_bank, evidence_enrichment=evidence_enrichment,
            evidence_reviews=evidence_reviews, allocation_reviews=allocation_reviews,
            superseded_decision=superseded_decision,
            superseded_enrichment=superseded_enrichment,
        )
        _validate_revision(self, superseded_resume_material)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "resume_material_id": self.resume_material_id,
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
            "supersedes_resume_material_id": self.supersedes_resume_material_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "materials": [item.to_dict() for item in self.materials],
            "coverage": self.coverage.to_dict(),
            "summary": self.summary.to_dict(),
        }


def generate_resume_material_id(value: ResumeMaterial) -> str:
    data = value.to_dict()
    data.pop("material_id")
    return _stable_hash("resume_material", data)


def generate_coverage_issue_id(value: ResumeCoverageIssue) -> str:
    return _stable_hash(
        "resume_coverage_issue",
        {
            "reason": value.reason.value,
            "category": value.category.value,
            "profile_path": value.profile_path,
        },
    )


def generate_resume_material_artifact_id(value: ResumeMaterialArtifact) -> str:
    data = value.to_dict()
    data.pop("resume_material_id")
    return _stable_hash(RESUME_MATERIAL_ID_VERSION, data)


def _material(
    *, material_type: ResumeMaterialType, section: ResumeSectionType,
    eligibility: ResumeMaterialEligibility, reasons: Sequence[ResumeMaterialReason],
    source_record_id: str, evidence_item_ids: Sequence[str],
    enrichment_claim_ids: Sequence[str], exact_text: str,
) -> ResumeMaterial:
    provisional = ResumeMaterial(
        material_id="resume_material_pending",
        material_type=material_type,
        section=section,
        eligibility=eligibility,
        reason_codes=tuple(sorted(set(reasons), key=lambda item: item.value)),
        source_record_id=source_record_id,
        evidence_item_ids=tuple(sorted(set(evidence_item_ids))),
        enrichment_claim_ids=tuple(sorted(set(enrichment_claim_ids))),
        exact_text=exact_text,
    )
    return ResumeMaterial.from_dict(
        {**provisional.to_dict(), "material_id": generate_resume_material_id(provisional)},
        "resume_material",
    )


def _section(source: EvidenceSourceRecord) -> ResumeSectionType:
    if source.record_type == EvidenceSourceRecordType.SKILL:
        return ResumeSectionType.SKILLS
    if source.record_type == EvidenceSourceRecordType.EDUCATION:
        return ResumeSectionType.EDUCATION
    if source.record_type == EvidenceSourceRecordType.PROJECT:
        return ResumeSectionType.PROJECTS
    identity = dict(source.identity)
    if identity.get("experience_type") in {"project", "personal_project"}:
        return ResumeSectionType.PROJECTS
    return ResumeSectionType.EXPERIENCE


def _issue(
    reason: ResumeCoverageReason, category: ResumeCoverageCategory, profile_path: str,
) -> ResumeCoverageIssue:
    provisional = ResumeCoverageIssue(
        issue_id="resume_coverage_pending", reason=reason,
        category=category, profile_path=profile_path,
    )
    return ResumeCoverageIssue.from_dict(
        {**provisional.to_dict(), "issue_id": generate_coverage_issue_id(provisional)},
        "resume_coverage_issue",
    )


def _coverage(
    profile: CareerProfile, evidence_bank: EvidenceBank,
    materials: Sequence[ResumeMaterial],
) -> ResumeCoverageReport:
    source_paths = {item.canonical_profile_path for item in evidence_bank.sources}
    material_source_ids = {item.source_record_id for item in materials}
    issues: list[ResumeCoverageIssue] = []
    contact_values = {"name": profile.basic_profile.name}
    available_contact: tuple[str, ...] = ()
    missing_contact = tuple(
        sorted(name for name in _CONTACT_FIELDS if not contact_values.get(name))
    )
    for name in _CONTACT_FIELDS:
        issues.append(
            _issue(
                (
                    ResumeCoverageReason.UNAVAILABLE_VERIFIED_MATERIAL
                    if contact_values.get(name)
                    else ResumeCoverageReason.MISSING_CONTACT_FIELD
                ),
                ResumeCoverageCategory.CONTACT,
                f"contact.{name}",
            )
        )
    record_groups = (
        ("education", profile.education, ResumeCoverageCategory.EDUCATION),
        ("experience_overview", profile.experience_overview, None),
        ("skills", profile.skills, ResumeCoverageCategory.SKILL),
    )
    for prefix, records, fixed_category in record_groups:
        for index, record in enumerate(records):
            path = f"{prefix}[{index}]"
            category = fixed_category
            if category is None:
                category = (
                    ResumeCoverageCategory.PROJECT
                    if record.experience_type in {"project", "personal_project"}
                    else ResumeCoverageCategory.EXPERIENCE
                )
            if path not in source_paths:
                issues.append(
                    _issue(
                        ResumeCoverageReason.UNCOVERED_PROFILE_RECORD, category, path
                    )
                )
                continue
            source_ids = {
                source.source_record_id
                for source in evidence_bank.sources
                if source.canonical_profile_path == path
            }
            if not source_ids.intersection(material_source_ids):
                issues.append(
                    _issue(
                        ResumeCoverageReason.UNAVAILABLE_VERIFIED_MATERIAL, category, path
                    )
                )
    covered_education = sum(
        f"education[{index}]" in source_paths for index in range(len(profile.education))
    )
    covered_experience = sum(
        f"experience_overview[{index}]" in source_paths
        for index in range(len(profile.experience_overview))
    )
    covered_skills = sum(
        f"skills[{index}]" in source_paths for index in range(len(profile.skills))
    )
    ordered_issues = tuple(sorted(issues, key=lambda item: item.issue_id))
    return ResumeCoverageReport(
        profile_education_count=len(profile.education),
        covered_education_count=covered_education,
        profile_experience_count=len(profile.experience_overview),
        covered_experience_count=covered_experience,
        profile_skill_count=len(profile.skills),
        covered_skill_count=covered_skills,
        available_contact_fields=available_contact,
        missing_contact_fields=missing_contact,
        issues=ordered_issues,
        partial_coverage=bool(ordered_issues),
    )


def _summary(materials: Sequence[ResumeMaterial]) -> ResumeMaterialSummary:
    return ResumeMaterialSummary(
        material_count=len(materials),
        source_count=len({item.source_record_id for item in materials}),
        structural_count=sum(
            item.eligibility == ResumeMaterialEligibility.STRUCTURAL_ONLY
            for item in materials
        ),
        factual_context_count=sum(
            item.eligibility == ResumeMaterialEligibility.FACTUAL_CONTEXT
            for item in materials
        ),
        achievement_component_count=sum(
            item.eligibility == ResumeMaterialEligibility.ACHIEVEMENT_COMPONENT
            for item in materials
        ),
    )


def _materials(
    evidence_bank: EvidenceBank, evidence_enrichment: EvidenceEnrichmentArtifact,
    *, context: Mapping[str, Any],
) -> tuple[ResumeMaterial, ...]:
    source_by_id = {item.source_record_id: item for item in evidence_bank.sources}
    item_by_id = {item.evidence_item_id: item for item in evidence_bank.items}
    claims_by_id = {item.claim_id: item for item in evidence_enrichment.claims}
    result: list[ResumeMaterial] = []
    for item in evidence_bank.items:
        if item.resume_use == ResumeEvidenceUse.NOT_ELIGIBLE:
            continue
        source = source_by_id[item.source_record_id]
        if item.resume_use == ResumeEvidenceUse.STRUCTURAL_INFORMATION:
            eligibility = ResumeMaterialEligibility.STRUCTURAL_ONLY
            reasons = (ResumeMaterialReason.STRUCTURAL_PROFILE_FACT,)
        elif item.resume_use == ResumeEvidenceUse.CONFIRMED_CAPABILITY_EVIDENCE:
            eligibility = ResumeMaterialEligibility.FACTUAL_CONTEXT
            reasons = (ResumeMaterialReason.CONFIRMED_CAPABILITY_EVIDENCE,)
        else:
            eligibility = ResumeMaterialEligibility.FACTUAL_CONTEXT
            reasons = (ResumeMaterialReason.CONFIRMED_PROFILE_FACT,)
        result.append(
            _material(
                material_type=ResumeMaterialType.PROFILE_EVIDENCE,
                section=_section(source), eligibility=eligibility, reasons=reasons,
                source_record_id=source.source_record_id,
                evidence_item_ids=(item.evidence_item_id,), enrichment_claim_ids=(),
                exact_text=item.exact_excerpt,
            )
        )
    selected_claims = select_resume_enrichment_materials(
        evidence_enrichment, **context
    )
    for selected in selected_claims:
        claim = claims_by_id[selected.claim_id]
        source = source_by_id[selected.source_record_id]
        if selected.use == ResumeEnrichmentUse.STRUCTURAL_INFORMATION:
            eligibility = ResumeMaterialEligibility.STRUCTURAL_ONLY
        elif selected.use == ResumeEnrichmentUse.ACHIEVEMENT_DETAIL:
            eligibility = ResumeMaterialEligibility.ACHIEVEMENT_COMPONENT
        else:
            eligibility = ResumeMaterialEligibility.FACTUAL_CONTEXT
        result.append(
            _material(
                material_type=ResumeMaterialType.ENRICHMENT_CLAIM,
                section=_section(source), eligibility=eligibility,
                reasons=(ResumeMaterialReason.CONFIRMED_ENRICHMENT_CLAIM,),
                source_record_id=source.source_record_id,
                evidence_item_ids=selected.evidence_item_ids,
                enrichment_claim_ids=(selected.claim_id,),
                exact_text=claim.user_supplied_statement,
            )
        )
    return tuple(sorted(result, key=lambda item: item.material_id))


def _context(
    *, profile: CareerProfile, rubric: CapabilityRubric, catalog: RoleCatalog,
    mapping: ProfileDimensionMappingCandidateSet,
    recommendation: RoleRecommendationArtifact, decision: UserRoleDecision,
    gap_analysis: CareerGapAnalysis, evidence_bank: EvidenceBank,
    evidence_enrichment: EvidenceEnrichmentArtifact,
    evidence_reviews: EvidenceBindingReviewArtifact | None,
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None,
    superseded_decision: UserRoleDecision | None,
    superseded_enrichment: EvidenceEnrichmentArtifact | None,
) -> dict[str, Any]:
    evidence_bank.validate(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision, gap_analysis=gap_analysis,
        evidence_reviews=evidence_reviews, allocation_reviews=allocation_reviews,
        superseded_decision=superseded_decision,
    )
    enrichment_context = dict(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision, gap_analysis=gap_analysis,
        evidence_bank=evidence_bank, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews, superseded_decision=superseded_decision,
        superseded_enrichment=superseded_enrichment,
    )
    evidence_enrichment.validate(**enrichment_context)
    return enrichment_context


def _build_resume_material(
    *, profile: CareerProfile, rubric: CapabilityRubric, catalog: RoleCatalog,
    mapping: ProfileDimensionMappingCandidateSet,
    recommendation: RoleRecommendationArtifact, decision: UserRoleDecision,
    gap_analysis: CareerGapAnalysis, evidence_bank: EvidenceBank,
    evidence_enrichment: EvidenceEnrichmentArtifact, created_at: str,
    updated_at: str, supersedes_resume_material_id: str | None,
    evidence_reviews: EvidenceBindingReviewArtifact | None = None,
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None = None,
    superseded_decision: UserRoleDecision | None = None,
    superseded_enrichment: EvidenceEnrichmentArtifact | None = None,
) -> ResumeMaterialArtifact:
    context = _context(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision, gap_analysis=gap_analysis,
        evidence_bank=evidence_bank, evidence_enrichment=evidence_enrichment,
        evidence_reviews=evidence_reviews, allocation_reviews=allocation_reviews,
        superseded_decision=superseded_decision,
        superseded_enrichment=superseded_enrichment,
    )
    materials = _materials(evidence_bank, evidence_enrichment, context=context)
    provisional = ResumeMaterialArtifact(
        resume_material_id="resume_material_artifact_pending",
        profile_fingerprint=profile_fingerprint(profile),
        mapping_artifact_id=evidence_bank.mapping_artifact_id,
        mapping_schema_version=5,
        binding_review_artifact_id=evidence_bank.binding_review_artifact_id,
        binding_review_schema_version=(None if evidence_reviews is None else 1),
        allocation_review_artifact_id=evidence_bank.allocation_review_artifact_id,
        allocation_review_schema_version=(None if allocation_reviews is None else 1),
        recommendation_set_id=evidence_bank.recommendation_set_id,
        recommendation_schema_version=7,
        decision_id=evidence_bank.decision_id,
        decision_schema_version=2,
        gap_analysis_id=evidence_bank.gap_analysis_id,
        gap_analysis_schema_version=2,
        evidence_bank_id=evidence_bank.evidence_bank_id,
        evidence_bank_schema_version=1,
        enrichment_artifact_id=evidence_enrichment.enrichment_artifact_id,
        enrichment_schema_version=1,
        rubric_version=rubric.rubric_version,
        rubric_fingerprint=capability_rubric_fingerprint(rubric),
        rubric_schema_version=2,
        catalog_version=catalog.catalog_version,
        catalog_schema_version=1,
        supersedes_resume_material_id=supersedes_resume_material_id,
        created_at=_iso_datetime(created_at, "resume_material.created_at"),
        updated_at=_iso_datetime(updated_at, "resume_material.updated_at"),
        materials=materials,
        coverage=_coverage(profile, evidence_bank, materials),
        summary=_summary(materials),
    )
    return ResumeMaterialArtifact.from_dict(
        {
            **provisional.to_dict(),
            "resume_material_id": generate_resume_material_artifact_id(provisional),
        }
    )


def _validate_revision(
    value: ResumeMaterialArtifact,
    superseded: ResumeMaterialArtifact | None,
) -> None:
    if value.supersedes_resume_material_id is None:
        if superseded is not None:
            raise Phase2ValidationError("Resume Material revision source was not referenced")
        return
    if superseded is None:
        raise Phase2ValidationError("Resume Material revision requires its superseded artifact")
    if value.supersedes_resume_material_id != superseded.resume_material_id:
        raise Phase2ValidationError("Resume Material revision references the wrong artifact")
    if _parsed_datetime(value.created_at) < _parsed_datetime(superseded.created_at):
        raise Phase2ValidationError("Resume Material revision cannot precede its source")
    provenance_names = (
        "profile_fingerprint", "mapping_artifact_id", "mapping_schema_version",
        "binding_review_artifact_id", "binding_review_schema_version",
        "allocation_review_artifact_id", "allocation_review_schema_version",
        "recommendation_set_id", "recommendation_schema_version", "decision_id",
        "decision_schema_version", "gap_analysis_id", "gap_analysis_schema_version",
        "evidence_bank_id", "evidence_bank_schema_version", "enrichment_artifact_id",
        "enrichment_schema_version", "rubric_version", "rubric_fingerprint",
        "rubric_schema_version", "catalog_version", "catalog_schema_version",
    )
    if any(getattr(value, name) != getattr(superseded, name) for name in provenance_names):
        raise Phase2ValidationError("Resume Material revision provenance is stale")


def _validate_contents(
    value: ResumeMaterialArtifact, *, profile: CareerProfile,
    rubric: CapabilityRubric, catalog: RoleCatalog,
    mapping: ProfileDimensionMappingCandidateSet,
    recommendation: RoleRecommendationArtifact, decision: UserRoleDecision,
    gap_analysis: CareerGapAnalysis, evidence_bank: EvidenceBank,
    evidence_enrichment: EvidenceEnrichmentArtifact,
    evidence_reviews: EvidenceBindingReviewArtifact | None = None,
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None = None,
    superseded_decision: UserRoleDecision | None = None,
    superseded_enrichment: EvidenceEnrichmentArtifact | None = None,
) -> None:
    expected = _build_resume_material(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision, gap_analysis=gap_analysis,
        evidence_bank=evidence_bank, evidence_enrichment=evidence_enrichment,
        evidence_reviews=evidence_reviews, allocation_reviews=allocation_reviews,
        created_at=value.created_at, updated_at=value.updated_at,
        supersedes_resume_material_id=value.supersedes_resume_material_id,
        superseded_decision=superseded_decision,
        superseded_enrichment=superseded_enrichment,
    )
    if value != expected:
        raise Phase2ValidationError(
            "Resume Material does not match deterministic source artifacts"
        )


def build_resume_material(
    *, profile: CareerProfile, rubric: CapabilityRubric, catalog: RoleCatalog,
    mapping: ProfileDimensionMappingCandidateSet,
    recommendation: RoleRecommendationArtifact, decision: UserRoleDecision,
    gap_analysis: CareerGapAnalysis, evidence_bank: EvidenceBank,
    evidence_enrichment: EvidenceEnrichmentArtifact, created_at: str,
    evidence_reviews: EvidenceBindingReviewArtifact | None = None,
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None = None,
    superseded_resume_material: ResumeMaterialArtifact | None = None,
    superseded_decision: UserRoleDecision | None = None,
    superseded_enrichment: EvidenceEnrichmentArtifact | None = None,
) -> ResumeMaterialArtifact:
    value = _build_resume_material(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision, gap_analysis=gap_analysis,
        evidence_bank=evidence_bank, evidence_enrichment=evidence_enrichment,
        evidence_reviews=evidence_reviews, allocation_reviews=allocation_reviews,
        created_at=created_at, updated_at=created_at,
        supersedes_resume_material_id=(
            None if superseded_resume_material is None
            else superseded_resume_material.resume_material_id
        ),
        superseded_decision=superseded_decision,
        superseded_enrichment=superseded_enrichment,
    )
    _validate_revision(value, superseded_resume_material)
    return value


def save_resume_material(
    value: ResumeMaterialArtifact, path: str | Path, **context: Any,
) -> Path:
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"Resume Material artifacts are immutable: {target}")
    value.validate(**context)
    return save_phase2_json(ResumeMaterialArtifact.from_dict(value.to_dict()).to_dict(), target)


def load_resume_material(
    path: str | Path, **context: Any,
) -> ResumeMaterialArtifact:
    value = ResumeMaterialArtifact.from_dict(load_phase2_json(path))
    value.validate(**context)
    return value


def load_resume_material_revision_source(
    path: str | Path, **context: Any,
) -> ResumeMaterialArtifact:
    value = ResumeMaterialArtifact.from_dict(load_phase2_json(path))
    _validate_contents(value, **context)
    return value
